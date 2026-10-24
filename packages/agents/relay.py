"""Trusted credential relay. The clean SDK process never receives this credential."""

import asyncio
import hmac
import json
import os
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import psutil
from fastapi import FastAPI, Request
from fastapi.responses import Response

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def usage_observations(pending, observed, message_id, credential):
    packets = pending.replace(b"\r\n", b"\n").split(b"\n\n")
    for packet in packets[:-1]:
        for line in packet.decode().splitlines():
            if not line.startswith("data:") or line[5:].strip() in ("", "[DONE]"):
                continue
            value = json.loads(line[5:])
            if value.get("type") == "message_start":
                message_id = value.get("message", {}).get("id")
                usage = value.get("message", {}).get("usage", {})
            elif value.get("type") == "message_delta":
                usage = value.get("usage", {})
            else:
                continue
            for key in TOKEN_KEYS:
                if key in usage:
                    if type(usage[key]) is not int or usage[key] < 0:
                        raise ValueError("Invalid provider counter")
                    observed[key] = max(observed.get(key, 0), usage[key])
    return packets[-1], observed, sanitized(message_id, credential)


def sanitized(value, credential):
    if isinstance(value, str):
        return value.replace(credential, "[redacted]")
    if isinstance(value, dict):
        return {sanitized(key, credential): sanitized(item, credential) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitized(item, credential) for item in value]
    return value


def safe_content(content, content_type, credential):
    if "text/event-stream" in content_type:
        lines = []
        for line in content.decode().splitlines():
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                line = "data: " + json.dumps(sanitized(json.loads(line[5:]), credential))
            lines.append(line.replace(credential, "[redacted]"))
        return ("\n".join(lines) + "\n\n").encode()
    return json.dumps(sanitized(json.loads(content), credential)).encode()


def create_relay(
    upstream,
    *,
    credential,
    grant,
    model,
    deadline,
    owner_alive,
    clock=time.monotonic,
    observe=lambda attempt, usage, message_id: None,
):
    app = FastAPI()
    lock = asyncio.Lock()

    def unstarted(attempt, status=403):
        signature = hmac.digest(grant.encode(), attempt.encode(), "sha256").hex()
        return Response(
            json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "agent_not_forwarded",
                        "message": "This model attempt was not forwarded",
                    },
                }
            ),
            status_code=status,
            media_type="application/json",
            headers={"x-lw-not-forwarded": signature},
        )

    @app.post("/v1/messages")
    async def messages(request: Request):
        attempt = request.headers.get("x-lw-attempt", "")
        authentication = request.headers.get("x-api-key", "")
        if not authentication or not hmac.compare_digest(authentication.encode(), grant.encode()):
            # No proof is returned to a caller that does not know the relay grant.
            return Response(status_code=401)
        try:
            async with asyncio.timeout(max(0, deadline - clock())):
                body = await request.json()
        except (ValueError, TimeoutError):
            return unstarted(attempt, 422)
        if not isinstance(body, dict) or body.get("model") != model or not attempt:
            return unstarted(attempt, 422)
        async with lock:
            remaining = deadline - clock()
            if remaining <= 0 or not owner_alive():
                return unstarted(attempt)
            chunks, content_type, pending, observed, message_id = [], "application/json", b"", {}, None
            try:
                # No await separates the last owner/deadline check from starting the external HTTP attempt.
                async with asyncio.timeout(remaining):
                    async with upstream.stream(
                        "POST",
                        "/v1/messages",
                        json=body,
                        timeout=remaining,
                        headers={"x-api-key": credential, "anthropic-version": "2023-06-01"},
                    ) as result:
                        content_type = result.headers.get("content-type", "application/json")
                        async for chunk in result.aiter_bytes():
                            chunks.append(chunk)
                            if "text/event-stream" in content_type:
                                previous = dict(observed)
                                pending, observed, message_id = usage_observations(
                                    pending + chunk, observed, message_id, credential
                                )
                                if observed != previous:
                                    observe(attempt, dict(observed), message_id)
                content = safe_content(b"".join(chunks), content_type, credential)
                # Provider headers never acquire the relay's private not-forwarded proof.
                return Response(
                    content, status_code=result.status_code, media_type=content_type.split(";")[0]
                )
            except (httpx.HTTPError, ValueError, UnicodeError, TimeoutError):
                error = {
                    "type": "error",
                    "error": {
                        "type": "provider_unavailable",
                        "message": "Model provider response is unavailable",
                    },
                }
                if "text/event-stream" in content_type:
                    try:
                        # Keep complete usage observations; a truncated trailing packet is not an observation.
                        packets = b"".join(chunks).replace(b"\r\n", b"\n").split(b"\n\n")[:-1]
                        prefix = safe_content(b"\n\n".join(packets), content_type, credential)
                        return Response(
                            prefix + ("data: " + json.dumps(error) + "\n\n").encode(),
                            status_code=502,
                            media_type="text/event-stream",
                        )
                    except (ValueError, UnicodeError):
                        pass
                return Response(
                    json.dumps(error),
                    status_code=502,
                    media_type="application/json",
                )

    return app


async def main():
    import uvicorn

    owner_pid = int(os.environ["LW_RELAY_OWNER_PID"])
    owner_created = float(os.environ["LW_RELAY_OWNER_CREATED"])
    cancelled = Path(os.environ["LW_RELAY_CANCELLED"])

    def owner_alive():
        try:
            return not cancelled.exists() and psutil.Process(owner_pid).create_time() == owner_created
        except psutil.NoSuchProcess:
            return False

    async with httpx.AsyncClient(base_url=os.environ["LW_RELAY_UPSTREAM"], trust_env=False) as upstream:
        observations = Path(os.environ["LW_RELAY_OBSERVATIONS"]).open("x", encoding="utf-8", newline="\n")

        def observe(attempt, usage, message_id):
            observations.write(
                json.dumps({"attempt": attempt, "usage": usage, "messageId": message_id}) + "\n"
            )
            observations.flush()
            os.fsync(observations.fileno())

        app = create_relay(
            upstream,
            credential=os.environ["LW_RELAY_KEY"],
            grant=os.environ["LW_RELAY_GRANT"],
            model=os.environ["LW_RELAY_MODEL"],
            deadline=float(os.environ["LW_RELAY_DEADLINE"]),
            owner_alive=owner_alive,
            observe=observe,
        )
        ready = Path(os.environ["LW_RELAY_READY"])

        @asynccontextmanager
        async def lifespan(_app):
            temporary = ready.with_suffix(".tmp")
            temporary.write_text(json.dumps({"port": listener.getsockname()[1]}), encoding="utf-8")
            temporary.replace(ready)
            yield

        app.router.lifespan_context = lifespan
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        try:
            await uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False)).serve(
                sockets=[listener]
            )
        finally:
            listener.close()
            observations.close()


if __name__ == "__main__":
    asyncio.run(main())

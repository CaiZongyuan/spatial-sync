"""One HTTP client shared by the command line and the generic MCP tools."""

from __future__ import annotations

import json
import os
import tarfile
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import httpx

TERMINAL = {"succeeded", "rejected", "failed", "cancelled"}
INVOCATION_STATES = TERMINAL | {"accepted", "running", "pending_approval"}


def plan_fields(plan_id, step_id, revision):
    return {name: value for name, value in {"planId": plan_id, "stepId": step_id,
                                           "planRevision": revision}.items() if value is not None}


def condition_hint(invocation):
    error = invocation.get("error") or {}
    failed = error.get("details", {}).get("failed", [])
    return "\n".join(dict.fromkeys(item["message"] for item in failed if item.get("message")))


class GatewayError(Exception):
    def __init__(self, message, *, detail=None, status_code=None):
        super().__init__(message)
        self.detail, self.status_code = detail, status_code

    def document(self):
        return {"error": {"message": str(self), "statusCode": self.status_code, "detail": self.detail}}


class GatewayClient:
    def __init__(self, url=None, token=None, session=None, *, http=None):
        self.url = (url or os.getenv("LW_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = token if token is not None else os.getenv("LW_TOKEN")
        self._session_id = session or os.getenv("LW_SESSION")
        self._session_explicit = self._session_id is not None
        try:
            self.http = http or httpx.AsyncClient(base_url=self.url, timeout=30, trust_env=False)
        except httpx.InvalidURL as exc:
            raise GatewayError("Invalid LW_URL") from exc
        self._owns_http = http is None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self._owns_http:
            await self.http.aclose()

    async def request(self, method, path, *, actor="cli:lw", timeout=30, **kwargs):
        headers = kwargs.pop("headers", {}) | {"X-Actor": actor}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            response = await self.http.request(method, path, headers=headers, timeout=timeout, **kwargs)
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            # Exception URLs/messages can contain credentials. Keep diagnostics local and generic.
            raise GatewayError(f"Gateway connection failed ({type(exc).__name__})") from exc
        try:
            document = response.json()
        except ValueError as exc:
            raise GatewayError("Gateway returned invalid JSON", status_code=response.status_code) from exc
        if response.is_error and not (
            isinstance(document, dict)
            and document.get("invocationId")
            and document.get("status") in INVOCATION_STATES
        ):
            detail = document.get("detail", document) if isinstance(document, dict) else document
            raise GatewayError("Gateway request failed", detail=detail, status_code=response.status_code)
        return document

    async def route(self):
        if not self._session_explicit:
            health = await self.request("GET", "/api/v1/health")
            active = [
                item for item in health["sessions"] if item["state"] in ("RUNNING", "PAUSED", "LOADING")
            ]
            if len(active) != 1:
                raise GatewayError(f"Set LW_SESSION: expected one active session, found {len(active)}")
            self._session_id = active[0]["sessionId"]
        return "/api/v1/sessions/" + quote(self._session_id, safe="")

    async def status(self):
        return await self.request("GET", await self.route())

    async def catalog(self):
        return await self.request("GET", await self.route() + "/catalog")

    async def catalog_if_active(self):
        health = await self.request("GET", "/api/v1/health")
        if any(session["state"] in ("RUNNING", "PAUSED") for session in health["sessions"]):
            return await self.catalog()
        return None

    async def scene_search(self, query=None, launchable=False):
        return await self.library_search(query=query, launchable=launchable)

    async def asset_search(self, query=None, kind=None, gaps=False):
        return await self.library_search(query=query, kind=kind, gaps=gaps, resource="assets")

    async def library_search(self, query=None, launchable=False, resource="scenes", kind=None, gaps=False):
        if kind is not None or gaps:
            resource = "assets"
        if resource not in ("scenes", "assets"):
            raise ValueError("resource must be scenes or assets")
        if resource == "assets" and launchable:
            raise ValueError("launchable is only supported for scenes")
        params = {"q": query} if query is not None else {}
        if launchable:
            params["launchable"] = True
        if kind is not None:
            params["kind"] = kind
        if gaps:
            params["gaps"] = True
        return (await self.request("GET", "/api/v1/library/" + resource, params=params))[resource]

    async def library_describe(self, id):
        return await self.request("GET", self._library_path(id))

    @staticmethod
    def _library_path(id):
        name, separator, rev = id.partition("@")
        if "/" in name:
            path = "/api/v1/library/assets/" + "/".join(quote(part, safe="") for part in name.split("/"))
        else:
            path = "/api/v1/library/scenes/" + quote(name, safe="")
        if separator:
            path += "/" + quote(rev, safe="")
        return path

    async def library_validate(self, id, *, actor="cli:lw"):
        if "@" not in id:
            raise ValueError("Validation requires an exact asset or scene version with @rev")
        return await self.request("POST", self._library_path(id) + "/validate", actor=actor, timeout=240)

    async def library_import(self, directory, *, actor="cli:lw"):
        # The gateway independently validates paths, schema and upload/expansion limits.
        try:
            root = Path(directory).expanduser().resolve()
            if not root.is_dir():
                raise ValueError("Import directory does not exist on the client's filesystem")
            with tempfile.TemporaryFile() as output:
                with tarfile.open(fileobj=output, mode="w", dereference=True) as archive:
                    for path in sorted(root.rglob("*")):
                        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
                            raise ValueError("Library directory cannot contain filesystem links")
                        if path.is_file():
                            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
                        elif not path.is_dir():
                            raise ValueError("Library directory can only contain regular files")
                output.seek(0)

                # httpx's async transport requires an async iterator for streamed bodies.
                async def chunks():
                    while chunk := output.read(1024 * 1024):
                        yield chunk

                return await self.request(
                    "POST",
                    "/api/v1/library/imports",
                    content=chunks(),
                    headers={"Content-Type": "application/x-tar"},
                    actor=actor,
                    timeout=240,
                )
        except OSError as exc:
            raise ValueError("Library directory could not be read") from exc

    async def scene_launch(self, sceneVersion, source=None, seed=None, *, actor="cli:lw"):
        body = {"sceneVersion": sceneVersion}
        if source is not None:
            body["source"] = source
        if seed is not None:
            body["seed"] = seed
        result = await self.request("POST", "/api/v1/sessions", json=body, actor=actor, timeout=190)
        self._session_id, self._session_explicit = result["sessionId"], False
        return result

    async def alerts_list(self, active=False, state=None, severity=None):
        params = {"active": "true"} if active else {}
        if state is not None:
            params["state"] = state
        if severity is not None:
            params["severity"] = severity
        if self._session_explicit:
            params["sessionId"] = self._session_id
        return (await self.request("GET", "/api/v1/alerts", params=params))["alerts"]

    async def alert_ack(self, alertId, note="", *, actor="cli:lw"):
        return await self.request(
            "POST", "/api/v1/alerts/" + quote(alertId, safe="") + "/ack", json={"note": note}, actor=actor
        )

    async def list_entities(self, type=None, query=None):
        params = {key: value for key, value in {"type": type, "q": query}.items() if value is not None}
        return (await self.request("GET", await self.route() + "/entities", params=params))["entities"]

    async def describe_entity(self, key):
        return await self.request("GET", await self.route() + "/entity", params={"key": key})

    async def get_state(self, keys):
        return await self.request("GET", await self.route() + "/state", params={"keys": ",".join(keys)})

    async def invoke_action(self, key, action, params=None, *, wait=True, timeout=120, actor="cli:lw",
                            planId=None, stepId=None, planRevision=None):
        return await self.request(
            "POST",
            await self.route() + "/actions",
            actor=actor,
            timeout=timeout + 10,
            json={
                "requestId": uuid.uuid4().hex,
                "entityKey": key,
                "action": action,
                "params": params or {},
                "wait": wait,
                "timeoutSeconds": timeout,
                **plan_fields(planId, stepId, planRevision),
            },
        )

    async def get_invocation(self, invocation_id, *, wait=False, timeout=120):
        return await self.request(
            "GET",
            await self.route() + "/invocations/" + quote(invocation_id, safe=""),
            params={"wait": wait, "timeoutSeconds": timeout},
            timeout=timeout + 10,
        )

    async def cancel_invocation(self, invocation_id, *, actor="cli:lw"):
        return await self.request(
            "POST",
            await self.route() + "/invocations/" + quote(invocation_id, safe="") + "/cancel",
            actor=actor,
        )

    async def describe_approval(self, invocation_id):
        if invocation_id.startswith("approval-"):
            return await self.request("GET", "/api/v1/approvals/" + quote(invocation_id, safe=""))
        return await self.get_invocation(invocation_id)

    async def approve_invocation(self, invocation_id, *, deny=False, note=""):
        path = (
            "/api/v1/approvals/" + quote(invocation_id, safe="") + "/decision"
            if invocation_id.startswith("approval-")
            else await self.route() + "/invocations/" + quote(invocation_id, safe="") + "/approve"
        )
        return await self.request(
            "POST",
            path,
            json={"decision": "deny" if deny else "approve", "note": note},
        )

    async def wait_until(self, key, prop, op, value, *, timeout=120, actor="cli:lw",
                         planId=None, stepId=None, planRevision=None, by=None):
        return await self.request(
            "GET",
            await self.route() + "/wait",
            actor=actor,
            timeout=timeout + 10,
            params={
                "key": key,
                "property": prop,
                "op": op,
                "value": json.dumps(value, ensure_ascii=False, allow_nan=False),
                "timeoutSeconds": timeout,
                **({"by": by} if by is not None else {}),
                **plan_fields(planId, stepId, planRevision),
            },
        )

    async def check_condition(self, key, prop, op, value, *, actor="cli:lw",
                              planId=None, stepId=None, planRevision=None, by=None):
        return await self.request("POST", await self.route() + "/check", actor=actor, json={
            "key": key, "property": prop, "op": op, "value": value,
            **({"by": by} if by is not None else {}),
            **plan_fields(planId, stepId, planRevision),
        })

    async def plan_create(self, document, *, actor="cli:lw"):
        session_id = (await self.route()).rsplit("/", 1)[-1]
        return await self.request("POST", "/api/v1/plans", actor=actor,
                                  json={**document, "sessionId": session_id})

    async def plan_revise(self, id, document, *, actor="cli:lw"):
        session_id = (await self.route()).rsplit("/", 1)[-1]
        return await self.request("POST", "/api/v1/plans/" + quote(id, safe="") + "/revisions", actor=actor,
                                  json={**document, "sessionId": session_id})

    async def plan_show(self, id, revision=None):
        return await self.request("GET", "/api/v1/plans/" + quote(id, safe=""),
                                  params={"revision": revision} if revision is not None else {})

    async def plan_list(self, status=None):
        return (await self.request("GET", "/api/v1/plans",
                                   params={"status": status} if status is not None else {}))["plans"]

    async def session_control(self, action, *, count=1, actor="cli:lw"):
        return await self.request(
            "POST",
            await self.route() + "/commands",
            actor=actor,
            json={
                "requestId": uuid.uuid4().hex,
                "command": action,
                "args": {"count": count} if action == "step" else {},
            },
        )

    async def recordings(self):
        return await self.request("GET", "/api/v1/recordings")

    async def watch(self, key=None) -> AsyncIterator[dict]:
        path = await self.route() + "/events"
        initial = await self.request("GET", path, params={"after": 0, "timeoutSeconds": 0})
        cursor = initial["lastSeq"]
        while True:
            result = await self.request(
                "GET", path, params={"after": cursor, "timeoutSeconds": 30}, timeout=40
            )
            if result["gap"]:
                raise GatewayError("Event history gap; restart watch", detail={"after": cursor})
            cursor = result["lastSeq"]
            for item in result["events"]:
                message = item["message"]
                if message["type"] == "entity.observations":
                    changes = [change for change in message["changes"] if key is None or change["key"] == key]
                    if changes:
                        yield message | {"changes": changes}
                elif message["type"] in ("entity.event", "invocation.update"):
                    if key is None or message["entityKey"] == key:
                        yield message


def exit_code(document):
    if isinstance(document, dict):
        if document.get("schema") == "lw.validation/1" and any(
            check["status"] == "fail" for check in document["checks"]
        ):
            return 3
        if document.get("timedOut"):
            return 4
        if document.get("satisfied") is False:
            return 2
        status = document.get("status")
        if status == "rejected" or document.get("ack", {}).get("status") == "rejected":
            return 2
        if status in ("failed", "cancelled"):
            return 3
    return 0

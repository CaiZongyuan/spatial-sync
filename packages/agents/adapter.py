"""Pinned SDK implementation; model context bounds must have verified provenance."""

import asyncio
import json
import socket
from contextlib import asynccontextmanager, contextmanager

import uvicorn
from fastapi import FastAPI

from apps.node.process import CleanupIncomplete
from packages.agents.provider import install_provider_route
from packages.agents.runtime import AgentWorker, clean_environment, runtime_identity, sdk_configuration
from packages.agents.transport import CredentialRelay
from packages.info.plans import PlanError
from packages.vault import Vault


@asynccontextmanager
async def model_server(gate, *, registered=lambda handle: None):
    class Server(uvicorn.Server):
        @contextmanager
        def capture_signals(self):
            yield  # The owning gateway retains its own signal handlers.

    app = FastAPI()
    install_provider_route(app, gate)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    server = Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    handle = server, task, listener
    registered(handle)
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        await close_model_server(handle)


async def close_model_server(handle):
    server, task, listener = handle
    server.should_exit = True
    requests = tuple(server.server_state.tasks)
    for request in requests:
        request.cancel()
    try:
        _, pending = await asyncio.wait((task, *requests), timeout=5)
        if pending:
            raise CleanupIncomplete("Owned model listener cleanup is unconfirmed")
    finally:
        listener.close()


class SDKAdapter:
    def __init__(self, state_root, *, context_bounds=None, upstream_origin="https://api.anthropic.com"):
        self.vault, self.context_bounds, self.upstream_origin = (
            Vault(state_root),
            context_bounds or {},
            upstream_origin,
        )
        self._relays, self._workers, self._listeners, self._untracked = {}, {}, {}, set()

    def describe(self, model, thinking):
        identity = runtime_identity()
        bound = self.context_bounds.get(model["modelId"])
        if model["provider"] != "anthropic" or bound is None:
            raise PlanError(
                "agent_bound_unavailable", "This model has no verified conservative context bound", status=422
            )
        if thinking not in {"default", "none", "low", "medium", "high", "xhigh", "max"}:
            raise PlanError("agent_thinking_unavailable", "This thinking mode is unavailable", status=422)
        return identity | {
            "contextTokens": bound["tokens"],
            "contextSource": bound["source"],
            "enforcement": {name: "hard" for name in ("requests", "tokens", "llmUsd", "wallSeconds")},
        }

    @asynccontextmanager
    async def open(self, record, deadline, directory):
        relay = CredentialRelay(
            directory / "private-transport",
            self.vault,
            record["plan"]["credentialRef"],
            record["model"].split("/", 1)[1],
            deadline=deadline,
            upstream_origin=self.upstream_origin,
        )
        relay.trusted_relay = True
        self._relays[record["jobId"]] = relay
        try:
            async with relay:
                yield relay
        except CleanupIncomplete:
            if relay.process is None:
                self._untracked.add(record["jobId"])
            raise
        finally:
            self.release_confirmed(record["jobId"])

    def release_confirmed(self, identity):
        workers = self._workers.get(identity, [])
        listeners = self._listeners.get(identity, [])
        relay = self._relays.get(identity)
        if (
            identity not in self._untracked
            and all(worker.process is not None and worker.process.cleanup.is_file() for worker in workers)
            and all(
                task.done() and not server.server_state.tasks and listener.fileno() == -1
                for server, task, listener in listeners
            )
            and (relay is None or relay.process is not None and relay.process.cleanup.is_file())
        ):
            self._workers.pop(identity, None)
            self._listeners.pop(identity, None)
            self._relays.pop(identity, None)
            return True
        return False

    async def confirm_cleanup(self, identity):
        if identity in self._untracked:
            return False
        try:
            for worker in self._workers.get(identity, []):
                if worker.process is not None:
                    await asyncio.to_thread(worker.process.close)
            for handle in self._listeners.get(identity, []):
                await close_model_server(handle)
            relay = self._relays.get(identity)
            if relay is not None:
                await relay.close()
        except Exception:
            return False
        return self.release_confirmed(identity)

    async def case(self, task, gateway, gate):
        configuration = sdk_configuration(
            task["harness"],
            model=task["model"],
            thinking=task["thinking"],
            statement=task["statement"],
            requests=task["limits"]["requests"],
            max_output_tokens=task["limits"]["max_output_tokens"],
        )
        identity = task["runId"]
        async with model_server(
            gate, registered=lambda handle: self._listeners.setdefault(identity, []).append(handle)
        ) as provider_url:
            environment = clean_environment(
                task["directory"] / "private-environment",
                gateway_url=task["gatewayUrl"],
                session_id=task["sessionId"],
                run_id=task["runId"],
                tool_token=task["grant"],
                provider_url=provider_url,
                provider_token=gate._transport_token,
            )
            worker = AgentWorker(
                task["directory"] / "worker",
                configuration,
                environment,
                files=task["harness"].files,
                deadline=task["deadline"],
                on_cleanup=task["onCleanup"],
            )
            self._workers.setdefault(identity, []).append(worker)
            try:
                receipt = await worker.run()
            except CleanupIncomplete:
                if worker.process is None:
                    self._untracked.add(identity)
                raise
        messages = [json.loads(line) for line in receipt["trace"].read_text(encoding="utf-8").splitlines()]
        results = [
            message["data"]["value"]
            for message in messages
            if message["kind"] == "message" and message["data"].get("type") == "ResultMessage"
        ]
        if receipt["code"] or not results or results[-1].get("is_error"):
            raise PlanError("agent_sdk_failed", "The SDK case did not complete", status=503)

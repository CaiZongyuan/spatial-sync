"""Own a credential relay handle. Credentials enter only its clean environment."""

import asyncio
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

from apps.node.process import OwnedProcess
from packages.agents.runtime import clean_process_environment
from packages.info.plans import PlanError


class CredentialRelay:
    def __init__(
        self, root, vault, credential_ref, model, *, deadline, upstream_origin="https://api.anthropic.com"
    ):
        self.root, self.vault, self.credential_ref = Path(root).resolve(), vault, credential_ref
        self.model, self.deadline, self.upstream_origin = model, deadline, upstream_origin
        self.grant = secrets.token_urlsafe(32)
        self.process, self.client = None, None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self):
        if time.monotonic() >= self.deadline:
            raise PlanError("agent_run_inactive", "The Agent execution deadline expired", status=403)
        self.root.mkdir(parents=True, exist_ok=False)
        environment = clean_process_environment(self.root / "environment")
        owner = psutil.Process()
        environment.update(
            {
                "LW_RELAY_GRANT": self.grant,
                "LW_RELAY_MODEL": self.model,
                "LW_RELAY_DEADLINE": str(self.deadline),
                "LW_RELAY_UPSTREAM": self.upstream_origin,
                "LW_RELAY_CANCELLED": str(self.root / "cancelled"),
                "LW_RELAY_READY": str(self.root / "ready.json"),
                "LW_RELAY_OBSERVATIONS": str(self.root / "observations.jsonl"),
                "LW_RELAY_OWNER_PID": str(owner.pid),
                "LW_RELAY_OWNER_CREATED": str(owner.create_time()),
            }
        )
        environment.update(self.vault.adapter_environment({"LW_RELAY_KEY": self.credential_ref}))
        try:
            if time.monotonic() >= self.deadline:
                raise PlanError("agent_run_inactive", "The Agent execution deadline expired", status=403)
            self.process = OwnedProcess(
                [sys.executable, "-I", str(Path(__file__).with_name("relay.py"))],
                cwd=self.root,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            environment.pop("LW_RELAY_KEY", None)
        try:
            ready = self.root / "ready.json"
            while not ready.exists():
                if self.process.process.poll() is not None or time.monotonic() >= self.deadline:
                    raise PlanError(
                        "agent_transport_unavailable", "The owned model transport is unavailable", status=503
                    )
                await asyncio.sleep(0.02)
            port = json.loads(ready.read_bytes())["port"]
            if type(port) is not int or not 0 < port < 65536:
                raise ValueError("Invalid relay listener")
            self.client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False)
            return self.client
        except BaseException:
            await self.close()
            raise

    def revoke(self):
        if self.root.exists():
            (self.root / "cancelled").touch()

    def reconcile(self, gate):
        path = self.root / "observations.jsonl"
        if not path.exists():
            return
        for line in path.read_bytes().splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            observation = json.loads(line)
            gate.observe_prefix(observation["attempt"], observation["usage"], observation.get("messageId"))

    async def close(self):
        self.revoke()
        if self.client is not None:
            await self.client.aclose()
        if self.process is not None:
            cleanup = asyncio.create_task(asyncio.to_thread(self.process.close))
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError

"""One gateway-owned local TensorBoard process, loaded from the frozen Node runtime."""

import asyncio
import contextlib
import hashlib
import os
import socket
import time
import uuid

import httpx

from apps.node.process import OwnedProcess
from packages.info.plans import PlanError


class TensorBoardService:
    def __init__(self, manager):
        self.manager = manager
        self.root = manager.settings.state_root / "tensorboard"
        self.process = None
        self.pending = None
        self.url = None
        self.closed = False
        self.input_directories = None

    def log_directories(self):
        published = self.manager.library_assets.roots["user"] / "jobs"
        directories = {"published": str(published)}
        for job in self.manager.jobs.records.values():
            if job["status"] != "running" or job["nodeId"] != "local" or not job["leaseId"]:
                continue
            identity = hashlib.sha256((job["jobId"] + job["leaseId"]).encode()).hexdigest()[:24]
            directory = self.manager.jobs.node.root / "work" / identity / "logs"
            directories["live-" + job["jobId"]] = str(directory)
        return directories

    def command(self, port, prefix):
        trainer = self.manager.settings.node_trainer
        if not trainer:
            raise PlanError(
                "tensorboard_runtime_unavailable",
                "Configure the frozen local Node runtime before starting TensorBoard", status=409,
            )
        directories = self.input_directories
        return [
            trainer[0], "-m", "tensorboard.main", "--host", "127.0.0.1", "--port", str(port),
            "--path_prefix", prefix, "--logdir_spec",
            ",".join(f"{name}:{directory}" for name, directory in directories.items()),
        ]

    async def start(self):
        while True:
            if self.closed:
                raise PlanError("tensorboard_closed", "Gateway is shutting down", status=503)
            if self.pending is not None:
                await asyncio.shield(self.pending)
                continue  # A Job may have changed the input set during cold readiness.
            directories = self.log_directories()
            if (
                self.process is not None and self.process.process.poll() is None
                and self.input_directories == directories
            ):
                return {"url": self.url}
            # Task creation is atomic; no Session/Jobs lock spans launch/readiness.
            self.input_directories = directories
            self.pending = asyncio.create_task(self._launch(), name="tensorboard-launch")
            self.pending.add_done_callback(lambda finished: setattr(self, "pending", None))
            await asyncio.shield(self.pending)

    async def _launch(self):
        if self.process is not None:
            await asyncio.to_thread(self.process.close)
            self.process = None
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        prefix = "/lw-tensorboard-" + uuid.uuid4().hex
        command = self.command(port, prefix)
        self.root.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment["CUDA_VISIBLE_DEVICES"] = "-1"
        for name in ("SPATIAL_NODE_TOKEN", "LW_TOKEN", "LW_NODE_INPUTS", "LW_NODE_OUTPUT"):
            environment.pop(name, None)
        try:
            with (self.root / "server.log").open("ab") as log:
                self.process = await asyncio.to_thread(
                    OwnedProcess, command, cwd=self.root, env=environment, stdout=log, stderr=log
                )
            self.url = f"http://127.0.0.1:{port}{prefix}"
            deadline = time.monotonic() + 30
            async with httpx.AsyncClient(trust_env=False, timeout=0.5) as http:
                while time.monotonic() < deadline:
                    if self.process.process.poll() is not None:
                        raise RuntimeError("TensorBoard process exited before becoming ready")
                    try:
                        response = await http.get(self.url + "/data/environment")
                        if response.status_code == 200 and isinstance(response.json(), dict):
                            return {"url": self.url}
                    except (httpx.HTTPError, ValueError):
                        pass
                    await asyncio.sleep(0.1)
            raise RuntimeError("TensorBoard did not become ready within 30 seconds")
        except Exception as error:
            if self.process is not None:
                await asyncio.to_thread(self.process.close)
                self.process = None
            self.url = None
            raise PlanError("tensorboard_start_failed", str(error), status=503) from error

    async def close(self):
        self.closed = True
        if self.pending:
            with contextlib.suppress(Exception):
                await asyncio.shield(self.pending)
        if self.process:
            await asyncio.to_thread(self.process.close)
            self.process = None
        self.url = None

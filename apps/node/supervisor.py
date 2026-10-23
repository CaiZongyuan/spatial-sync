"""Start one local node; retain its owner across gateway reconnections."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import psutil

from packages.library.transactions import durable_json


class LocalNodeSupervisor:
    def __init__(self, jobs):
        self.jobs, self.settings = jobs, jobs.manager.settings
        self.root = self.settings.state_root / "local-node"
        self.process = None

    def _owner(self):
        file = self.root / "owner.json"
        if not file.exists():
            return None
        value = json.loads(file.read_text(encoding="utf-8"))
        try:
            process = psutil.Process(value["pid"])
            if process.create_time() == value["createdAt"] and process.cmdline() == value["command"]:
                return process
        except psutil.NoSuchProcess:
            return None
        return None

    def start(self):
        if not self.settings.node_trainer:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        if self._owner() is not None:
            return
        (self.root / "stop").unlink(missing_ok=True)
        command = [
            sys.executable,
            "-m",
            "apps.node.runner",
            "--url",
            self.settings.node_gateway_url,
            "--root",
            str(self.root),
            "--trainer",
            json.dumps(self.settings.node_trainer),
            "--interval",
            str(self.settings.node_interval),
            "--timeout",
            str(self.settings.node_job_timeout),
        ]
        if self.settings.node_capabilities:
            command.extend(["--capabilities", str(self.settings.node_capabilities)])
        with (self.root / "node.log").open("ab") as log:
            self.process = subprocess.Popen(
                command,
                stdout=log,
                stderr=log,
                env=os.environ | {"SPATIAL_NODE_TOKEN": self.jobs.node_token},
                cwd=str(Path(__file__).resolve().parents[2]),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        owner = psutil.Process(self.process.pid)
        durable_json(
            self.root / "owner.json",
            {"pid": owner.pid, "createdAt": owner.create_time(), "command": owner.cmdline()},
        )

    async def close(self):
        if any(node["leases"] for node in self.jobs.nodes.values()):
            return  # The node retains its process handles and re-reports after gateway restart.
        if self._owner() is None:
            return
        (self.root / "stop").write_text("idle gateway shutdown", encoding="utf-8")
        deadline = asyncio.get_running_loop().time() + self.settings.node_interval + 5
        while self._owner() is not None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        if self.process and self.process.poll() is not None:
            self.process.wait()
        elif self._owner() is not None:
            raise RuntimeError("Owned local node did not acknowledge idle shutdown")

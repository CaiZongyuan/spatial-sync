"""A local node downloads immutable inputs and owns its external training processes."""

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
import psutil

from apps.node.process import CleanupIncomplete, OwnedProcess, cleanup_confirmed
from apps.node.view_relay import relay_views
from packages.library.archive import extract_package, relative_path
from packages.library.common import content_hash
from packages.training.inputs import pack_package


def retained_rows(path):
    if not path.exists():
        return []
    rows, complete = [], 0
    for line in path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            with path.open("r+b") as stream:
                stream.truncate(complete)
            break
        rows.append(json.loads(line))
        complete += len(line)
    return rows


def trainer_arguments(inputs, callback):
    plan, job = inputs["plan"], inputs["job"]
    task = inputs["task"]
    hyper = plan["hyperparameters"]
    if plan["algorithm"].get("library") != "rsl_rl" or plan["algorithm"].get("name") != "PPO":
        raise ValueError("The local node currently supports rsl_rl PPO")
    known = {
        "num_envs",
        "max_iterations",
        "learning_rate",
        "device",
        "num_steps_per_env",
        "num_learning_epochs",
        "num_mini_batches",
        "save_interval",
    }
    if set(hyper) - known or set(plan["model"]) - {"actor", "critic", "activation"}:
        raise ValueError("Unsupported approved hyperparameter/model fields; no field may be silently ignored")
    command = [
        "train",
        "--rl_library",
        "rsl_rl",
        "--task",
        task["isaaclab"]["task_id"],
        "--num_envs",
        str(hyper["num_envs"]),
        "--max_iterations",
        str(hyper["max_iterations"]),
        "--seed",
        str(job["seed"]),
        "--device",
        "cpu" if job["executionDevice"] == "cpu" else "cuda:0",
        "--logger",
        "tensorboard",
        "--experiment_name",
        job["jobId"],
        "--external_callback",
        callback,
        "presets=" + ",".join(task["isaaclab"]["presets"]),
    ]
    aliases = {
        "learning_rate": "algorithm.learning_rate",
        "num_steps_per_env": "num_steps_per_env",
        "num_learning_epochs": "algorithm.num_learning_epochs",
        "num_mini_batches": "algorithm.num_mini_batches",
        "save_interval": "save_interval",
    }
    command.extend(
        f"agent.{target}={json.dumps(hyper[name])}" for name, target in aliases.items() if name in hyper
    )
    for name in ("actor", "critic"):
        if name in plan["model"]:
            command.append(f"agent.{name}.hidden_dims={json.dumps(plan['model'][name])}")
        if "activation" in plan["model"]:
            command.append(f"agent.{name}.activation={plan['model']['activation']}")
    if job["executionDevice"] == "cpu":
        command.append("agent.device=cpu")
        if "newton_mjwarp" in task["isaaclab"]["presets"]:
            command.append("env.sim.physics.solver_cfg.use_mujoco_cpu=true")
    return command


class OwnedRun:
    def __init__(self, node, assignment):
        self.node, self.assignment = node, assignment
        identity = hashlib.sha256((assignment["jobId"] + assignment["leaseId"]).encode()).hexdigest()[:24]
        self.root = node.root / "work" / identity
        self.view_directory = self.root / "view-state"
        self.view_active = threading.Event()
        self.cancel = threading.Event()
        self.status, self.reason = "running", None
        self.started, self.log_cursor = time.monotonic(), 0
        self.metrics = {}
        self._logs = []
        self._log_offsets = {"stdout.log": 0, "stderr.log": 0}
        self.uncertain_owner = False
        self.finished = None
        self.baseline_elapsed = 0.0
        self.current_process = None
        self.thread = threading.Thread(target=self.run, name="owned-training-job", daemon=False)
        self.thread.start()

    def _command(self, command, env, stdout, stderr):
        process = OwnedProcess(command, cwd=self.root, env=env, stdout=stdout, stderr=stderr)
        self.current_process = process.process
        try:
            while process.process.poll() is None:
                if self.cancel.wait(0.05) or time.monotonic() - self.started > self.node.timeout:
                    self.cancel.set()
                    raise InterruptedError("Owned job was cancelled or reached its wall-clock bound")
            if process.process.returncode:
                raise RuntimeError(f"Owned subprocess exited with status {process.process.returncode}")
        finally:
            process.close()
            self.current_process = None

    def _request(self, http, method, url, **arguments):
        while True:
            try:
                response = http.request(method, url, **arguments)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500:
                    raise
            except httpx.TransportError:
                pass
            if self.cancel.wait(0.1) or time.monotonic() - self.started > self.node.timeout:
                self.cancel.set()
                raise InterruptedError("Owned job stopped while waiting for gateway recovery")

    def run(self):
        try:
            if self.root.exists():
                self.uncertain_owner = True
                self.reason = "owner_reconciliation_required"
                history = self.root / "node-reports.jsonl"
                rows = retained_rows(history)
                if rows:
                    self.baseline_elapsed = max(row["elapsedSeconds"] for row in rows)
                for row in retained_rows(self.root / "node-logs.jsonl"):
                    self._logs.append(row["text"])
                    self._log_offsets[row["stream"]] = max(self._log_offsets[row["stream"]], row["line"] + 1)
                return
            self.root.mkdir(parents=True, exist_ok=False)
            env = dict(os.environ)
            for name in ("SPATIAL_NODE_TOKEN", "LW_TOKEN"):
                env.pop(name, None)
            env.update(
                PXR_WORK_THREAD_LIMIT="1",
                PYTHONIOENCODING="utf-8",
                PYTHONDONTWRITEBYTECODE="1",
                WARP_CACHE_PATH=str(self.root / "warp-cache"),
                TORCH_EXTENSIONS_DIR=str(self.root / "torch-cache"),
                TRITON_CACHE_DIR=str(self.root / "triton-cache"),
            )
            with httpx.Client(
                base_url=self.node.url, headers=self.headers(), trust_env=False, timeout=60
            ) as http:
                response = self._request(http, "GET", f"/api/v1/jobs/{self.assignment['jobId']}/inputs")
                inputs = response.json()
                job = inputs["job"]
                expected = {
                    ("task", job["task"]): job["taskContentHash"],
                    ("scene", job["scene"]): job["sceneContentHash"],
                    **{("asset", item["id"]): item["contentHash"] for item in job["assets"]},
                }
                actual = {(item["kind"], item["id"]): item["contentHash"] for item in inputs["packages"]}
                if (
                    inputs["schemaVersion"] != 1
                    or job["jobId"] != self.assignment["jobId"]
                    or job["leaseId"] != self.assignment["leaseId"]
                    or job["resourceId"] != self.assignment["resourceId"]
                    or actual != expected
                    or len(inputs["packages"]) != len(expected)
                ):
                    raise ValueError("Node inputs do not describe this exact immutable dispatch")
                for index, package in enumerate(inputs["packages"]):
                    if package["url"] != f"/api/v1/jobs/{job['jobId']}/inputs/packages/{index}":
                        raise ValueError("Input downloads must stay inside the owned dispatch endpoint")
                    target = self.root / "inputs" / str(index)
                    target.mkdir(parents=True)
                    response = self._request(http, "GET", package["url"])
                    extract_package(io.BytesIO(response.content), target)
                    if content_hash(target) != package["contentHash"]:
                        raise ValueError("Downloaded input content differs from the approved hash")
                    package["directory"] = str(target)
                task_package = next(item for item in inputs["packages"] if item["kind"] == "task")
                scene_package = next(item for item in inputs["packages"] if item["kind"] == "scene")
                import yaml

                inputs["task"] = yaml.safe_load(
                    (Path(task_package["directory"]) / "task.yaml").read_text(encoding="utf-8")
                )
                inputs["hardware"] = {
                    "device": inputs["job"]["executionDevice"],
                    "gpu": ({"uuid": self.assignment["gpuId"], "name": inputs["job"]["resourceModel"]}
                            if self.assignment["gpuId"] is not None else None),
                    "node": self.node.hardware,
                }
                output = self.root / "output"
                output.mkdir()
                manifest = self.root / "inputs.json"
                manifest.write_text(json.dumps(inputs), encoding="utf-8")
                bridge = self.root / "bridge"
                bridge.mkdir()
                shutil.copyfile(
                    Path(__file__).with_name("bridge_callback.py"), bridge / "lw_training_bridge.py"
                )
                shutil.copyfile(Path(__file__).with_name("view_bridge.py"), bridge / "lw_view_bridge.py")
                self.view_directory.mkdir()
                self.view_active.set()
                env.update(
                    LW_NODE_INPUTS=str(manifest),
                    LW_NODE_OUTPUT=str(output),
                    LW_NODE_VIEW_IPC=str(self.view_directory),
                    LW_TASK_PACKAGE=task_package["directory"],
                    LW_SCENE_PACKAGE=scene_package["directory"],
                    LW_ASSET_PACKAGES=json.dumps(
                        {
                            item["id"]: item["directory"]
                            for item in inputs["packages"]
                            if item["kind"] == "asset"
                        }
                    ),
                    PYTHONPATH=str(bridge),
                    CUDA_VISIBLE_DEVICES="-1"
                    if inputs["job"]["executionDevice"] == "cpu"
                    else self.assignment["gpuId"],
                    UV_CACHE_DIR=str(self.node.root / "uv-cache"),
                )
                if inputs["job"]["executionDevice"] == "cpu":
                    env.update(OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
                stage = scene_package["stage"]
                if inputs["task"]["isaaclab"].get("sceneInput", "usd") == "usd":
                    if stage.startswith("python:"):
                        raise ValueError(
                            "Python builder scene has no composed USD; Task needs scene-package support"
                        )
                    env["LW_SCENE_STAGE"] = str(Path(scene_package["directory"]) / relative_path(stage))
                with (
                    (self.root / "stdout.log").open("wb") as stdout,
                    (self.root / "stderr.log").open("wb") as stderr,
                ):
                    environment = self.root / "environment"
                    self._command(
                        [
                            "uv",
                            "venv",
                            "--python",
                            self.node.trainer[0],
                            "--system-site-packages",
                            str(environment),
                        ],
                        env,
                        stdout,
                        stderr,
                    )
                    python = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
                    runtime_sites = self.root / "runtime-sites.json"
                    with runtime_sites.open("wb") as runtime_output:
                        self._command(
                            [
                                self.node.trainer[0],
                                "-c",
                                "import json,site;print(json.dumps(site.getsitepackages()))",
                            ],
                            env,
                            runtime_output,
                            stderr,
                        )
                    sites = json.loads(runtime_sites.read_text(encoding="utf-8"))
                    own_site = (
                        environment / "Lib/site-packages"
                        if os.name == "nt"
                        else next((environment / "lib").glob("python*/site-packages"))
                    )
                    (own_site / "lw-frozen-runtime.pth").write_text(
                        "import site; " + "; ".join(f"site.addsitedir({path!r})" for path in sites) + "\n",
                        encoding="utf-8",
                    )
                    build = self.root / "task-build"
                    shutil.copytree(task_package["directory"], build)
                    self._command(
                        ["uv", "pip", "install", "--python", python, "--no-deps", str(build)],
                        env,
                        stdout,
                        stderr,
                    )
                    args = trainer_arguments(inputs, "lw_training_bridge.install")
                    self._command([python, *self.node.trainer[1:], *args], env, stdout, stderr)
                for pattern, category in (("*.pt", "checkpoints"), ("events.out.tfevents.*", "tensorboard")):
                    for file in (self.root / "logs").rglob(pattern):
                        target = output / category / file.relative_to(self.root / "logs")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(file, target)
                for name in ("stdout.log", "stderr.log"):
                    shutil.copyfile(self.root / name, output / name)
                response = self._request(
                    http,
                    "POST",
                    f"/api/v1/jobs/{self.assignment['jobId']}/artifacts",
                    content=pack_package(output),
                    headers={"Content-Type": "application/x-tar"},
                )
                self.status = "succeeded"
        except BaseException as error:
            self.reason = str(error)
            if isinstance(error, CleanupIncomplete):
                self.uncertain_owner = True
                self.status = "running"
            else:
                self.status = "cancelled" if self.cancel.is_set() else "failed"
        finally:
            self.view_active.clear()
            if not self.uncertain_owner and self.view_directory.exists():
                for name in ("latest.json", "latest.tmp"):
                    with contextlib.suppress(OSError):
                        (self.view_directory / name).unlink(missing_ok=True)
            if self.status in ("succeeded", "failed", "cancelled"):
                self.finished = time.monotonic()
                if self.root.exists():
                    (self.root / "result.json").write_text(
                        json.dumps(
                            {
                                "status": self.status,
                                "reason": self.reason,
                                "elapsedSeconds": self.finished - self.started,
                            }
                        ),
                        encoding="utf-8",
                    )

    def headers(self):
        return self.node.headers | {"X-Lease-Id": self.assignment["leaseId"]}

    def report(self):
        # An exit during any producer-owned read requires another report for its final data.
        producer_finished = not self.thread.is_alive() and not self.uncertain_owner
        if self.uncertain_owner:
            alive = False
            for receipt in self.root.glob("process-*.json"):
                value = json.loads(receipt.read_text(encoding="utf-8"))
                if not cleanup_confirmed(self.root, value):
                    alive = True
                try:
                    process = psutil.Process(value["pid"])
                    alive |= (
                        process.create_time() == value["createdAt"] and process.cmdline() == value["command"]
                    )
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    alive = True
            if not alive:
                result = self.root / "result.json"
                if result.exists():
                    saved = json.loads(result.read_text(encoding="utf-8"))
                    self.status, self.reason = saved["status"], saved["reason"]
                else:
                    self.status, self.reason = "failed", "node_restart_after_owned_process_exit"
                self.uncertain_owner = False
                self.finished = time.monotonic()
        metrics = self.root / "output/metrics.jsonl"
        if metrics.exists():
            complete = metrics.read_text(encoding="utf-8").splitlines()
            if complete:
                try:
                    self.metrics = json.loads(complete[-1])
                except ValueError:
                    pass  # A still-open final line is retried at the next heartbeat.
        for name in ("stdout.log", "stderr.log"):
            path = self.root / name
            if path.exists():
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                complete = [line for line in lines if line.endswith("\n") or producer_finished]
                new = []
                for index, line in enumerate(complete[self._log_offsets[name] :], self._log_offsets[name]):
                    line = line.rstrip("\r\n")
                    for offset in range(0, max(1, len(line)), 15900):
                        new.append(
                            {
                                "stream": name,
                                "line": index,
                                "text": f"[{name}] {line[offset : offset + 15900]}",
                            }
                        )
                if new:
                    with (self.root / "node-logs.jsonl").open("a", encoding="utf-8") as stream:
                        for row in new:
                            stream.write(json.dumps(row) + "\n")
                    self._logs.extend(row["text"] for row in new)
                self._log_offsets[name] = len(complete)
        memory = None
        if self.assignment["gpuId"] is not None and self.current_process is not None:
            try:
                root = psutil.Process(self.current_process.pid)
                identities = {root.pid, *(child.pid for child in root.children(recursive=True))}
                rows = [
                    self.node.process_memory[identity]
                    for identity in identities
                    if identity in self.node.process_memory
                ]
                memory = sum(rows) if rows else None
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        selected = self._logs[self.log_cursor:self.log_cursor + 500]
        draining = self.status in ("succeeded", "failed", "cancelled") and (
            not producer_finished or self.log_cursor + len(selected) < len(self._logs))
        return {
            **self.metrics,
            "jobId": self.assignment["jobId"],
            "leaseId": self.assignment["leaseId"],
            "status": "running" if draining else self.status,
            "reason": "draining_logs" if draining else self.reason,
            "elapsedSeconds": self.baseline_elapsed + (self.finished or time.monotonic()) - self.started,
            "logAfter": self.log_cursor,
            "logs": selected,
            "memoryUsedMb": memory,
        }


class LocalNode:
    def __init__(self, url, root, trainer, capabilities, *, interval=10, timeout=86400):
        self.url, self.root, self.trainer = url, Path(root), trainer
        self.capabilities, self.interval, self.timeout = capabilities, interval, timeout
        self.root.mkdir(parents=True, exist_ok=True)
        identity = self.root / "instance-id"
        if not identity.exists():
            import uuid

            identity.write_text(uuid.uuid4().hex, encoding="utf-8")
        self.instance = identity.read_text(encoding="utf-8")
        self.hardware = capabilities["hardware"]
        self.headers = {"X-Node-Token": os.environ["SPATIAL_NODE_TOKEN"], "X-Node-Instance": self.instance}
        self.active = {}
        self.capabilities_path = None
        self.process_memory = {}

    async def run(self):
        view_worker = asyncio.create_task(relay_views(self), name="node-view-relays")
        async with httpx.AsyncClient(
            base_url=self.url, headers=self.headers, trust_env=False, timeout=10
        ) as http:
            try:
                while True:
                    if (self.root / "stop").exists():
                        return
                    if self.capabilities_path:
                        updated = json.loads(self.capabilities_path.read_text(encoding="utf-8"))
                        if updated["hardware"] != self.hardware:
                            raise RuntimeError("A live node cannot change its measured hardware identity")
                        self.capabilities["profiles"] = updated.get("profiles", [])
                    if self.capabilities["gpus"]:
                        usage, self.process_memory = await asyncio.to_thread(gpu_usage)
                        for gpu in self.capabilities["gpus"]:
                            gpu.update(
                                usage.get(
                                    gpu["id"],
                                    {
                                        "memoryUsedMb": None,
                                        "utilization": None,
                                        "observedAt": None,
                                        "unavailableReason": "GPU telemetry unavailable",
                                    },
                                )
                            )
                    reports = [owned.report() for owned in self.active.values()]
                    for report in reports:
                        root = self.active[report["jobId"]].root
                        if root.exists():
                            with (root / "node-reports.jsonl").open("a", encoding="utf-8") as stream:
                                stream.write(json.dumps(report) + "\n")
                    try:
                        response = await http.post(
                            "/api/v1/nodes/local/heartbeat",
                            json={
                                **self.capabilities,
                                "instanceId": self.instance,
                                "reports": reports,
                            },
                        )
                        response.raise_for_status()
                        reply = response.json()
                        for identity, cursor in reply["logCursors"].items():
                            self.active[identity].log_cursor = cursor
                        assignments = {row["jobId"]: row for row in reply["assignments"]}
                        for identity, owned in list(self.active.items()):
                            if identity not in assignments and not owned.thread.is_alive():
                                owned.thread.join()
                                del self.active[identity]
                        for identity, assignment in assignments.items():
                            if identity not in self.active:
                                self.active[identity] = OwnedRun(self, assignment)
                            elif self.active[identity].assignment["leaseId"] != assignment["leaseId"]:
                                raise RuntimeError("Node cannot replace an owned live dispatch")
                            if assignment["cancel"]:
                                self.active[identity].cancel.set()
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code < 500:
                            for owned in self.active.values():
                                owned.cancel.set()
                    except httpx.TransportError:
                        pass  # Preserve process ownership and retry the same reports after gateway recovery.
                    await asyncio.sleep(self.interval)
            finally:
                view_worker.cancel()
                await asyncio.gather(view_worker, return_exceptions=True)
                for owned in self.active.values():
                    owned.cancel.set()
                await asyncio.gather(
                    *(asyncio.to_thread(owned.thread.join) for owned in self.active.values())
                )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument(
        "--trainer", required=True, help="JSON command prefix in an existing Isaac Lab environment"
    )
    parser.add_argument("--capabilities", help="Measured node/GPU/timing metadata JSON")
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=86400)
    args = parser.parse_args()
    capabilities_path = (
        Path(args.capabilities) if args.capabilities else Path(args.root) / "capabilities.json"
    )
    if args.capabilities:
        capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
    else:
        capabilities = discover()
        if capabilities_path.exists():
            previous = json.loads(capabilities_path.read_text(encoding="utf-8"))
            if previous["hardware"] == capabilities["hardware"]:
                capabilities["profiles"] = previous.get("profiles", [])
        capabilities_path.parent.mkdir(parents=True, exist_ok=True)
        capabilities_path.write_text(json.dumps(capabilities), encoding="utf-8")
    node = LocalNode(
        args.url,
        args.root,
        json.loads(args.trainer),
        capabilities,
        interval=args.interval,
        timeout=args.timeout,
    )
    node.capabilities_path = capabilities_path
    asyncio.run(node.run())


def gpu_usage():
    usage, processes = {}, {}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                identity, memory, utilization = [part.strip() for part in line.split(",", 2)]
                usage[identity] = {
                    "memoryUsedMb": float(memory),
                    "utilization": float(utilization),
                    "observedAt": time.time(),
                    "unavailableReason": None,
                }
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                identity, memory = [part.strip() for part in line.split(",", 1)]
                if memory != "N/A":
                    processes[int(identity)] = processes.get(int(identity), 0) + float(memory)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return usage, processes


def discover():
    gpus = []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=uuid,name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                identifier, name, memory = [value.strip() for value in line.split(",", 2)]
                gpus.append({"id": identifier, "name": name, "memoryTotalMb": float(memory)})
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    hardware = json.dumps({"host": platform.uname()._asdict(), "gpus": gpus}, sort_keys=True)
    return {
        "hardware": hashlib.sha256(hardware.encode()).hexdigest(),
        "gpus": gpus,
        "cpuAvailable": True,
        "profiles": [],
    }


if __name__ == "__main__":
    main()

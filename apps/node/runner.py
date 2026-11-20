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
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil

from apps.node.mjlab_launcher import compiler_hash, compiler_sources
from apps.node.process import CleanupIncomplete, OwnedProcess, cleanup_confirmed
from apps.node.view_relay import relay_views
from packages.library.archive import extract_package, import_lock, package_files, relative_path
from packages.library.common import content_hash
from packages.library.transactions import durable_json
from packages.motion.contract import MAX_VIDEO_BYTES, MODEL_SHA256, POSE_RUNTIME, SDK_VERSION
from packages.training import checkpoint_format
from packages.training import runtime as training_runtime
from packages.training.checkpoints import identity as checkpoint_identity
from packages.training.inputs import pack_package
from packages.training.schema import TaskMetadata

GATEWAY_CREDENTIALS = {"LW_TOKEN", "SPATIAL_NODE_TOKEN", "SPATIAL_OPERATOR_TOKEN", "SPATIAL_VIEWER_TOKEN"}


def work_directory_id(job_id, lease_id):
    return hashlib.sha256((job_id + lease_id).encode()).hexdigest()[:24]


def owned_processes_clean(root):
    try:
        for path in root.iterdir():
            if not path.match("process-*.json"):
                continue
            receipt = json.loads(path.read_text(encoding="utf-8"))
            if not cleanup_confirmed(root, receipt):
                return False
            try:
                process = psutil.Process(receipt["pid"])
                if process.create_time() == receipt["createdAt"] and process.cmdline() == receipt["command"]:
                    return False
            except psutil.NoSuchProcess:
                continue
    except (OSError, ValueError, KeyError, TypeError, psutil.AccessDenied):
        return False
    return True


def trainer_environment():
    return {key: value for key, value in os.environ.items() if key not in GATEWAY_CREDENTIALS}


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


def mjlab_trainer_arguments(inputs, callback):
    plan, job = inputs["plan"], inputs["job"]
    task = inputs["task"]
    mjlab = task["mjlab"]
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
    if set(mjlab) - {"task_id", "sceneInput", "physics_dt", "decimation"}:
        raise ValueError("Unsupported mjlab backend fields; no field may be silently ignored")
    if set(hyper) - known or set(plan["model"]) - {"actor", "critic", "activation", "obs_normalization"}:
        raise ValueError("Unsupported approved hyperparameter/model fields; no field may be silently ignored")
    command = [
        "--task",
        mjlab["task_id"],
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
    ]
    if mjlab.get("physics_dt") is not None:
        command.extend(["--physics_dt", str(mjlab["physics_dt"])])
    if mjlab.get("decimation") is not None:
        command.extend(["--decimation", str(mjlab["decimation"])])
    for name in (
        "learning_rate",
        "num_steps_per_env",
        "num_learning_epochs",
        "num_mini_batches",
        "save_interval",
    ):
        if name in hyper:
            command.extend([f"--{name}", str(hyper[name])])
    if "actor" in plan["model"]:
        command.extend(["--actor_hidden_dims", json.dumps(plan["model"]["actor"])])
    if "critic" in plan["model"]:
        command.extend(["--critic_hidden_dims", json.dumps(plan["model"]["critic"])])
    if "activation" in plan["model"]:
        command.extend(["--activation", str(plan["model"]["activation"])])
    if "obs_normalization" in plan["model"]:
        command.extend(["--obs_normalization", json.dumps(bool(plan["model"]["obs_normalization"]))])
    return command


class OwnedRun:
    def __init__(self, node, assignment):
        self.node, self.assignment = node, assignment
        self.root = node.root / "work" / work_directory_id(assignment["jobId"], assignment["leaseId"])
        self.view_directory = self.root / "view-state"
        self.view_active = threading.Event()
        self.cancel = threading.Event()
        self.status, self.reason = "running", None
        self.started = time.monotonic()
        self.log_cursor = assignment.get("logAfter", 0)
        self.timeout = min(node.timeout, assignment.get("timeoutSeconds", node.timeout))
        self.metrics = {"iteration": assignment["startIteration"]} if "startIteration" in assignment else {}
        self.checkpoint_http = None
        self._logs = []
        self._log_offsets = {"stdout.log": 0, "stderr.log": 0}
        self.uncertain_owner = False
        self.finished = None
        self.baseline_elapsed = 0.0
        self.current_process = None
        self.thread = threading.Thread(target=self.run, name="owned-training-job", daemon=False)
        self.thread.start()

    def _send_checkpoints(self):
        if self.checkpoint_http is None:
            return
        for file in sorted((self.root / "checkpoints-pending").glob("*.tar")):
            try:
                with file.open("rb") as source:
                    raw = source.read(checkpoint_format.MAX_BYTES + 1)
            except FileNotFoundError:
                continue
            if len(raw) > checkpoint_format.MAX_BYTES:
                raise ValueError("Trainer checkpoint exceeds the wire limit")
            reply = self._request(
                self.checkpoint_http, "POST", f"/api/v1/jobs/{self.assignment['jobId']}/checkpoints",
                content=raw, headers={"Content-Type": "application/x-tar"},
            )
            if reply.json()["sha256"] != checkpoint_format.digest(raw):
                raise ValueError("Gateway confirmed different checkpoint bytes")
            file.unlink(missing_ok=True)

    def _command(self, command, env, stdout, stderr):
        process = OwnedProcess(command, cwd=self.root, env=env, stdout=stdout, stderr=stderr)
        self.current_process = process.process
        try:
            while process.process.poll() is None:
                if self.cancel.wait(0.05) or time.monotonic() - self.started > self.timeout:
                    self.cancel.set()
                    raise InterruptedError("Owned job was cancelled or reached its wall-clock bound")
                self._send_checkpoints()
            if process.process.returncode:
                raise RuntimeError(f"Owned subprocess exited with status {process.process.returncode}")
            self._send_checkpoints()
        finally:
            process.close()
            self.current_process = None

    def _request(self, http, method, url, *, maximum_bytes=None, **arguments):
        while True:
            remaining = self.timeout - (time.monotonic() - self.started)
            if self.cancel.is_set() or remaining <= 0:
                self.cancel.set()
                raise InterruptedError("Owned job stopped before completing gateway I/O")
            try:
                if maximum_bytes is None:
                    response = http.request(method, url, timeout=min(10, remaining), **arguments)
                    response.raise_for_status()
                    return response
                with http.stream(method, url, timeout=min(10, remaining), **arguments) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw) + len(chunk) > maximum_bytes:
                            raise ValueError("Downloaded checkpoint exceeds the leased size")
                        if self.cancel.is_set() or time.monotonic() - self.started > self.timeout:
                            raise InterruptedError("Owned job stopped during checkpoint download")
                        raw.extend(chunk)
                    return bytes(raw)
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500:
                    raise
            except httpx.TransportError:
                pass
            self.cancel.wait(0.1)

    def _motion(self, http, inputs, env):
        command = self.node.pose_command
        if command is None or inputs["poseRuntime"] != POSE_RUNTIME:
            raise ValueError("pose_runtime_unavailable")
        video = inputs["video"]
        raw = self._request(http, "GET", video["url"], maximum_bytes=MAX_VIDEO_BYTES)
        if len(raw) != video["bytes"] or hashlib.sha256(raw).hexdigest() != video["sha256"]:
            raise ValueError("motion_video_changed")
        video_path = self.root / "video"
        evidence = self.root / "pose.json"
        try:
            video_path.write_bytes(raw)
            source = self.root / "video.json"
            durable_json(source, video)
            with ((self.root / "stdout.log").open("wb") as stdout,
                  (self.root / "stderr.log").open("wb") as stderr):
                self._command([*command, "--inputs", str(source)], env, stdout, stderr)
            reply = self._request(
                http, "POST", f"/api/v1/jobs/{self.assignment['jobId']}/artifacts",
                content=evidence.read_bytes(), headers={"Content-Type": "application/json"},
            ).json()
            if not reply.get("motionClip"):
                raise ValueError("motion_artifact_invalid")
            self.status = "succeeded"
        except httpx.HTTPStatusError as exc:
            detail = exc.response.json().get("detail")
            if isinstance(detail, dict) and str(detail.get("code", "")).startswith("motion_"):
                raise ValueError(detail["code"]) from exc
            raise
        finally:
            video_path.unlink(missing_ok=True)
            evidence.unlink(missing_ok=True)

    def run(self):
        try:
            if self.assignment.get("cleanupOnly") or self.root.exists():
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
            env = trainer_environment()
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
                if job["kind"] == "motion_preprocess":
                    self._motion(http, inputs, env)
                    return
                validation = job["kind"] == "plan_validation"
                portable = inputs["schemaVersion"] == 2
                if portable and (self.node.capabilities.get("checkpointVersion") != 1
                                 or "checkpointIdentity" not in inputs):
                    raise ValueError("This node cannot execute the portable dispatch")
                if not portable and not validation:
                    inputs["keepLegacyCheckpointLocal"] = True
                if inputs.get("resumeCheckpoint") and not portable:
                    raise ValueError("Resume requires a portable dispatch version")
                if validation:
                    self.timeout = min(self.timeout, job["validationLimits"]["timeoutSeconds"])
                expected = {
                    ("task", job["task"]): job["taskContentHash"],
                    ("scene", job["scene"]): job["sceneContentHash"],
                    **{("asset", item["id"]): item["contentHash"] for item in job["assets"]},
                    **{("demoset", item["id"]): item["contentHash"]
                       for item in job.get("demonstrationSets", [])},
                    **{("recording", item["id"]): item["contentHash"]
                       for item in job.get("demonstrationRecordings", [])},
                }
                actual = {(item["kind"], item["id"]): item["contentHash"] for item in inputs["packages"]}
                if (
                    inputs["schemaVersion"] not in (1, 2)
                    or job["jobId"] != self.assignment["jobId"]
                    or job["leaseId"] != self.assignment["leaseId"]
                    or job["resourceId"] != self.assignment["resourceId"]
                    or actual != expected
                    or len(inputs["packages"]) != len(expected)
                ):
                    raise ValueError("Node inputs do not describe this exact immutable dispatch")
                if portable and inputs["checkpointIdentity"] != checkpoint_identity(
                    job | {"plan": inputs["plan"]},
                    {"leaseId": job["leaseId"], "runtimeIdentity": inputs["runtimeIdentity"]},
                ):
                    raise ValueError("Portable checkpoint identity differs from the leased dispatch")
                if source := inputs.get("resumeCheckpoint"):
                    expected_url = f"/api/v1/jobs/{job['jobId']}/inputs/checkpoint/{source['sha256']}"
                    if (
                        source["url"] != expected_url
                        or source["inputsSha256"] != inputs["checkpointIdentity"]["inputsSha256"]
                        or source["runtime"] != inputs.get("runtimeIdentity")
                        or source["iteration"] != self.assignment.get("startIteration")
                    ):
                        raise ValueError("Checkpoint source does not match the leased dispatch")
                    size = source["size"]
                    if type(size) is not int or not 0 < size <= checkpoint_format.MAX_BYTES:
                        raise ValueError("Invalid leased checkpoint size")
                    raw = self._request(http, "GET", expected_url, maximum_bytes=size)
                    if len(raw) != source["size"] or checkpoint_format.digest(raw) != source["sha256"]:
                        raise ValueError("Downloaded checkpoint differs from the leased digest")
                    target = self.root / "checkpoint.tar"
                    target.write_bytes(raw)
                    inputs["checkpointPath"] = str(target)
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
                declarative = inputs["task"].get("declarative") is not None
                if declarative:
                    declared = TaskMetadata.model_validate(inputs["task"])
                    task_root = Path(task_package["directory"])
                    if set(package_files(task_root)) != {"task.yaml", declared.observation}:
                        raise ValueError("Declarative task input contains unapproved files")
                    inputs["task"] = declared.model_dump(mode="json")
                demonstration_inputs = []
                for reference in job.get("demonstrationSets", []):
                    dataset = next(item for item in inputs["packages"]
                                   if item["kind"] == "demoset" and item["id"] == reference["id"])
                    manifest = json.loads((Path(dataset["directory"]) / "manifest.json").read_bytes())
                    recordings = {}
                    for entry in manifest["demonstrations"]:
                        recording = next(
                            item for item in inputs["packages"]
                            if item["kind"] == "recording" and item["id"] == entry["recordingId"]
                        )
                        if recording["contentHash"] != entry["recordingContentHash"]:
                            raise ValueError("DemoSet refers to another recording package")
                        recordings[recording["id"]] = recording["directory"]
                    demonstration_inputs.append(reference | {
                        "directory": dataset["directory"], "recordings": recordings,
                    })
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
                shutil.copyfile(Path(__file__).with_name("environment_observer.py"),
                                bridge / "lw_environment_observer.py")
                shutil.copyfile(Path(__file__).with_name("native_observation_capture.py"),
                                bridge / "lw_native_observation_capture.py")
                shutil.copyfile(Path(__file__).with_name("observation_capture.py"),
                                bridge / "lw_observation_capture.py")
                for name, source in compiler_sources().items():
                    shutil.copyfile(source, bridge / ("lw_" + name))
                shutil.copyfile(training_runtime.__file__, bridge / "lw_training_runtime.py")
                if portable:
                    shutil.copyfile(checkpoint_format.__file__, bridge / "lw_checkpoint_format.py")
                    shutil.copyfile(Path(__file__).with_name("checkpoint_runner.py"),
                                    bridge / "lw_checkpoint_runner.py")
                leased_compiler = inputs.get("compilerHash")
                if leased_compiler and compiler_hash(bridge, prefix="lw_") != leased_compiler:
                    raise RuntimeError("Bundled mjlab compiler differs from the leased compiler digest")
                if not validation:
                    self.view_directory.mkdir()
                    self.view_active.set()
                env.update(
                    LW_NODE_INPUTS=str(manifest),
                    LW_NODE_OUTPUT=str(output),
                    LW_TASK_PACKAGE=task_package["directory"],
                    LW_SCENE_PACKAGE=scene_package["directory"],
                    LW_ASSET_PACKAGES=json.dumps(
                        {
                            item["id"]: item["directory"]
                            for item in inputs["packages"]
                            if item["kind"] == "asset"
                        }
                    ),
                    LW_DEMONSTRATION_SETS=json.dumps(demonstration_inputs),
                    PYTHONPATH=str(bridge),
                    CUDA_VISIBLE_DEVICES="-1"
                    if inputs["job"]["executionDevice"] == "cpu"
                    else self.assignment["gpuId"],
                    UV_CACHE_DIR=str(self.node.root / "uv-cache"),
                )
                if not validation:
                    env["LW_NODE_VIEW_IPC"] = str(self.view_directory)
                if inputs["job"]["executionDevice"] == "cpu":
                    env.update(OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
                is_mjlab = inputs["task"].get("mjlab") is not None
                backend_name = "mjlab" if is_mjlab else "isaaclab"
                trainer_prefix = list(self.node.mjlab_trainer if is_mjlab else self.node.trainer)
                if not trainer_prefix:
                    raise RuntimeError(
                        f"{backend_name} trainer runtime is not configured on this compute node"
                    )
                entry = trainer_prefix[1:]
                if runtime := inputs.get("runtimeIdentity"):
                    if (
                        runtime["backend"] != backend_name
                        or training_runtime.entry_identity(backend_name, entry) != runtime["entry"]
                    ):
                        raise RuntimeError(
                            "Configured trainer entry differs from the leased runtime identity"
                        )
                    if backend_name == "isaaclab" and not entry[0].startswith("-"):
                        entry[0] = str(Path(entry[0]).resolve())
                if declarative and (
                    not leased_compiler or training_runtime.entry_identity("mjlab", entry) != "mjlab:managed"
                ):
                    raise ValueError("Declarative Task requires the leased platform mjlab entry")
                if is_mjlab and (validation or not entry):
                    entry = list(training_runtime.MJLAB_ENTRY)
                stage = scene_package["stage"]
                env.pop("LW_SCENE_STAGE", None)
                if inputs["task"][backend_name].get("sceneInput", "usd") == "usd":
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
                            trainer_prefix[0],
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
                                trainer_prefix[0],
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
                    if not declarative:
                        build = self.root / "task-build"
                        shutil.copytree(task_package["directory"], build)
                        self._command(
                            ["uv", "pip", "install", "--python", python, "--no-deps", str(build)],
                            env,
                            stdout,
                            stderr,
                        )
                    if is_mjlab:
                        if portable and job["kind"] == "training":
                            self.checkpoint_http = http
                        args = mjlab_trainer_arguments(inputs, "lw_training_bridge.install")
                        if validation:
                            iterations = str(job["validationLimits"]["iterations"])
                            args.extend(["--validation-iterations", iterations])
                        try:
                            self._command([python, *entry, *args], env, stdout, stderr)
                        except RuntimeError as error:
                            diagnostic = output / "validation-error.json"
                            message = None
                            if validation and diagnostic.is_file() and diagnostic.stat().st_size < 32_768:
                                with contextlib.suppress(OSError, ValueError):
                                    report = json.loads(diagnostic.read_bytes())
                                    message = report.get("message") if isinstance(report, dict) else None
                                if isinstance(message, str) and message:
                                    raise ValueError(message[:3500]) from error
                            raise
                    else:
                        args = trainer_arguments(inputs, "lw_training_bridge.install")
                        self._command([python, *entry, *args], env, stdout, stderr)
                if validation:
                    self.metrics = {
                        "iteration": job["validationLimits"]["iterations"],
                        "validation": json.loads((output / "validation.json").read_text(encoding="utf-8")),
                    }
                    self.status = "succeeded"
                    return
                for file in (self.root / "logs").rglob("events.out.tfevents.*"):
                    target = output / "tensorboard" / file.relative_to(self.root / "logs")
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
        if self.uncertain_owner and owned_processes_clean(self.root):
            result = self.root / "result.json"
            if result.exists():
                saved = json.loads(result.read_text(encoding="utf-8"))
                self.status, self.reason = saved["status"], saved["reason"]
            else:
                self.status, self.reason = "failed", "node_restart_after_owned_process_exit"
            self.uncertain_owner = False
            self.finished = time.monotonic()
        # An exit during any producer-owned read requires another report for its final data.
        producer_finished = not self.thread.is_alive() and not self.uncertain_owner
        elapsed = self.baseline_elapsed + (self.finished or time.monotonic()) - self.started
        if producer_finished and ("cleanup" in self.assignment or self.assignment.get("quarantined")):
            producer_finished = owned_processes_clean(self.root)
        recovered = self.assignment.get("quarantined") and producer_finished
        snapshot = self.root / "recovered-log-tail.json"
        metrics = self.root / "output/metrics.jsonl"
        if metrics.exists():
            complete = metrics.read_text(encoding="utf-8").splitlines()
            if complete:
                try:
                    self.metrics = json.loads(complete[-1])
                except ValueError:
                    pass  # A still-open final line is retried at the next heartbeat.
        for name in ("stdout.log", "stderr.log"):
            if recovered and snapshot.exists():
                break
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
        offset = self.log_cursor - self.assignment.get("logAfter", 0)
        selected = self._logs[offset:offset + 500]
        if recovered:
            if snapshot.exists():
                final_cursor = json.loads(snapshot.read_bytes())["end"]
                if final_cursor != len(self._logs):
                    raise RuntimeError("The recovered log spool differs from its final cursor")
            else:
                # Sync the spool and its final cursor before releasing the GPU.
                spool = self.root / "node-logs.jsonl"
                if spool.exists():
                    with spool.open("rb") as stream:
                        os.fsync(stream.fileno())
                final_cursor = len(self._logs)
                durable_json(snapshot, {"end": final_cursor})
            return {
                "jobId": self.assignment["jobId"], "leaseId": self.assignment["leaseId"],
                "status": "cleaned", "elapsedSeconds": elapsed, "tailEnd": final_cursor,
                "logAfter": self.log_cursor, "logs": selected,
            }
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
        draining = self.status in ("succeeded", "failed", "cancelled") and (
            not producer_finished or offset + len(selected) < len(self._logs))
        status = "running" if draining else self.status
        return {
            **{key: value for key, value in self.metrics.items()
               if key != "validation" or status == "succeeded"},
            "jobId": self.assignment["jobId"],
            "leaseId": self.assignment["leaseId"],
            "status": status,
            "reason": "draining_logs" if draining else self.reason,
            "elapsedSeconds": elapsed,
            "logAfter": self.log_cursor,
            "logs": selected,
            "memoryUsedMb": memory,
        }


class LocalNode:
    def __init__(self, url, root, trainer, capabilities, *, mjlab_trainer=(), interval=10, timeout=86400,
                 node_id="local", token=None, pose_command=None):
        self.url, self.root, self.trainer = url, Path(root), trainer
        self.mjlab_trainer = mjlab_trainer
        self.pose_command = pose_command
        self.capabilities, self.interval, self.timeout = capabilities, interval, timeout
        self.root.mkdir(parents=True, exist_ok=True)
        self.node_id, self.instance = node_id, uuid.uuid4().hex
        if node_id == "local":
            identity = self.root / "instance-id"
            if not identity.exists():
                identity.write_text(self.instance, encoding="utf-8")
            self.instance = identity.read_text(encoding="utf-8")
        self.hardware = capabilities["hardware"]
        self.headers = {"X-Node-Token": token or os.environ["SPATIAL_NODE_TOKEN"],
                        "X-Node-Id": node_id, "X-Node-Instance": self.instance}
        self.active = {}
        self.capabilities_path = None
        self.process_memory = {}
        self.cleanup_receipts = self.root / "cleanup-receipts"

    def _remember_cleanup(self, assignment):
        capability = assignment.get("cleanup")
        if capability is None or capability["schemaVersion"] != 1:
            return
        job, lease = assignment["jobId"], assignment["leaseId"]
        # Save the capability before starting any subprocess. The work directory then records each launcher.
        durable_json(self.cleanup_receipts / f"{work_directory_id(job, lease)}.json", {
            "url": self.url, "token": capability["token"],
            "receipt": {
                "schemaVersion": 1, "nodeId": self.node_id, "instanceId": self.instance,
                "jobId": job, "leaseId": lease, "gpuId": assignment["gpuId"],
            },
        })

    async def _send_cleanup(self, http, *, stopping=False):
        active = {run.root.name for run in self.active.values()}
        for path in self.cleanup_receipts.glob("*.json"):
            if not stopping and path.stem in active:
                continue
            if not await asyncio.to_thread(owned_processes_clean, self.root / "work" / path.stem):
                continue
            try:
                pending = json.loads(path.read_text(encoding="utf-8"))
                if pending["url"] != self.url:
                    continue
                response = await http.post(
                    "/api/v1/nodes/cleanup", json=pending["receipt"],
                    headers={"X-Node-Cleanup-Token": pending["token"]},
                )
                if response.status_code == 204:
                    path.unlink()
            except (OSError, ValueError, KeyError, httpx.TransportError):
                continue  # Retain the receipt for a later poll or a subsequent runner process.

    async def run(self):
        view_worker = asyncio.create_task(relay_views(self), name="node-view-relays")
        refresh_recovery = False
        retiring = set()
        async with httpx.AsyncClient(base_url=self.url, trust_env=False, timeout=10) as http:
            try:
                while True:
                    await self._send_cleanup(http)
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
                    reports = []
                    if not refresh_recovery:
                        for identity, owned in self.active.items():
                            if identity in retiring:
                                continue
                            report = owned.report()
                            if owned.assignment.get("quarantined") and report["status"] == "running":
                                continue
                            reports.append(report)
                    refresh_recovery = False
                    for report in reports:
                        root = self.active[report["jobId"]].root
                        if root.exists():
                            with (root / "node-reports.jsonl").open("a", encoding="utf-8") as stream:
                                stream.write(json.dumps(report) + "\n")
                    try:
                        response = await http.post(
                            f"/api/v1/nodes/{self.node_id}/heartbeat",
                            headers=self.headers,
                            json={
                                **self.capabilities,
                                "instanceId": self.instance,
                                "reports": reports,
                            },
                        )
                        response.raise_for_status()
                        reply = response.json()
                        if reply["nodeId"] != self.node_id:
                            raise RuntimeError("Gateway replied for another Compute Node")
                        for identity, cursor in reply["logCursors"].items():
                            self.active[identity].log_cursor = cursor
                        assignments = {
                            row["jobId"]: row for row in (*reply["assignments"], *reply.get("logTails", ()))
                        }
                        retiring = self.active.keys() - assignments.keys()
                        for identity, owned in list(self.active.items()):
                            if identity in retiring:
                                owned.cancel.set()
                                if not owned.thread.is_alive():
                                    owned.thread.join()
                                    del self.active[identity]
                        for identity, assignment in assignments.items():
                            if identity not in self.active:
                                self._remember_cleanup(assignment)
                                self.active[identity] = OwnedRun(self, assignment)
                            elif self.active[identity].assignment["leaseId"] != assignment["leaseId"]:
                                previous = self.active[identity]
                                if "logAfter" not in assignment:
                                    raise RuntimeError("Node cannot replace an owned live dispatch")
                                previous.cancel.set()
                                retiring.add(identity)
                                if not previous.thread.is_alive() and not previous.uncertain_owner:
                                    previous.thread.join()
                                    self._remember_cleanup(assignment)
                                    self.active[identity] = OwnedRun(self, assignment)
                                    retiring.discard(identity)
                            else:
                                self.active[identity].assignment = assignment
                            if assignment["cancel"]:
                                self.active[identity].cancel.set()
                    except httpx.HTTPStatusError as error:
                        if reports and error.response.status_code == 403:
                            try:
                                detail = error.response.json()["detail"]
                            except (ValueError, KeyError, TypeError):
                                detail = None
                            # Refresh ownership once without replaying fenced job reports.
                            refresh_recovery = (
                                isinstance(detail, dict) and detail.get("code") == "lease_mismatch"
                            )
                        if not refresh_recovery and error.response.status_code < 500:
                            for owned in self.active.values():
                                owned.cancel.set()
                                if (error.response.status_code == 422 and not owned.thread.is_alive()
                                        and owned.status == "succeeded" and "validation" in owned.metrics):
                                    owned.status = "failed"
                                    owned.reason = "gateway_rejected_validation"
                            if self.node_id != "local" and error.response.status_code in (401, 403, 409):
                                raise RuntimeError(
                                    "Gateway rejected Compute Node credentials or ownership"
                                ) from None
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
                await self._send_cleanup(http, stopping=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--node-id", help="Persistent Compute Node ID (defaults to local)")
    parser.add_argument("--registration-code", help="Exchange a single-use code and retain the node token")
    parser.add_argument(
        "--trainer", default="[]", help="JSON command prefix in an existing Isaac Lab environment"
    )
    parser.add_argument(
        "--mjlab-trainer", default="[]", help="JSON command prefix in an existing mjlab environment"
    )
    parser.add_argument("--capabilities", help="Measured node/GPU/timing metadata JSON")
    parser.add_argument("--pose-python", help="Python interpreter in the owned MediaPipe CPU environment")
    parser.add_argument("--pose-model", help="Local path to the pinned Pose Landmarker Lite model bundle")
    parser.add_argument("--interval", type=float, default=10)
    parser.add_argument("--timeout", type=float, default=86400)
    args = parser.parse_args()
    with import_lock(Path(args.root)):
        run_node(args)


def node_credentials(args):
    url = args.url.rstrip("/")
    parsed = urlsplit(url)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Use an explicit gateway HTTP(S) origin")
    path = Path(args.root) / "node-credentials.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if args.registration_code:
        with httpx.Client(base_url=url, trust_env=False, timeout=10) as http:
            response = http.post("/api/v1/nodes/register", json={"code": args.registration_code})
            if not response.is_success:
                raise RuntimeError(f"Compute Node registration failed (HTTP {response.status_code})")
            saved = response.json() | {"url": url}
        durable_json(path, saved)
    node_id = args.node_id or (saved["nodeId"] if saved else "local")
    token = os.getenv("SPATIAL_NODE_TOKEN")
    if args.registration_code or not token and saved:
        if saved["url"] != url or saved["nodeId"] != node_id:
            raise ValueError("Stored registration belongs to a different gateway or Compute Node")
        token = saved["token"]
    if not token:
        raise ValueError("Use --registration-code or set SPATIAL_NODE_TOKEN")
    return node_id, token


def run_node(args):
    node_id, token = node_credentials(args)
    trainer = json.loads(args.trainer)
    mjlab_trainer = json.loads(args.mjlab_trainer)
    capabilities_path = (
        Path(args.capabilities) if args.capabilities else Path(args.root) / "capabilities.json"
    )
    if args.capabilities:
        capabilities = json.loads(capabilities_path.read_text(encoding="utf-8"))
    else:
        capabilities = discover()
        if capabilities_path.exists():
            previous = json.loads(capabilities_path.read_text(encoding="utf-8"))
            if previous["hardware"] in (capabilities["hardware"], capabilities.get("legacyHardware")):
                capabilities["profiles"] = previous.get("profiles", [])
        if node_id != "local":
            capabilities.pop("legacyHardware", None)
    mjlab_versions = trainer_versions(mjlab_trainer, is_mjlab=True)
    isaaclab_versions = trainer_versions(trainer, is_mjlab=False)
    modern = "mjlab" in mjlab_versions or "isaaclab" in isaaclab_versions
    if modern or node_id != "local" or not args.capabilities:
        capabilities["trainerVersions"] = mjlab_versions or isaaclab_versions
        capabilities["isaaclabVersions"] = isaaclab_versions
        entries = {
            backend: training_runtime.entry_identity(backend, prefix[1:])
            for backend, prefix in (("isaaclab", trainer), ("mjlab", mjlab_trainer)) if prefix
        }
        capabilities["trainingEntries"] = {backend: entry for backend, entry in entries.items() if entry}
    if "mjlab" in (capabilities.get("trainerVersions") or {}):
        capabilities["compilerHash"] = compiler_hash()
    if mjlab_versions.get("rsl_rl") == "5.0.1" and (
        capabilities.get("trainingEntries") or {}
    ).get("mjlab") == "mjlab:managed":
        capabilities["checkpointVersion"] = 1
    capabilities.pop("poseRuntime", None)
    pose_command = None
    pose_python, pose_model = getattr(args, "pose_python", None), getattr(args, "pose_model", None)
    if pose_python or pose_model:
        if not pose_python or not pose_model:
            raise ValueError("Configure both --pose-python and --pose-model")
        pose_command = [
            str(Path(pose_python).absolute()), "-I", str(Path(__file__).with_name("pose_worker.py")),
            str(Path(pose_model).resolve(strict=True)), MODEL_SHA256, SDK_VERSION,
        ]
        subprocess.run(pose_command, check=True, timeout=20, env=trainer_environment(), capture_output=True)
        capabilities["poseRuntime"] = POSE_RUNTIME
        capabilities["cpuAvailable"] = True
    if not args.capabilities:
        durable_json(capabilities_path, capabilities)
    node = LocalNode(
        args.url,
        args.root,
        trainer,
        capabilities,
        mjlab_trainer=mjlab_trainer,
        interval=args.interval,
        timeout=args.timeout,
        node_id=node_id,
        token=token,
        pose_command=pose_command,
    )
    node.capabilities_path = capabilities_path
    asyncio.run(node.run())


def trainer_versions(prefix, *, is_mjlab):
    if not prefix:
        return {}
    script = """
import importlib.metadata
import json
import sys
versions = {}
for name, distribution in json.loads(sys.argv[1]).items():
    try:
        versions[name] = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        pass
print(json.dumps(versions))
"""
    try:
        result = subprocess.run(
            [prefix[0], "-c", script, json.dumps(training_runtime.trainer_distributions(is_mjlab))],
            capture_output=True, text=True, timeout=10, check=True,
            env=trainer_environment(),
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def _nvidia_smi() -> str:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    wsl_smi = Path("/usr/lib/wsl/lib/nvidia-smi")
    if wsl_smi.is_file():
        return str(wsl_smi)
    return "nvidia-smi"


def gpu_usage():
    usage, processes = {}, {}
    smi = _nvidia_smi()
    try:
        result = subprocess.run(
            [smi, "--query-gpu=uuid,memory.used,utilization.gpu", "--format=csv,noheader,nounits"],
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
            [smi, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
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
    smi = _nvidia_smi()
    try:
        result = subprocess.run(
            [smi, "--query-gpu=uuid,name,memory.total,compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            result = subprocess.run(
                [smi, "--query-gpu=uuid,name,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                identifier, name, memory, *cap = [value.strip() for value in line.split(",", 3)]
                gpu = {"id": identifier, "name": name, "memoryTotalMb": float(memory)}
                if cap and cap[0].replace(".", "").isdigit():
                    gpu["computeCapability"] = cap[0]
                gpus.append(gpu)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    host = platform.uname()
    hardware = json.dumps([host.system, host.machine, host.processor])
    legacy = json.dumps({
        "host": host._asdict(),
        "gpus": [{key: gpu[key] for key in ("id", "name", "memoryTotalMb")} for gpu in gpus],
    }, sort_keys=True)
    return {
        "hardware": "node-hardware/2:" + hashlib.sha256(hardware.encode()).hexdigest(),
        "legacyHardware": hashlib.sha256(legacy.encode()).hexdigest(),
        "hostname": host.node,
        "gpus": gpus,
        "cpuAvailable": True,
        "profiles": [],
    }


if __name__ == "__main__":
    main()

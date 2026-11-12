"""GPU-free compute-node lifecycle, POSIX cancellation, and Policy registration tests for mjlab."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import psutil
import uvicorn
import yaml

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import content_hash, document
from packages.policies.inference import CpuPolicy
from packages.policies.observations import ObservationSpec
from tests.fixtures.training_task import task_archive, task_document, training_plan


def _wait_for(read, predicate, *, timeout=30.0):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.05)
    raise AssertionError(f"Condition did not settle within {timeout}s: {value}")


def _start_server(settings: Settings):
    app = create_app(settings)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    object.__setattr__(settings, "node_gateway_url", f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "Owned HTTP server failed to start"
    return app, server, thread


def _build_mjlab_task_package(path: Path, name: str = "mjlab-node-probe") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    doc = task_document(name)
    doc.pop("isaaclab")
    doc["control"] = {"hz": 50.0, "action": "joint_position_offset"}
    doc["mjlab"] = {
        "task_id": "Lab-Arm-NodeProbe-v0",
        "sceneInput": "scene-package",
        "physics_dt": 0.005,
        "decimation": 4,
    }
    joints = ["/World/Lab/RobotA/joint1", "/World/Lab/RobotA/joint2"]
    obs_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "joint_pos",
                "source": "joint_position",
                "keys": joints,
                "frame": "world",
                "unit": "rad",
                "shape": [2],
                "offset": [0.0, 0.0],
                "scale": [1.0, 1.0],
            },
            {
                "name": "joint_vel",
                "source": "joint_velocity",
                "keys": joints,
                "frame": "world",
                "unit": "rad/s",
                "shape": [2],
                "offset": [0.0, 0.0],
                "scale": [1.0, 1.0],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad", "rad"],
            "defaultPosition": [0.0, 0.0],
            "scale": [0.5, 0.5],
            "lower": [-1.57, -1.57],
            "upper": [1.57, 1.57],
        },
    }
    (path / "task.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    (path / "obs_spec.yaml").write_text(yaml.safe_dump(obs_spec), encoding="utf-8")
    (path / "pyproject.toml").write_text(
        '[project]\nname="mjlab-node-probe"\nversion="0.1.0"\n'
        '[project.entry-points."mjlab.tasks"]\nprobe="probe:register"\n',
        encoding="utf-8",
    )
    (path / "probe.py").write_text('def register(): return "mjlab-entry-marker"\n', encoding="utf-8")
    return path


def _write_fake_mjlab_trainer(script_path: Path) -> Path:
    script_path.write_text(
        '''import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


def _write_valid_mlp_onnx(path: Path, obs_dim: int = 4, act_dim: int = 2) -> None:
    rng = np.random.default_rng(7)
    mean = numpy_helper.from_array(np.array([[0.1, -0.1, 0.0, 0.2]], dtype=np.float32), name="obs_mean")
    inv_std = numpy_helper.from_array(np.array([[2.0, 2.0, 1.0, 0.5]], dtype=np.float32), name="obs_inv_std")
    w = numpy_helper.from_array(
        rng.normal(0.0, 0.25, size=(obs_dim, act_dim)).astype(np.float32),
        name="mlp_w",
    )
    b = numpy_helper.from_array(np.array([[0.05, -0.05]], dtype=np.float32), name="mlp_b")

    sub_node = helper.make_node("Sub", ["obs", "obs_mean"], ["obs_centered"])
    mul_node = helper.make_node("Mul", ["obs_centered", "obs_inv_std"], ["obs_norm"])
    mm_node = helper.make_node("MatMul", ["obs_norm", "mlp_w"], ["mm_out"])
    add_node = helper.make_node("Add", ["mm_out", "mlp_b"], ["actions"])

    graph = helper.make_graph(
        [sub_node, mul_node, mm_node, add_node],
        "mjlab_policy_graph",
        [helper.make_tensor_value_info("obs", TensorProto.FLOAT, [1, obs_dim])],
        [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, act_dim])],
        initializer=[mean, inv_std, w, b],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold", action="store_true")
    parser.add_argument("--task", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--num_envs", type=int, required=True)
    parser.add_argument("--max_iterations", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--logger", required=True)
    parser.add_argument("--experiment_name", required=True)
    parser.add_argument("--external_callback", required=True)
    args, _ = parser.parse_known_args()

    assert args.device == "cpu" and os.environ["CUDA_VISIBLE_DEVICES"] == "-1"
    assert "SPATIAL_NODE_TOKEN" not in os.environ
    import lw_mjlab_launcher  # noqa: F401
    import lw_usd_mjcf  # noqa: F401

    registered = importlib.metadata.entry_points(group="mjlab.tasks", name="probe")
    assert len(registered) == 1 and next(iter(registered)).load()() == "mjlab-entry-marker"

    inputs = json.loads(Path(os.environ["LW_NODE_INPUTS"]).read_text(encoding="utf-8"))
    assert args.task == inputs["task"]["mjlab"]["task_id"]
    output = Path(os.environ["LW_NODE_OUTPUT"])

    if args.hold:
        marker = output / "descendant.json"
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import json,os,signal,time,psutil;from pathlib import Path;"
                    "signal.signal(signal.SIGTERM,signal.SIG_IGN);"
                    "m=Path(os.environ['LW_DESCENDANT_MARKER']);t=m.with_suffix('.tmp');"
                    "t.write_text(json.dumps({'pid':os.getpid(),'createdAt':psutil.Process().create_time()}));"
                    "t.replace(m);time.sleep(120)"
                ),
            ],
            env=os.environ | {"LW_DESCENDANT_MARKER": str(marker)},
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists() and child.poll() is None
        while True:
            time.sleep(0.1)

    for it in range(1, args.max_iterations + 1):
        with (output / "metrics.jsonl").open("a", encoding="utf-8") as stream:
            row = {"iteration": it, "elapsedSeconds": 0.05 * it, "meanReward": 1.5 * it}
            stream.write(json.dumps(row) + "\\n")
    print("mjlab fake trainer completed iterations", flush=True)

    versions = {
        "versions": {
            "mjlab": "1.3.0",
            "mujoco": "3.5.0",
            "mujoco_warp": "3.5.0",
            "warp": "1.12.0",
            "torch": "2.9.1",
            "rsl_rl": inputs["plan"]["algorithm"]["version"],
        },
        "hardware": inputs["hardware"],
        "demonstrationSets": inputs["job"].get("demonstrationSets", []),
        **{
            key: inputs["job"][key]
            for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
        },
    }
    (output / "versions.json").write_text(json.dumps(versions), encoding="utf-8")

    logs = Path.cwd() / "logs" / "rsl_rl" / args.experiment_name / "run_0"
    logs.mkdir(parents=True, exist_ok=True)
    ckpt = logs / f"model_{args.max_iterations}.pt"
    ckpt.write_bytes(b"mjlab-checkpoint-payload")
    (logs / "events.out.tfevents.mjlab").write_bytes(b"mjlab-tfevents-payload")

    policy_file = output / "policy.onnx"
    _write_valid_mlp_onnx(policy_file, obs_dim=4, act_dim=2)

    rel_ckpt = ckpt.resolve().relative_to((Path.cwd() / "logs").resolve()).as_posix()
    (output / "export.json").write_text(
        json.dumps(
            {
                "checkpoint": "checkpoints/" + rel_ckpt,
                "checkpointSha256": hashlib.sha256(ckpt.read_bytes()).hexdigest(),
                "policy": "policy.onnx",
                "policySha256": hashlib.sha256(policy_file.read_bytes()).hexdigest(),
                "iteration": args.max_iterations,
                "afterLearn": True,
                "externalData": [],
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
''',
        encoding="utf-8",
    )
    return script_path


def _import_cpu_profile(
    job_id: str, package: Path, plan: dict, capabilities: Path, gateway_url: str, tmp_path: Path
):
    measurement = tmp_path / f"measurement-{job_id}.json"
    measurement.write_text(
        json.dumps(
            {
                "schema": "lw.training-profile/1",
                "taskContentHash": content_hash(package),
                "hardware": "mjlab-cpu-contract-fixture",
                "resourceId": "cpu",
                **{key: plan[key] for key in ("algorithm", "model", "hyperparameters")},
                "samplesSeconds": [0.05],
                "setupSeconds": 0,
                "measuredAt": 10,
            }
        ),
        encoding="utf-8",
    )
    tool = Path(__file__).resolve().parents[2] / "tools/import_training_profile.py"
    imported = subprocess.run(
        [
            sys.executable,
            str(tool),
            "--job",
            job_id,
            "--resource",
            "cpu",
            "--measurement",
            str(measurement),
            "--capabilities",
            str(capabilities),
            "--url",
            gateway_url,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )
    assert imported.returncode == 0, imported.stdout + imported.stderr


def test_mjlab_node_job_runs_end_to_end_registers_policy_and_runs_cpu_inference(tmp_path: Path):
    package = _build_mjlab_task_package(tmp_path / "task")
    trainer = _write_fake_mjlab_trainer(tmp_path / "fake_mjlab_trainer.py")
    capabilities = tmp_path / "capabilities.json"
    capabilities.write_text(
        json.dumps(
            {
                "hardware": "mjlab-cpu-contract-fixture",
                "gpus": [],
                "cpuAvailable": True,
                "profiles": [],
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        node_trainer=(),
        node_mjlab_trainer=(sys.executable, str(trainer)),
        node_capabilities=capabilities,
        node_interval=0.2,
        node_job_timeout=30,
    )
    app, server, thread = _start_server(settings)
    try:
        with httpx.Client(base_url=settings.node_gateway_url, trust_env=False, timeout=10) as http:
            _wait_for(lambda: http.get("/api/v1/nodes").json(), lambda v: bool(v["nodes"]))
            pushed = http.post(
                "/api/v1/tasks",
                content=task_archive(package),
                headers={"Content-Type": "application/x-tar"},
            )
            assert pushed.status_code == 201, pushed.text
            task = pushed.json()

            plan_req = training_plan(
                task["id"],
                hyperparameters={"num_envs": 8, "max_iterations": 3, "device": "cpu", "learning_rate": 1e-3},
            )
            submitted = http.post("/api/v1/plans", json=plan_req).json()
            assert (
                http.post(
                    f"/api/v1/approvals/{submitted['approvalId']}/decision",
                    json={"decision": "approve"},
                ).status_code
                == 200
            )
            started = http.post("/api/v1/jobs", json={"planId": submitted["id"], "seed": 42}).json()
            job_id = started["jobId"]
            _import_cpu_profile(job_id, package, plan_req, capabilities, settings.node_gateway_url, tmp_path)

            settled = _wait_for(
                lambda: http.get(f"/api/v1/jobs/{job_id}").json(),
                lambda v: v["status"] in ("succeeded", "failed", "cancelled")
                and not http.get("/api/v1/nodes").json()["nodes"][0]["leases"],
            )
            assert settled["status"] == "succeeded", settled
            assert settled["metrics"]["iteration"] == 3
            assert settled["metrics"]["meanReward"] == 4.5
            logs = http.get(f"/api/v1/jobs/{job_id}/logs").json()
            assert any("mjlab fake trainer completed iterations" in line for line in logs["lines"])

            ckpt_entry = next(item["path"] for item in settled["artifacts"] if item["path"].endswith(".pt"))
            created_policy = http.post(
                "/api/v1/policies",
                json={"checkpoint": f"{job_id}/{ckpt_entry}"},
            )
            assert created_policy.status_code == 201, created_policy.text
            policy_doc = created_policy.json()
            assert policy_doc["versions"]["versions"]["mjlab"] == "1.3.0"

            _, frozen_pkg = app.state.policies.resolve(policy_doc["id"])
            spec = ObservationSpec.model_validate(document(frozen_pkg / "obs_spec.yaml"))
            cpu_policy = CpuPolicy(
                frozen_pkg / policy_doc["onnx"],
                spec,
                root=frozen_pkg / "artifacts",
            )
            action = cpu_policy.infer([0.1, -0.1, 0.2, -0.2])
            assert action.shape == (2,)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_mjlab_node_job_cancellation_reaps_descendants_and_unconfigured_runtime_fails_cleanly(tmp_path: Path):
    package = _build_mjlab_task_package(tmp_path / "task_cancel", name="mjlab-cancel-probe")
    trainer = _write_fake_mjlab_trainer(tmp_path / "fake_mjlab_trainer.py")
    capabilities = tmp_path / "capabilities.json"
    capabilities.write_text(
        json.dumps(
            {
                "hardware": "mjlab-cpu-contract-fixture",
                "gpus": [],
                "cpuAvailable": True,
                "profiles": [],
            }
        ),
        encoding="utf-8",
    )

    # 1. Node with only IsaacLab trainer configured fails cleanly on an mjlab job
    settings_unconf = Settings(
        node_trainer=(sys.executable, str(trainer)),
        node_mjlab_trainer=(),
        node_capabilities=capabilities,
        node_interval=0.2,
        node_job_timeout=30,
    )
    _, server1, thread1 = _start_server(settings_unconf)
    try:
        with httpx.Client(base_url=settings_unconf.node_gateway_url, trust_env=False, timeout=10) as http:
            _wait_for(lambda: http.get("/api/v1/nodes").json(), lambda v: bool(v["nodes"]))
            task = http.post(
                "/api/v1/tasks", content=task_archive(package), headers={"Content-Type": "application/x-tar"}
            ).json()
            plan_req = training_plan(
                task["id"], hyperparameters={"num_envs": 4, "max_iterations": 1, "device": "cpu"}
            )
            submitted = http.post("/api/v1/plans", json=plan_req).json()
            http.post(f"/api/v1/approvals/{submitted['approvalId']}/decision", json={"decision": "approve"})
            job = http.post("/api/v1/jobs", json={"planId": submitted["id"], "seed": 42}).json()
            _import_cpu_profile(
                job["jobId"], package, plan_req, capabilities, settings_unconf.node_gateway_url, tmp_path
            )
            failed = _wait_for(
                lambda: http.get(f"/api/v1/jobs/{job['jobId']}").json(),
                lambda v: v["status"] == "failed"
                and not http.get("/api/v1/nodes").json()["nodes"][0]["leases"],
            )
            assert "mjlab" in (failed["reason"] or "").lower()
    finally:
        server1.should_exit = True
        thread1.join(timeout=10)

    # 2. Running mjlab job with SIGTERM-ignoring descendant is cleanly cancelled and reaped
    capabilities2 = tmp_path / "capabilities2.json"
    capabilities2.write_text(
        json.dumps(
            {
                "hardware": "mjlab-cpu-contract-fixture",
                "gpus": [],
                "cpuAvailable": True,
                "profiles": [],
            }
        ),
        encoding="utf-8",
    )
    settings_cancel = Settings(
        state_root=tmp_path / "state2",
        node_trainer=(),
        node_mjlab_trainer=(sys.executable, str(trainer), "--hold"),
        node_capabilities=capabilities2,
        node_interval=0.1,
        node_job_timeout=30,
    )
    _, server2, thread2 = _start_server(settings_cancel)
    try:
        with httpx.Client(base_url=settings_cancel.node_gateway_url, trust_env=False, timeout=10) as http:
            _wait_for(lambda: http.get("/api/v1/nodes").json(), lambda v: bool(v["nodes"]))
            task = http.post(
                "/api/v1/tasks", content=task_archive(package), headers={"Content-Type": "application/x-tar"}
            ).json()
            plan_req = training_plan(
                task["id"], hyperparameters={"num_envs": 4, "max_iterations": 10, "device": "cpu"}
            )
            submitted = http.post("/api/v1/plans", json=plan_req).json()
            http.post(f"/api/v1/approvals/{submitted['approvalId']}/decision", json={"decision": "approve"})
            job = http.post("/api/v1/jobs", json={"planId": submitted["id"], "seed": 42}).json()
            job_id = job["jobId"]
            _import_cpu_profile(
                job_id, package, plan_req, capabilities2, settings_cancel.node_gateway_url, tmp_path
            )

            work_root = settings_cancel.state_root / "local-node/work"
            markers = _wait_for(lambda: list(work_root.glob("*/output/descendant.json")), bool)
            descendant = json.loads(markers[0].read_text(encoding="utf-8"))
            assert psutil.pid_exists(descendant["pid"])

            cancelled_resp = http.post(f"/api/v1/jobs/{job_id}/cancel")
            assert cancelled_resp.status_code == 200
            settled = _wait_for(
                lambda: http.get(f"/api/v1/jobs/{job_id}").json(),
                lambda v: v["status"] == "cancelled"
                and not http.get("/api/v1/nodes").json()["nodes"][0]["leases"],
            )
            assert settled["status"] == "cancelled"
            assert not psutil.pid_exists(descendant["pid"])
    finally:
        server2.should_exit = True
        thread2.join(timeout=10)

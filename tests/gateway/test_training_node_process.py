"""The real node/HTTP/process path, with fake CPU payloads and actual descendant ownership."""

import json
import socket
import sys
import threading
import time

import httpx
import psutil
import pytest
import uvicorn

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import content_hash
from packages.training.jobs import profile_key
from tests.fixtures.training_task import task_archive, task_directory, training_plan


def wait_for(read, predicate, *, timeout=30):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.05)
    raise AssertionError(f"Owned node journey did not settle: {value}")


def start_server(settings):
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


@pytest.mark.parametrize("lose_node_owner", [False, True])
def test_real_local_node_installs_inputs_once_and_reaps_resistant_descendants(tmp_path, lose_node_owner):
    package = task_directory(tmp_path / "task")
    (package / "probe.py").write_text('def register(): return "installed-source-marker"\n', encoding="utf-8")
    plan = training_plan(hyperparameters={"num_envs": 4, "max_iterations": 1, "device": "cpu"})
    capabilities = tmp_path / "capabilities.json"
    capabilities.write_text(
        json.dumps(
            {
                "hardware": "explicit CPU contract fixture",
                "gpus": [],
                "cpuAvailable": True,
                "profiles": [
                    {
                        "key": profile_key(
                            plan, content_hash(package), "explicit CPU contract fixture", "cpu"
                        ),
                        "iterationSeconds": 0.05,
                        "setupSeconds": 0,
                        "samples": 1,
                        "measuredAt": 10,
                        "evidence": "Controlled fixture data; not a performance measurement",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    from pathlib import Path

    trainer = Path(__file__).resolve().parents[1] / "fixtures/fake_trainer.py"
    settings = Settings(
        node_trainer=(sys.executable, str(trainer), *(("--hold",) if lose_node_owner else ())),
        node_capabilities=capabilities,
        node_interval=0.1,
        node_job_timeout=30,
    )
    app, server, thread = start_server(settings)
    identity = None
    try:
        with httpx.Client(base_url=settings.node_gateway_url, trust_env=False, timeout=10) as http:
            ready = wait_for(lambda: http.get("/api/v1/nodes").json(), lambda value: bool(value["nodes"]))
            assert ready["nodes"][0]["gpus"] == []
            assert (
                http.post(
                    "/api/v1/tasks",
                    content=task_archive(package),
                    headers={"Content-Type": "application/x-tar"},
                ).status_code
                == 201
            )
            submitted = http.post("/api/v1/plans", json=plan).json()
            assert (
                http.post(
                    f"/api/v1/approvals/{submitted['approvalId']}/decision", json={"decision": "approve"}
                ).status_code
                == 200
            )
            started = http.post("/api/v1/jobs", json={"planId": submitted["id"], "seed": 42}).json()
            identity = started["jobId"]
            assert started["gpuId"] is None and started["estimatedGpuHours"] == 0
            root = settings.state_root / "local-node/work"
            def started_marker():
                state = http.get(f"/api/v1/jobs/{identity}").json()
                assert state["status"] not in ("failed", "cancelled"), state
                return list(root.glob("*/output/descendant.json"))
            marker = wait_for(started_marker, bool)
            descendant = json.loads(marker[0].read_text(encoding="utf-8"))
            if lose_node_owner:
                owner = app.state.jobs.node.process
                owner.kill()
                owner.wait(timeout=5)
                # The node may be lost, but its lease cannot turn idle before descendant cleanup.
                assert http.get("/api/v1/nodes").json()["nodes"][0]["leases"]
                app.state.jobs.node.start()
            finished = wait_for(
                lambda: http.get(f"/api/v1/jobs/{identity}").json(),
                lambda value: value["status"] in ("succeeded", "failed"),
            )
            assert finished["status"] == ("failed" if lose_node_owner else "succeeded"), finished
            wait_for(lambda: http.get("/api/v1/nodes").json(), lambda value: not value["nodes"][0]["leases"])
            try:
                process = psutil.Process(descendant["pid"])
                assert process.create_time() != descendant["createdAt"] or not process.is_running()
            except psutil.NoSuchProcess:
                pass
            assert len((marker[0].parent / "actual-starts.txt").read_text().splitlines()) == 1
            assert finished["chargedGpuHours"] == 0 and finished["gpuId"] is None
            assert http.get("/api/v1/health").json()["sessionCount"] == 0
            if not lose_node_owner:
                assert {row["path"] for row in finished["artifacts"]} >= {
                    "versions.json",
                    "stdout.log",
                    "stderr.log",
                    "policy.onnx",
                }
                assert "Fake trainer" in "\n".join(http.get(f"/api/v1/jobs/{identity}/logs").json()["lines"])
    finally:
        if identity:
            with httpx.Client(base_url=settings.node_gateway_url, trust_env=False, timeout=5) as http:
                http.post(f"/api/v1/jobs/{identity}/cancel")
        server.should_exit = True
        stop = settings.state_root / "local-node/stop"
        if stop.parent.exists():
            stop.write_text("owned test teardown", encoding="utf-8")
        thread.join(timeout=15)
        assert not thread.is_alive()
        owner = app.state.jobs.node.process
        if owner:
            owner.wait(timeout=5)

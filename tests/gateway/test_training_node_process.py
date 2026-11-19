"""The real node/HTTP/process path, with fake CPU payloads and actual descendant ownership."""

import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import psutil
import pytest
import uvicorn
from fastapi import Response
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.node import runner
from apps.node.process import OwnedProcess
from packages.library.common import content_hash
from packages.training.jobs import profile_key
from packages.training.runtime import entry_identity
from tests.fixtures.training_task import task_archive, task_directory, training_plan
from tests.gateway.test_remote_training_nodes import (
    ISAAC_VERSIONS,
    OPERATOR,
    node_profile_key,
    register,
    remote_settings,
)
from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, heartbeat, setup_plan


def wait_for(read, predicate, *, timeout=30):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.05)
    raise AssertionError(f"Owned node journey did not settle: {value}")


def start_server(settings, app=None):
    if app is None:
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


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX process ownership")
@pytest.mark.parametrize("recovery", ["missing-work", "retained-proof", "expires-after-upgrade"])
def test_upgraded_local_runner_reconciles_retained_lease_without_relaunch(
    monkeypatch, recovery,
):
    settings = Settings(node_token=TOKEN)
    old = create_app(settings)
    old.state.jobs.clock = lambda: 100
    with TestClient(old) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        body["hardware"] = "a" * 64
        gpu = body["gpus"][0]
        body["profiles"][0]["key"] = profile_key(plan, task["contentHash"], body["hardware"], gpu["id"])
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        request = {"planId": plan["id"], "seed": 42}
        job = client.post("/api/v1/jobs", json=request).json()
        assert job["leaseId"]

    root = settings.state_root / "local-node"
    root.mkdir()
    (root / "instance-id").write_text(NODE["X-Node-Instance"], encoding="utf-8")
    (root / "capabilities.json").write_text(json.dumps({"hardware": body["hardware"], "profiles": []}))
    work = root / "work" / runner.work_directory_id(job["jobId"], job["leaseId"])
    owner = None
    if recovery != "missing-work":
        work.mkdir(parents=True)
        program = "import time; time.sleep(30)" if recovery == "expires-after-upgrade" else "pass"
        with (work / "stdout.log").open("wb") as log:
            owner = OwnedProcess(
                [sys.executable, "-c", program], cwd=work,
                env={"PATH": ""}, stdout=log, stderr=log,
            )
            if recovery == "retained-proof":
                try:
                    assert owner.process.wait(timeout=5) == 0
                finally:
                    owner.close()
        # Cleanup must not depend on draining ordinary reports from the expired lease.
        (work / "stdout.log").write_text("old line\n" * 501, encoding="utf-8")
    hardware = "node-hardware/2:fixture"
    capabilities = {"hardware": hardware, "legacyHardware": body["hardware"], "gpus": [gpu], "profiles": []}
    monkeypatch.setattr(runner, "discover", lambda: capabilities)
    monkeypatch.setattr(runner, "gpu_usage", lambda: ({}, {}))
    monkeypatch.setenv("SPATIAL_NODE_TOKEN", TOKEN)
    gateway = create_app(settings)
    now = [200]
    gateway.state.jobs.clock = lambda: now[0]
    reconciling = threading.Event()

    @gateway.middleware("http")
    async def observe_reconciliation(request, call_next):
        if request.url.path == HEARTBEAT:
            payload = await request.json()
            if any(
                report["jobId"] == job["jobId"] and report.get("reason") == "owner_reconciliation_required"
                for report in payload.get("reports", [])
            ):
                reconciling.set()
        return await call_next(request)

    _, server, server_thread = start_server(settings, gateway)
    args = SimpleNamespace(
        url=settings.node_gateway_url, root=str(root), node_id=None, registration_code=None,
        trainer="[]", mjlab_trainer="[]", capabilities=None, interval=0.05, timeout=5,
    )
    thread = threading.Thread(target=runner.run_node, args=(args,))
    try:
        if recovery != "expires-after-upgrade":
            now[0] += 61
        thread.start()
        with httpx.Client(base_url=settings.node_gateway_url, trust_env=False) as client:
            def read():
                return client.get("/api/v1/nodes/local").json()

            actual = wait_for(read, lambda node: node["hardware"] == hardware)
            if recovery == "expires-after-upgrade":
                assert reconciling.wait(5)
                assert owner.process.poll() is None
                now[0] += 61
                wait_for(read, lambda node: node["leases"][gpu["id"]]["quarantined"])
                owner.close()
                wait_for(read, lambda node: not node["leases"])
            elif recovery == "retained-proof":
                wait_for(read, lambda node: not node["leases"])
            else:
                assert actual["leases"][gpu["id"]]["leaseId"] == job["leaseId"]
                assert actual["leases"][gpu["id"]]["quarantined"]
                assert reconciling.wait(5)
                assert not work.exists()
    finally:
        if owner is not None and owner.process.poll() is None:
            owner.close()
        (root / "stop").touch()
        if thread.ident is not None:
            thread.join(10)
        server.should_exit = True
        server_thread.join(10)
    assert not thread.is_alive() and not server_thread.is_alive()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX descendant ownership")
@pytest.mark.parametrize("launch_failure", [None, "enoent", "enoexec"],
                         ids=["descendants-exited", "enoent", "enoexec"])
def test_remote_node_retries_cleanup_after_reply_is_lost(tmp_path, monkeypatch, launch_failure):
    monkeypatch.setattr(runner, "gpu_usage", lambda: ({}, {}))
    if launch_failure:
        search = tmp_path / "isolated-bin"
        search.mkdir()
        if launch_failure == "enoexec":
            invalid = search / "uv"
            invalid.write_bytes(b"not a binary or shebang")
            invalid.chmod(0o700)
        monkeypatch.setenv("PATH", str(search))
    settings = remote_settings()
    app = create_app(settings)
    root = tmp_path / "workstation"
    verified = []
    revoked = threading.Event()

    @app.middleware("http")
    async def lose_first_cleanup_reply(request, call_next):
        if launch_failure and request.url.path.endswith("/heartbeat"):
            payload = await request.json()
            if any(report["status"] == "failed" for report in payload.get("reports", [])):
                await asyncio.to_thread(revoked.wait, 10)
        response = await call_next(request)
        if request.url.path == "/api/v1/nodes/cleanup" and response.status_code == 204:
            if launch_failure:
                (marker,) = (root / "work").glob("*/clean-*")
                assert marker.read_text() == "not_started"
                assert not list((root / "work").glob("*/leaf.json"))
            else:
                (leaf_file,) = (root / "work").glob("*/leaf.json")
                leaf = json.loads(leaf_file.read_bytes())
                try:
                    process = psutil.Process(leaf["pid"])
                    assert process.create_time() != leaf["createdAt"] or not process.is_running()
                except psutil.NoSuchProcess:
                    pass
            assert "x-node-token" not in request.headers
            verified.append(response.status_code)
            if len(verified) == 1:
                return Response(status_code=503)
        return response

    _, server, server_thread = start_server(settings, app)
    owner_thread = None
    errors = []

    def start_owner(node):
        def run():
            try:
                asyncio.run(node.run())
            except RuntimeError as error:
                errors.append(str(error))

        thread = threading.Thread(target=run)
        thread.start()
        return thread

    try:
        with httpx.Client(base_url=settings.node_gateway_url, headers=OPERATOR, trust_env=False) as client:
            scope = register(client, "worker", colocated=True)
            package = task_directory(tmp_path / "task")
            task = client.post("/api/v1/tasks", content=task_archive(package),
                               headers={"Content-Type": "application/x-tar"}).json()
            plan = client.post("/api/v1/plans", json=training_plan(task["id"])).json()
            approval_url = f"/api/v1/approvals/{plan['approvalId']}/decision"
            assert client.post(approval_url, json={"decision": "approve"}).is_success
            gpu = {"id": "GPU-fictional", "name": "CPU process fixture"}
            holder = tmp_path / "hold.py"
            fixture = Path(__file__).resolve().parents[1] / "fixtures/owned_process_tree.py"
            holder.write_text(
                "import os,sys\n"
                f"args = [sys.executable, {str(fixture)!r}, 'trainer', os.getcwd(), '--hold']\n"
                "os.execv(sys.executable, args)\n",
                encoding="utf-8",
            )
            hardware = {
                "id": "worker", "hardware": "fixture", "trainerVersions": ISAAC_VERSIONS,
                "trainingEntries": {"isaaclab": entry_identity("isaaclab", [str(holder)])},
            }
            profile = node_profile_key(plan, task["contentHash"], hardware, gpu, backend="isaaclab")
            capabilities = {key: value for key, value in hardware.items() if key != "id"}
            capabilities.update(gpus=[gpu], profiles=[{
                "key": profile, "iterationSeconds": 1.0, "samples": 1,
                "measuredAt": time.time(), "evidence": "CPU process fixture",
            }])

            def node():
                return runner.LocalNode(
                    settings.node_gateway_url, root, (sys.executable, str(holder)), capabilities,
                    interval=0.1, timeout=40, node_id="worker", token=scope["X-Node-Token"],
                )

            owner_thread = start_owner(node())
            assert client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).status_code == 202
            ready = "*/clean-*" if launch_failure else "*/leaf.json"
            wait_for(lambda: list((root / "work").glob(ready)), bool, timeout=35)
            assert not verified
            live = client.get("/api/v1/nodes/worker").json()["leases"]
            assert gpu["id"] in live
            assert client.delete("/api/v1/nodes/worker").status_code == 204
            revoked.set()
            owner_thread.join(10)
            assert not owner_thread.is_alive()
            assert verified == [204]
            assert errors == ["Gateway rejected Compute Node credentials or ownership"]
            (receipt,) = (root / "cleanup-receipts").glob("*.json")

            # This invocation replays the original instance's proof before it attempts a heartbeat.
            (root / "stop").touch()
            owner_thread = start_owner(node())
            owner_thread.join(5)
            assert not owner_thread.is_alive()
            assert verified == [204, 204] and not receipt.exists()
    finally:
        revoked.set()
        (root / "stop").parent.mkdir(parents=True, exist_ok=True)
        (root / "stop").touch()
        if owner_thread:
            owner_thread.join(12)
        server.should_exit = True
        server_thread.join(10)


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
                "profiles": [],
            }
        ),
        encoding="utf-8",
    )
    trainer = Path(__file__).resolve().parents[1] / "fixtures/fake_trainer.py"
    settings = Settings(
        node_trainer=(sys.executable, str(trainer), *(("--hold",) if lose_node_owner else ())),
        node_capabilities=capabilities,
        node_interval=0.1 if lose_node_owner else 1,
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
            assert started["reason"] == "estimate_unavailable" and started["estimatedGpuHours"] is None
            assert started["leaseId"] is None
            measurement = tmp_path / "measurement.json"
            measurement.write_text(
                json.dumps(
                    {
                        "schema": "lw.training-profile/1",
                        "taskContentHash": content_hash(package),
                        "hardware": "explicit CPU contract fixture",
                        "resourceId": "cpu",
                        **{key: plan[key] for key in ("algorithm", "model", "hyperparameters")},
                        "samplesSeconds": [0.05],
                        "setupSeconds": 0,
                        "measuredAt": 10,
                    }
                ),
                encoding="utf-8",
            )
            imported = subprocess.run(
                [
                    sys.executable,
                    str(trainer.parents[2] / "tools/import_training_profile.py"),
                    "--job",
                    identity,
                    "--resource",
                    "cpu",
                    "--measurement",
                    str(measurement),
                    "--capabilities",
                    str(capabilities),
                    "--url",
                    settings.node_gateway_url,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            assert imported.returncode == 0, imported.stdout + imported.stderr
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
            else:
                draining = wait_for(
                    lambda: http.get(f"/api/v1/jobs/{identity}").json(),
                    lambda value: value["reason"] == "draining_logs",
                )
                assert draining["status"] == "running"
                assert http.get("/api/v1/nodes").json()["nodes"][0]["leases"]
                assert not http.get(f"/api/v1/jobs/{identity}/logs").json()["complete"]
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
                cursor, lines = 0, []
                while True:
                    chunk = http.get(f"/api/v1/jobs/{identity}/logs", params={"after": cursor}).json()
                    lines.extend(chunk["lines"])
                    cursor = chunk["cursor"]
                    if chunk["complete"]:
                        break
                numbers = [int(line.split("LOGTAIL_")[1]) for line in lines if "LOGTAIL_" in line]
                assert numbers == list(range(1201))
                assert finished["versions"]["hardware"]["gpu"] is None
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

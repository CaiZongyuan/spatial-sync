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
@pytest.mark.parametrize(
    "recovery",
    ["missing-work", "retained-proof", "retained-zero", "expires-after-upgrade", "normal-finish",
     "retained-normal-finish", "modern-expired", "modern-no-proof"],
)
def test_upgraded_local_runner_reconciles_retained_lease_without_relaunch(
    monkeypatch, recovery,
):
    normal_finish = recovery.endswith("normal-finish")
    retry_tail = recovery in ("retained-proof", "retained-zero", "modern-expired")
    observed_metrics = {"iteration": 4, "meanReward": 2.5, "trainSuccess": 0.75, "memoryUsedMb": 128}
    if recovery == "retained-zero":
        observed_metrics.update(iteration=0, meanReward=0, trainSuccess=0)
    settings = Settings(node_token=TOKEN)
    old = create_app(settings)
    old.state.jobs.clock = lambda: 100
    with TestClient(old) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        modern = recovery == "normal-finish" or recovery.startswith("modern-")
        body["hardware"] = "node-hardware/2:fixture" if modern else "a" * 64
        gpu = body["gpus"][0]
        if modern:
            body.update(trainerVersions=ISAAC_VERSIONS, trainingEntries={"isaaclab": "isaaclab:cli"})
            identity = {"id": "local", **body}
            key = node_profile_key(plan, task["contentHash"], identity, gpu, backend="isaaclab")
        else:
            key = profile_key(plan, task["contentHash"], body["hardware"], gpu["id"])
        body["profiles"][0]["key"] = key
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        request = {"planId": plan["id"], "seed": 42}
        job = client.post("/api/v1/jobs", json=request).json()
        assert job["leaseId"]
        progress = {
            "jobId": job["jobId"], "leaseId": job["leaseId"], "status": "running",
            **observed_metrics,
        }
        assert client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [progress]}).is_success
        if normal_finish:
            assert client.post(f"/api/v1/jobs/{job['jobId']}/cancel").is_success

    root = settings.state_root / "local-node"
    root.mkdir()
    (root / "instance-id").write_text(NODE["X-Node-Instance"], encoding="utf-8")
    (root / "capabilities.json").write_text(json.dumps({"hardware": body["hardware"], "profiles": []}))
    work = root / "work" / runner.work_directory_id(job["jobId"], job["leaseId"])
    owner = None
    missing_proof = recovery in ("missing-work", "modern-no-proof")
    if not missing_proof:
        work.mkdir(parents=True)
        if normal_finish:
            result = {"status": "cancelled", "reason": "owned cancellation"}
            (work / "result.json").write_text(json.dumps(result), encoding="utf-8")
            output = work / "output"
            output.mkdir()
            (output / "metrics.jsonl").write_text(json.dumps({
                "iteration": 4, "meanReward": 2.5, "trainSuccess": 0.75,
            }) + "\n")
        program = (
            "import sys; sys.stdout.write(''.join(f'owned line {i}\\n' for i in range(500)) + 'owned tail');"
        )
        if recovery == "expires-after-upgrade":
            program += "sys.stdout.flush(); import time; time.sleep(30)"
        with (work / "stdout.log").open("wb") as log:
            owner = OwnedProcess(
                [sys.executable, "-c", program], cwd=work,
                env={"PATH": ""}, stdout=log, stderr=log,
            )
            if normal_finish or retry_tail:
                try:
                    assert owner.process.wait(timeout=5) == 0
                finally:
                    owner.close()
    hardware = "node-hardware/2:fixture"
    capabilities = {"hardware": hardware, "gpus": [gpu], "profiles": []}
    if not modern:
        capabilities["legacyHardware"] = body["hardware"]
    monkeypatch.setattr(runner, "discover", lambda: capabilities)
    monkeypatch.setattr(runner, "gpu_usage", lambda: ({}, {}))
    monkeypatch.setenv("SPATIAL_NODE_TOKEN", TOKEN)
    gateway = create_app(settings)
    now = [200]
    gateway.state.jobs.clock = lambda: now[0]
    reconciling = threading.Event()
    permit_terminal = threading.Event()
    committed_tail = {}
    allow_tail = False
    attempts = []

    async def observe_reconciliation(request, call_next):
        if request.url.path == HEARTBEAT:
            payload = await request.json()
            reports = payload.get("reports", [])
            if normal_finish and any(report["status"] == "cancelled" for report in reports):
                await asyncio.to_thread(permit_terminal.wait, 5)
            if any(
                report["jobId"] == job["jobId"] and report.get("reason") == "owner_reconciliation_required"
                for report in payload.get("reports", [])
            ):
                reconciling.set()
            original_instance = request.headers.get("x-node-instance") == NODE["X-Node-Instance"]
            if retry_tail and original_instance:
                tail = next((report for report in payload.get("reports", []) if "tailEnd" in report), None)
                if tail and tail["tailEnd"] == 501:
                    if not committed_tail:
                        response = await call_next(request)
                        assert response.status_code == 200
                        committed_tail.update(tail)
                        return Response(status_code=503)
                    if not allow_tail:
                        return Response(status_code=503)
                    attempts.append(tail["logAfter"])
        return await call_next(request)

    gateway.middleware("http")(observe_reconciliation)
    _, server, server_thread = start_server(settings, gateway)
    args = SimpleNamespace(
        url=settings.node_gateway_url, root=str(root), node_id=None, registration_code=None,
        trainer="[]", mjlab_trainer="[]", capabilities=None, interval=0.05, timeout=5,
    )
    thread = threading.Thread(target=runner.run_node, args=(args,))
    try:
        if recovery != "expires-after-upgrade" and not normal_finish:
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
            elif normal_finish:
                job_url = f"/api/v1/jobs/{job['jobId']}"
                draining = wait_for(
                    lambda: client.get(job_url).json(), lambda value: value["reason"] == "draining_logs",
                )
                assert draining["metrics"]["memoryUsedMb"] == 128
                first = client.get(job_url + "/logs").json()
                assert first["cursor"] == 500 and not first["complete"]
                permit_terminal.set()
                wait_for(read, lambda node: not node["leases"], timeout=7)
            elif retry_tail:
                wait_for(read, lambda node: not node["leases"], timeout=7)
            else:
                actual = wait_for(read, lambda node: node["leases"][gpu["id"]]["quarantined"])
                assert actual["leases"][gpu["id"]]["leaseId"] == job["leaseId"]
                now[0] += 1
                wait_for(read, lambda node: node["lastSeen"] == now[0])
                assert not work.exists()
                if modern:
                    proofless = {
                        "jobId": job["jobId"], "leaseId": job["leaseId"], "status": "cleaned",
                    }
                    packet = {"instanceId": NODE["X-Node-Instance"], **capabilities}
                    for report in (proofless, progress):
                        reply = client.post(HEARTBEAT, headers=NODE, json=packet | {"reports": [report]})
                        assert reply.status_code == 403
                    assert read()["leases"][gpu["id"]]["quarantined"]
            if not missing_proof:
                url = f"/api/v1/jobs/{job['jobId']}/logs"

                if retry_tail:
                    assert committed_tail["logAfter"] == 0 and len(committed_tail["logs"]) == 500
                    assert client.get(url).json()["cursor"] == 500
                    assert client.get(url, params={"after": 500}).json() == {
                        "lines": [], "cursor": 500, "complete": False,
                    }
                    (root / "stop").touch()
                    thread.join(10)
                    assert not thread.is_alive()
                    server.should_exit = True
                    server_thread.join(10)
                    assert not server_thread.is_alive()
                    restarted = create_app(settings)
                    restarted.state.jobs.clock = lambda: now[0]
                    restarted.middleware("http")(observe_reconciliation)
                    _, server, server_thread = start_server(settings, restarted)
                    client.base_url = settings.node_gateway_url
                    fenced = NODE | {"X-Lease-Id": job["leaseId"]}
                    job_url = f"/api/v1/jobs/{job['jobId']}"
                    assert client.get(job_url + "/inputs", headers=fenced).status_code == 403
                    assert client.post(job_url + "/artifacts", headers=fenced).status_code == 403
                    assert client.post(
                        HEARTBEAT, headers=NODE | {"X-Node-Instance": "wrong"},
                        json=capabilities | {"instanceId": "wrong", "reports": [committed_tail]},
                    ).status_code == 403
                    ordinary = {"jobId": job["jobId"], "leaseId": job["leaseId"], "status": "running"}
                    packet = capabilities | {"instanceId": NODE["X-Node-Instance"]}
                    assert client.post(
                        HEARTBEAT, headers=NODE, json=packet | {"reports": [ordinary]},
                    ).status_code == 403
                    wrong_end = committed_tail | {"tailEnd": 502}
                    assert client.post(
                        HEARTBEAT, headers=NODE, json=packet | {"reports": [wrong_end]},
                    ).status_code == 403
                    allow_tail = True
                    (root / "stop").unlink()
                    args.url = settings.node_gateway_url
                    thread = threading.Thread(target=runner.run_node, args=(args,))
                    thread.start()

                def completed_logs():
                    result = client.get(url, params={"after": 500})
                    return result.json() if result.is_success else {}

                last = wait_for(completed_logs, lambda value: value.get("complete"), timeout=4)
                assert last == {"lines": ["[stdout.log] owned tail"], "cursor": 501, "complete": True}
                first = client.get(url).json()
                assert first["lines"] == [f"[stdout.log] owned line {i}" for i in range(500)]
                assert first["cursor"] == 500 and not first["complete"]
                if retry_tail:
                    assert attempts[:2] == [0, 500]
                settled = client.get(f"/api/v1/jobs/{job['jobId']}").json()
                assert settled["status"] == ("cancelled" if normal_finish else "failed")
                assert settled["reason"] == ("owned cancellation" if normal_finish else "node_lost")
                assert settled["metrics"] | observed_metrics == settled["metrics"]
                if recovery == "retained-zero":
                    history = settled["metricsHistory"]
                    assert history[0] | observed_metrics == history[0]
                    assert history[-1]["reportedStatus"] == "cleaned"
                    assert "iteration" not in committed_tail and "iteration" not in history[-1]
    finally:
        permit_terminal.set()
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

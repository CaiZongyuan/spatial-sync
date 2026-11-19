"""Real loopback heartbeats and owned CPU payloads on two simulated devices."""

import asyncio
import os
import sys
import threading

import httpx
import pytest

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.node import runner
from tests.fixtures.training_task import task_archive, training_plan
from tests.gateway.test_mjlab_node_jobs import _build_mjlab_task_package
from tests.gateway.test_remote_training_nodes import (
    COMPILER,
    MJLAB_VERSIONS,
    OPERATOR,
    node_profile_key,
    register,
    remote_settings,
)
from tests.gateway.test_training_jobs import TOKEN
from tests.gateway.test_training_node_process import start_server, wait_for


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX process ownership")
@pytest.mark.parametrize("remote", [False, True], ids=["local", "named"])
def test_expired_validation_keeps_other_owned_job_running(tmp_path, monkeypatch, remote):
    # Exercise the real runner and owned process lifecycles without installing a trainer.
    payload = tmp_path / "payload.py"
    payload.write_text(
        "import json,time\n"
        "from pathlib import Path\n"
        "for iteration in range(1, 1000):\n"
        "    metrics = json.dumps({'iteration': iteration, 'meanReward': 2.5})\n"
        "    Path('output/metrics.jsonl').write_text(metrics+'\\n')\n"
        "    print(f'owned iteration {iteration}', flush=True)\n"
        "    time.sleep(.08)\n",
        encoding="utf-8",
    )
    permit_validation_cleanup = threading.Event()

    class CpuPayloadRun(runner.OwnedRun):
        def run(self):
            self.root.mkdir(parents=True, exist_ok=False)
            (self.root / "output").mkdir()
            try:
                with (self.root / "stdout.log").open("wb") as log:
                    self._command([sys.executable, str(payload)], dict(os.environ), log, log)
            except InterruptedError:
                self.status = "cancelled"
            finally:
                if self.assignment["gpuId"] == "card-a":
                    permit_validation_cleanup.wait(7)
                self.finished = runner.time.monotonic()

    monkeypatch.setattr(runner, "OwnedRun", CpuPayloadRun)
    telemetry = [50.0]

    def usage():
        telemetry[0] = (telemetry[0] + 1) % 100
        return {"card-b": {"utilization": telemetry[0], "memoryUsedMb": 128.0}}, {}

    monkeypatch.setattr(runner, "gpu_usage", usage)
    settings = remote_settings() if remote else Settings(node_token=TOKEN)
    app = create_app(settings)
    clock = [100.0]
    app.state.jobs.clock = lambda: clock[0]
    _, server, server_thread = start_server(settings, app)
    root = tmp_path / "worker"
    thread = None
    errors = []
    node_id = "worker" if remote else "local"
    operator = OPERATOR if remote else {}
    path = f"/api/v1/nodes/{node_id}/heartbeat"
    try:
        with httpx.Client(base_url=settings.node_gateway_url, headers=operator, trust_env=False) as client:
            credentials = register(client, node_id)["X-Node-Token"] if remote else TOKEN
            task = client.post(
                "/api/v1/tasks", headers={"Content-Type": "application/x-tar"},
                content=task_archive(_build_mjlab_task_package(tmp_path / "task")),
            ).raise_for_status().json()
            plan = client.post("/api/v1/plans", json=training_plan(
                task["id"], algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
            )).raise_for_status().json()
            raw_gpu = {"id": "card-a", "name": "Fixture GPU", "memoryTotalMb": 2048.0}
            capabilities = {
                "hardware": "node-hardware/2:fixture", "gpus": [raw_gpu, raw_gpu | {"id": "card-b"}],
                "compilerHash": COMPILER, "trainerVersions": MJLAB_VERSIONS,
                "trainingEntries": {"mjlab": "mjlab:managed"},
            }
            identity = {"id": node_id, **capabilities}
            profile_key = node_profile_key(plan, task["contentHash"], identity, raw_gpu, backend="mjlab")
            capabilities["profiles"] = [{
                "key": profile_key, "iterationSeconds": 1, "samples": 1,
                "measuredAt": 50, "evidence": "fixture",
            }]
            node = runner.LocalNode(settings.node_gateway_url, root, (), capabilities, interval=0.05,
                                    node_id=node_id, token=credentials)
            client.post(path, headers=node.headers, json={
                **capabilities, "instanceId": node.instance,
            }).raise_for_status()
            decision = f"/api/v1/approvals/{plan['approvalId']}/decision"
            client.post(decision, json={"decision": "approve"}).raise_for_status()
            requested = {"planId": plan["id"], "seed": 42, "targetNodeId": node_id}
            preflight = client.post("/api/v1/jobs", json=requested | {"kind": "plan_validation"}).json()
            training = client.post("/api/v1/jobs", json=requested).json()
            assert (preflight["gpuId"], training["gpuId"]) == ("card-a", "card-b")

            def run_node():
                try:
                    asyncio.run(node.run())
                except RuntimeError as error:
                    errors.append(str(error))

            def gpu_node():
                return client.get(f"/api/v1/nodes/{node_id}").raise_for_status().json()

            def running_job():
                return client.get(f"/api/v1/jobs/{training['jobId']}").raise_for_status().json()

            thread = threading.Thread(target=run_node)
            thread.start()
            wait_for(running_job, lambda job: job["metrics"].get("iteration", 0) >= 2, timeout=5)
            for timestamp in (150.0, 200.0, 250.0, 280.0):
                clock[0] = timestamp
                wait_for(gpu_node, lambda value: value["lastSeen"] == timestamp, timeout=4)
            iteration = running_job()["metrics"]["iteration"]
            clock[0] = 281.0
            wait_for(
                running_job,
                lambda job: job["status"] == "running" and job["metrics"].get("iteration", 0) > iteration + 3,
                timeout=5,
            )
            result = client.get(f"/api/v1/jobs/{preflight['jobId']}").json()
            assert (result["status"], result["reason"]) == ("failed", "validation_timeout")
            during_cleanup = gpu_node()
            assert "card-a" in during_cleanup["leases"] or during_cleanup["quarantinedGpus"]
            assert during_cleanup["gpus"][1]["memoryUsedMb"] == 128.0
            permit_validation_cleanup.set()
            def expired_card_released(value):
                return "card-a" not in value["leases"] and not value["quarantinedGpus"]

            released = wait_for(gpu_node, expired_card_released, timeout=5)
            assert released["lastSeen"] == 281
            assert released["leases"]["card-b"]["leaseId"] == training["leaseId"]
            assert released["gpus"][1]["memoryUsedMb"] == 128.0

            expired = node.headers | {"X-Lease-Id": preflight["leaseId"]}
            inputs = f"/api/v1/jobs/{preflight['jobId']}/inputs"
            assert client.get(inputs, headers=expired).status_code == 403
            assert not errors and thread.is_alive()
            if remote:
                assert client.delete(f"/api/v1/nodes/{node_id}").status_code == 204
                thread.join(5)
                assert not thread.is_alive()
                assert errors == ["Gateway rejected Compute Node credentials or ownership"]
    finally:
        permit_validation_cleanup.set()
        root.mkdir(exist_ok=True)
        (root / "stop").touch()
        if thread:
            thread.join(8)
        server.should_exit = True
        server_thread.join(8)
    assert not thread or not thread.is_alive()
    assert not server_thread.is_alive()

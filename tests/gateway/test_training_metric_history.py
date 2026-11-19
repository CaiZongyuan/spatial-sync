"""Accepted heartbeat observations remain real, bounded and available after restart."""

import json

import pytest
from fastapi.testclient import TestClient
from test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, heartbeat, setup_plan

from apps.api.app.main import create_app
from apps.api.app.settings import Settings


@pytest.mark.parametrize("measured_elapsed", [None, 36], ids=["unmeasured", "retained-charge"])
def test_node_report_omissions_preserve_missing_samples_and_billing(measured_elapsed):
    settings = Settings(node_token=TOKEN)
    with TestClient(create_app(settings)) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task)).is_success
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert job["leaseId"]
        scope = {"jobId": job["jobId"], "leaseId": job["leaseId"]}
        url = f"/api/v1/jobs/{job['jobId']}"
        observation = {"elapsedSeconds": measured_elapsed} if measured_elapsed is not None else {}
        first = scope | observation | {"status": "running", "logs": ["first"]}
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [first])).is_success
        running = client.get(url).json()
        if measured_elapsed is not None:
            earlier = first | {"elapsedSeconds": 0}
            reply = client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [earlier]))
            assert reply.status_code == 422
        terminal = scope | {"status": "failed", "reason": "owned exit", "logAfter": 1, "logs": ["last"]}
        packet = heartbeat(plan, task, [terminal])
        assert client.post(HEARTBEAT, headers=NODE, json=packet).is_success
        settled = client.get(url).json()
        assert client.get(url + "/logs").json() == {"lines": ["first", "last"], "cursor": 2, "complete": True}
        assert settled["status"] == "failed" and settled["reason"] == "owned exit"
        assert settled["chargedGpuHours"] == (measured_elapsed or 0) / 3600
        history = settled["metricsHistory"]
        assert len(history) == 2
        for metrics in (running["metrics"], settled["metrics"], history[0]):
            reported = {key: metrics[key] for key in ("iteration", "elapsedSeconds") if key in metrics}
            assert reported == observation
        assert "iteration" not in history[-1] and "elapsedSeconds" not in history[-1]
    if measured_elapsed is not None:
        path = settings.state_root / "jobs.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved["jobs"][job["jobId"]]["lastNodeReport"].update(iteration=0, elapsedSeconds=0)
        path.write_text(json.dumps(saved), encoding="utf-8")
    with TestClient(create_app(settings)) as restarted:
        assert restarted.post(HEARTBEAT, headers=NODE, json=packet).is_success
        changed = heartbeat(plan, task, [terminal | {"logs": ["different"]}])
        assert restarted.post(HEARTBEAT, headers=NODE, json=changed).status_code == 403
        assert restarted.get(url).json()["metricsHistory"] == history


def test_accepted_metrics_preserve_zero_null_terminal_and_reject_invalid_lease_without_a_point():
    settings = Settings(node_token=TOKEN)
    app = create_app(settings)
    now = [100.0]
    app.state.jobs.clock = lambda: now[0]
    with TestClient(app) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task))
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        identity = job["jobId"]
        assert job["metricsHistory"] == []
        report = {"jobId": identity, "leaseId": job["leaseId"], "status": "running", "iteration": 1,
                  "meanReward": 0.0, "trainSuccess": None, "elapsedSeconds": 1}
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report])).status_code == 200
        first = client.get(f"/api/v1/jobs/{identity}").json()["metricsHistory"]
        assert len(first) == 1 and first[0]["receivedAt"] == 100
        assert first[0]["meanReward"] == 0 and first[0]["trainSuccess"] is None
        now[0] += 1
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report])).status_code == 200
        assert client.get(f"/api/v1/jobs/{identity}").json()["metricsHistory"] == first  # Idempotent retry.
        forged = report | {"leaseId": "unowned", "iteration": 2}
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [forged])).status_code == 403
        assert client.get(f"/api/v1/jobs/{identity}").json()["metricsHistory"] == first
        retained = app.state.jobs.get(identity)
        retained["metricsHistory"] = [first[0] | {"receivedAt": index} for index in range(900)]
        report.update(status="cancelled", reason="owned test completion", elapsedSeconds=2)
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report])).status_code == 200
        history = client.get(f"/api/v1/jobs/{identity}").json()["metricsHistory"]
        assert len(history) == 900 and history[0]["receivedAt"] == 1
        assert history[-1]["reportedStatus"] == "cancelled" and history[-1]["iteration"] == 1
        assert history[-1]["reason"] == "owned test completion" and history[-1]["receivedAt"] == 101
    with TestClient(create_app(settings)) as resumed:
        assert resumed.get(f"/api/v1/jobs/{identity}").json()["metricsHistory"] == history


def test_job_retains_last_observation_and_accepts_new_sparse_metrics():
    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task)).is_success
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        scope = {"jobId": job["jobId"], "leaseId": job["leaseId"], "status": "running"}
        url = f"/api/v1/jobs/{job['jobId']}"

        def send(elapsed, **metrics):
            report = scope | {"elapsedSeconds": elapsed, **metrics}
            assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report])).is_success
            return client.get(url).json()

        assert "iteration" not in send(0)["metricsHistory"][-1]
        assert send(0, iteration=0, meanReward=0, trainSuccess=0, memoryUsedMb=0)["metrics"] == {
            "iteration": 0, "elapsedSeconds": 0, "meanReward": 0, "trainSuccess": 0, "memoryUsedMb": 0,
        }
        send(1, iteration=4, meanReward=2.5, trainSuccess=0.5, memoryUsedMb=128)
        after = send(2, iteration=5, meanReward=0, trainSuccess=None, memoryUsedMb=None)
        assert after["metrics"] == {
            "iteration": 5, "elapsedSeconds": 2, "meanReward": 0, "trainSuccess": 0.5, "memoryUsedMb": 128,
        }
        assert after["metricsHistory"][-1]["memoryUsedMb"] is None
        assert after["metricsHistory"][-1]["trainSuccess"] is None

        # A device observation can arrive without an iteration sample.
        after = send(3, memoryUsedMb=64)
        assert after["metrics"] == {
            "iteration": 5, "elapsedSeconds": 3, "meanReward": 0, "trainSuccess": 0.5, "memoryUsedMb": 64,
        }
        after = send(4, iteration=5, trainSuccess=0, memoryUsedMb=0)
        assert after["metrics"] == {
            "iteration": 5, "elapsedSeconds": 4, "meanReward": 0, "trainSuccess": 0, "memoryUsedMb": 0,
        }

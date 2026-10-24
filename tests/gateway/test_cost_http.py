"""GPU-free public accounting with authenticated cumulative node reports."""

import json

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.training.jobs import profile_key
from tests.fixtures.training_task import task_archive, training_plan
from tests.gateway.test_evaluation_center import documents as evaluation_documents

TOKEN = "cost-contract-fake-node-token"
HEADERS = {"X-Node-Token": TOKEN, "X-Node-Instance": "cost-worker"}


def admitted(http, clock, *, device="cuda", budget=10, connect=True):
    task = http.post(
        "/api/v1/tasks", content=task_archive(), headers={"Content-Type": "application/x-tar"}
    ).json()
    document = training_plan(task["id"], budget={"gpuHours": budget})
    document["hyperparameters"]["device"] = device
    submitted = http.post("/api/v1/plans", json=document)
    assert submitted.status_code == 201, submitted.text
    plan = submitted.json()
    assert (
        http.post(
            f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"}
        ).status_code
        == 200
    )
    body = {
        "instanceId": "cost-worker",
        "hardware": "cost-fake-hardware",
        "cpuAvailable": True,
        "gpus": [{"id": "fake-gpu", "name": "GPU-free accounting double"}],
        "profiles": [
            {
                "key": profile_key(
                    plan, task["contentHash"], "cost-fake-hardware", "cpu" if device == "cpu" else "fake-gpu"
                ),
                "iterationSeconds": 1,
                "samples": 10,
                "measuredAt": clock[0],
                "evidence": "fake node contract",
            }
        ],
    }
    if connect:
        assert http.post("/api/v1/nodes/local/heartbeat", headers=HEADERS, json=body).status_code == 200
    result = http.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42})
    assert result.status_code == 202, result.text
    job = result.json()
    assert bool(job["leaseId"]) == connect, job
    return plan, body, job


def report(http, body, job, elapsed, status="running", *, memory=0):
    return http.post(
        "/api/v1/nodes/local/heartbeat",
        headers=HEADERS,
        json=body
        | {
            "reports": [
                {
                    "jobId": job["jobId"],
                    "leaseId": job["leaseId"],
                    "status": status,
                    "elapsedSeconds": elapsed,
                    "memoryUsedMb": memory,
                }
            ]
        },
    )


def totals(http, **params):
    result = http.get("/api/v1/cost/summary", params={"range": "all", **params})
    assert result.status_code == 200, result.text
    return result.json()["totals"]


def test_accepted_counters_keep_zero_price_unknown_llm_and_frozen_price_revisions():
    app = create_app(Settings(node_token=TOKEN))
    clock = [1000.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as http:
        plan, body, job = admitted(http, clock)
        assert (
            http.put(
                "/api/v1/prices", json={"revision": 0, "gpus": [{"nodeId": "local", "usdPerHour": 0}]}
            ).status_code
            == 200
        )
        clock[0] += 60
        assert report(http, body, job, 60).status_code == 200
        zero = totals(http)
        assert zero["gpuSeconds"] == pytest.approx(60)
        assert zero["gpuUsd"] == 0 and zero["unpricedGpuSeconds"] == 0
        assert zero["tokens"] is None and zero["requests"] is None and zero["llmUsd"] is None
        assert zero["memoryPeakMb"] == 0
        assert report(http, body, job, 60).status_code == 200
        assert report(http, body, job, 59).status_code == 422
        assert totals(http)["gpuSeconds"] == pytest.approx(60)
        assert (
            http.put(
                "/api/v1/prices", json={"revision": 1, "gpus": [{"nodeId": "local", "usdPerHour": 2}]}
            ).status_code
            == 200
        )
        clock[0] += 30
        assert report(http, body, job, 90).status_code == 200
        actual = totals(http)
        assert actual["gpuSeconds"] == pytest.approx(90)
        assert actual["gpuUsd"] == pytest.approx(30 * 2 / 3600)
        assert actual["priceRevisions"] == [1, 2]
        assert actual["reportedGpuSeconds"] == pytest.approx(90)
        assert actual["estimatedGpuSeconds"] == 0
        by_plan = http.get("/api/v1/cost/breakdown", params={"by": "plan", "range": "all"}).json()
        assert by_plan["rows"][0]["key"] == f"{plan['id']}@1"
        assert by_plan["rows"][0]["gpuSeconds"] == pytest.approx(90)
        charged = http.get(f"/api/v1/jobs/{job['jobId']}").json()["chargedGpuHours"]
        assert actual["gpuSeconds"] / 3600 == pytest.approx(charged)


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_unknown_gpu_prices_remain_unpriced_and_cpu_jobs_have_zero_gpu_seconds(device):
    app = create_app(Settings(node_token=TOKEN))
    clock = [1000.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as http:
        _, body, job = admitted(http, clock, device=device)
        clock[0] += 10
        assert report(http, body, job, 10, "failed").status_code == 200
        actual = totals(http)
        if device == "cuda":
            assert actual["gpuSeconds"] == pytest.approx(10)
            assert actual["gpuUsd"] is None and actual["unpricedGpuSeconds"] == pytest.approx(10)
        else:
            assert actual["gpuSeconds"] == 0 and actual["gpuUsd"] == 0


def test_bucket_allocation_conserves_accepted_gpu_seconds_across_an_hour():
    app = create_app(Settings(node_token=TOKEN))
    clock = [3600.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as http:
        _, body, job = admitted(http, clock)
        for sample in range(1, 61):
            # Stay within the real node-liveness boundary while crossing all twelve buckets.
            clock[0] = 3600 + sample * 60
            assert report(http, body, job, sample * 10).status_code == 200
        result = http.get("/api/v1/cost/series", params={"range": "1h"})
        assert result.status_code == 200, result.text
        buckets = result.json()["buckets"]
        assert len(buckets) == 12
        assert [bucket["gpuSeconds"] for bucket in buckets] == pytest.approx([50] * 12)
        assert sum(bucket["gpuSeconds"] for bucket in buckets) == pytest.approx(totals(http)["gpuSeconds"])
        narrowed = totals(http, range="custom", start="1970-01-01T01:30:00Z", end="1970-01-01T02:00:00Z")
        assert narrowed["gpuSeconds"] == pytest.approx(300)


def test_reopened_gateway_retains_usage_price_snapshots_and_read_receipts():
    settings = Settings(node_token=TOKEN)
    clock = [1000.0]
    first = create_app(settings)
    first.state.jobs.clock = lambda: clock[0]
    with TestClient(first) as http:
        _, body, job = admitted(http, clock)
        clock[0] += 10
        assert report(http, body, job, 10, "failed").status_code == 200
        anomalies = http.get("/api/v1/cost/anomalies").json()
        assert anomalies["unreadCount"] == 1
        identity = anomalies["anomalies"][0]["id"]
        assert http.post(f"/api/v1/cost/anomalies/{identity}/read").status_code == 200
        assert http.get("/api/v1/cost/anomalies").json()["unreadCount"] == 0
    second = create_app(settings)
    second.state.jobs.clock = lambda: clock[0]
    with TestClient(second) as http:
        assert report(http, body, job, 10, "failed").status_code == 200
        assert totals(http)["gpuSeconds"] == pytest.approx(10)
        assert http.get("/api/v1/cost/anomalies").json()["unreadCount"] == 0
        assert http.post("/api/v1/cost/anomalies/absent/read").status_code == 404


def test_timeout_estimates_are_visible_and_a_later_report_confirms_without_double_charging():
    app = create_app(Settings(node_token=TOKEN))
    clock = [1000.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as http:
        _, body, job = admitted(http, clock)
        clock[0] += 10
        assert report(http, body, job, 10).status_code == 200
        clock[0] += 61
        lost = http.get(f"/api/v1/jobs/{job['jobId']}").json()
        measured = totals(http)
        assert lost["reason"] == "node_lost"
        assert measured["gpuSeconds"] == pytest.approx(lost["chargedGpuHours"] * 3600)
        assert measured["reportedGpuSeconds"] == pytest.approx(10)
        assert measured["estimatedGpuSeconds"] == pytest.approx(61)
        assert report(http, body, job, 80, "failed").status_code == 200
        confirmed = totals(http)
        assert confirmed["gpuSeconds"] == pytest.approx(80)
        assert confirmed["reportedGpuSeconds"] == pytest.approx(80)
        assert confirmed["estimatedGpuSeconds"] == 0


def test_corrupt_price_file_cannot_break_authenticated_heartbeats_or_persisted_gpu_ownership():
    settings = Settings(node_token=TOKEN)
    clock = [1000.0]
    first = create_app(settings)
    first.state.jobs.clock = lambda: clock[0]
    with TestClient(first) as http:
        _, body, job = admitted(http, clock)
        (settings.state_root / "prices.yaml").write_text("[invalid human configuration", encoding="utf-8")
        assert http.get("/api/v1/prices").status_code == 503
        clock[0] += 10
        response = report(http, body, job, 10)
        assert response.status_code == 200, response.text
        assert response.json()["assignments"][0]["leaseId"] == job["leaseId"]
        assert http.get(f"/api/v1/jobs/{job['jobId']}").json()["chargedGpuHours"] == pytest.approx(10 / 3600)
        assert totals(http)["gpuUsd"] is None
        assert totals(http)["priceWarnings"] == ["Price table is unavailable"]
    reopened = create_app(settings)
    reopened.state.jobs.clock = lambda: clock[0]
    with TestClient(reopened) as http:
        assert totals(http)["gpuSeconds"] == pytest.approx(10)
        assert report(http, body, job, 10).status_code == 200
        assert http.get("/api/v1/nodes").json()["nodes"][0]["leases"]["fake-gpu"]["leaseId"] == job["leaseId"]


def test_preledger_job_counters_reopen_as_explicit_unpriced_history():
    settings = Settings(node_token=TOKEN)
    clock = [1000.0]
    first = create_app(settings)
    first.state.jobs.clock = lambda: clock[0]
    with TestClient(first) as http:
        _, body, job = admitted(http, clock)
        clock[0] += 10
        assert report(http, body, job, 10, "failed").status_code == 200
    # The pre-ledger format is the same public Job journey minus its new private accounting.
    path = settings.state_root / "jobs.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["jobs"][job["jobId"]].pop("costLedger")
    path.write_text(json.dumps(document), encoding="utf-8")
    reopened = create_app(settings)
    reopened.state.jobs.clock = lambda: clock[0]
    with TestClient(reopened) as http:
        actual = totals(http)
        assert actual["gpuSeconds"] == pytest.approx(10)
        assert actual["reportedGpuSeconds"] == pytest.approx(10)
        assert actual["historicalGpuSeconds"] == pytest.approx(10)
        assert actual["gpuUsd"] is None
        assert actual["priceWarnings"] == ["Price at historical usage was not retained"]
        assert "costLedger" not in http.get(f"/api/v1/jobs/{job['jobId']}").json()


def test_cross_day_preledger_reopen_does_not_retimestamp_an_old_gpu_memory_report():
    settings = Settings(node_token=TOKEN)
    clock = [1000.0]
    first = create_app(settings)
    first.state.jobs.clock = lambda: clock[0]
    with TestClient(first) as http:
        _, body, job = admitted(http, clock)
        clock[0] += 10
        assert report(http, body, job, 10, "failed", memory=512).status_code == 200
    path = settings.state_root / "jobs.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["jobs"][job["jobId"]].pop("costLedger")
    path.write_text(json.dumps(saved), encoding="utf-8")
    clock[0] += 86400
    reopened = create_app(settings)
    reopened.state.jobs.clock = lambda: clock[0]
    with TestClient(reopened) as http:
        assert http.get(f"/api/v1/jobs/{job['jobId']}").status_code == 200
        assert totals(http, range="today")["memoryPeakMb"] is None
        assert totals(http, range="today")["gpuSeconds"] == 0


def test_a_job_queued_for_an_hour_does_not_allocate_gpu_seconds_before_its_lease():
    app = create_app(Settings(node_token=TOKEN))
    clock = [1000.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as http:
        _, body, queued = admitted(http, clock, connect=False)
        assert queued["leaseId"] is None and queued["reason"] == "estimate_unavailable"
        clock[0] += 3600
        assert http.post("/api/v1/nodes/local/heartbeat", headers=HEADERS, json=body).status_code == 200
        active = http.get(f"/api/v1/jobs/{queued['jobId']}").json()
        assert active["leaseId"]
        clock[0] += 10
        assert report(http, body, active, 10).status_code == 200
        series = http.get("/api/v1/cost/series", params={"range": "1h"}).json()["buckets"]
        assert [bucket["gpuSeconds"] for bucket in series[:-1]] == [0] * (len(series) - 1)
        assert series[-1]["gpuSeconds"] == pytest.approx(10)
        assert sum(bucket["gpuSeconds"] for bucket in series) == pytest.approx(totals(http)["gpuSeconds"])


def test_timeout_anomalies_respect_public_evaluation_visibility_and_operator_read_audit():
    settings = Settings(remote=True, operator_token="cost-operator", viewer_token="cost-viewer")
    app = create_app(settings)
    with TestClient(app) as http:
        _, record = evaluation_documents()
        record.update(status="failed", result=None,
                      error={"code": "episode_timeout", "message": "PRIVATE_GRADING_NOT_A_COST_DETAIL"})
        app.state.manager.evaluations.store.save(record)
        app.state.manager.evaluations.store.save(record | {"evaluationId": "eval-held-out", "split": "test"})
        viewer = {"Authorization": "Bearer cost-viewer"}
        result = http.get("/api/v1/cost/anomalies", headers=viewer)
        assert result.status_code == 200 and "PRIVATE_GRADING" not in result.text
        assert "eval-held-out" not in result.text
        listed = result.json()
        assert listed["unreadCount"] == 1 and listed["canAcknowledge"] is False
        identity = listed["anomalies"][0]["id"]
        assert http.post(f"/api/v1/cost/anomalies/{identity}/read", headers=viewer).status_code == 403
        operator = {"Authorization": "Bearer cost-operator", "X-Actor": "user:cost-reader"}
        first = http.post(f"/api/v1/cost/anomalies/{identity}/read", headers=operator)
        assert first.status_code == 200 and first.json()["actor"] == {"kind": "user", "name": "cost-reader"}
        assert http.post(f"/api/v1/cost/anomalies/{identity}/read", headers=operator).json() == first.json()


def test_cost_reads_do_not_publish_an_uncommitted_counter_after_job_persistence_fails():
    settings = Settings(node_token=TOKEN)
    app = create_app(settings)
    clock = [1000.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as http:
        _, body, job = admitted(http, clock)
        clock[0] += 10
        assert report(http, body, job, 10).status_code == 200
        path = settings.state_root / "jobs.json"
        backup = settings.state_root / "retained-jobs.json"
        path.rename(backup)
        path.mkdir()  # Atomic file replacement cannot replace an existing directory.
        try:
            clock[0] += 10
            assert report(http, body, job, 20).status_code == 503
            assert http.get("/api/v1/cost/summary").status_code == 503
        finally:
            path.rmdir()
            backup.rename(path)
    reopened = create_app(settings)
    reopened.state.jobs.clock = lambda: clock[0]
    with TestClient(reopened) as http:
        assert totals(http)["gpuSeconds"] == pytest.approx(10)

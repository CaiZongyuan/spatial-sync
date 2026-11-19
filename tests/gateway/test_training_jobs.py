"""Public training admission and authenticated, socket-free node ownership."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.internal import SessionOptions
from packages.training.jobs import profile_key
from tests.fixtures.training_task import task_archive, training_plan

TOKEN = "fixture-local-node-secret"
NODE = {"X-Node-Token": TOKEN, "X-Node-Instance": "worker-one"}
HEARTBEAT = "/api/v1/nodes/local/heartbeat"


def setup_plan(client, *, budget=1, seeds=None):
    uploaded = client.post(
        "/api/v1/tasks", content=task_archive(), headers={"Content-Type": "application/x-tar"}
    )
    assert uploaded.status_code == 201, uploaded.text
    task = uploaded.json()
    overrides = {"jobs": {"seeds": seeds}} if seeds else {}
    created = client.post(
        "/api/v1/plans", json=training_plan(task["id"], budget={"gpuHours": budget}, **overrides),
    )
    assert created.status_code == 201, created.text
    return created.json(), task


def approve(client, approval):
    response = client.post(f"/api/v1/approvals/{approval}/decision", json={"decision": "approve"})
    assert response.status_code == 200, response.text


def heartbeat(plan, task, reports=()):
    return {
        "instanceId": "worker-one",
        "hardware": "fake-hardware-uuid",
        "gpus": [{"id": "gpu-fixture", "name": "GPU-free contract double"}],
        "profiles": [
            {
                "key": profile_key(plan, task["contentHash"], "fake-hardware-uuid", "gpu-fixture"),
                "iterationSeconds": 1,
                "samples": 10,
                "measuredAt": 50,
                "evidence": "fixture timing",
            }
        ],
        "reports": list(reports),
    }


def test_plan_revision_budget_grant_and_node_token_are_bound_to_one_immutable_dispatch():
    app = create_app(Settings(node_token=TOKEN))
    clock = [100.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as client:
        plan, task = setup_plan(client, budget=0.001)
        start = {"planId": plan["id"], "seed": 42, "requestId": "first-job"}
        rejected = client.post("/api/v1/jobs", json=start)
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "plan_not_approved"
        assert client.post(HEARTBEAT, json=heartbeat(plan, task)).status_code == 403
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task)).status_code == 200
        approve(client, plan["approvalId"])
        clock[0] = 161  # Cached measured timing remains valid while the worker is offline.
        queued = client.post("/api/v1/jobs", json=start)
        assert queued.status_code == 202, queued.text
        job = queued.json()
        assert job["reason"] == "pending_approval" and job["leaseId"] is None
        refused = client.post(
            f"/api/v1/approvals/{job['approvalId']}/decision",
            headers={"X-Actor": "agent:trainer"},
            json={"decision": "approve"},
        )
        assert refused.status_code == 403
        approve(client, job["approvalId"])
        revised = client.post(
            f"/api/v1/plans/{plan['id']}/revisions",
            json=training_plan(
                task["id"],
                jobs={"seeds": [43]},
                rationale="A different, unapproved round",
            ),
        ).json()
        assert revised["revision"] == 2 and revised["status"] == "submitted"
        retried = client.post("/api/v1/jobs", json=start)
        assert retried.status_code == 202 and retried.json()["jobId"] == job["jobId"]
        assert client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 43}).status_code == 409
        reply = client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task)).json()
        assert len(reply["assignments"]) == 1
        assignment = reply["assignments"][0]
        headers = NODE | {"X-Lease-Id": assignment["leaseId"]}
        inputs = client.get(f"/api/v1/jobs/{job['jobId']}/inputs", headers=headers)
        assert inputs.status_code == 200, inputs.text
        assert inputs.json()["plan"]["revision"] == 1 and inputs.json()["job"]["seed"] == 42
        assert inputs.json()["job"]["taskContentHash"] == task["contentHash"]
        forged = heartbeat(plan, task, [{"jobId": job["jobId"], "leaseId": "forged", "status": "failed"}])
        assert client.post(HEARTBEAT, headers=NODE, json=forged).status_code == 403
        assert (
            client.get(
                f"/api/v1/jobs/{job['jobId']}/inputs", headers=headers | {"X-Lease-Id": "stale-attempt"}
            ).status_code
            == 403
        )
        repeated = client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task)).json()
        assert repeated["assignments"] == reply["assignments"]
        assert TOKEN not in client.get("/api/v1/nodes").text
        client.post(f"/api/v1/jobs/{job['jobId']}/cancel")
        done = client.post(
            HEARTBEAT,
            headers=NODE,
            json=heartbeat(
                plan,
                task,
                [
                    {
                        "jobId": job["jobId"],
                        "leaseId": assignment["leaseId"],
                        "status": "cancelled",
                        "elapsedSeconds": 1,
                    }
                ],
            ),
        )
        assert done.status_code == 200 and not done.json()["assignments"]


def test_default_private_node_identity_and_one_dispatch_survive_gateway_reopening():
    settings = Settings()
    first = create_app(settings)
    with TestClient(first) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        token = first.state.jobs.node_token
        headers = NODE | {"X-Node-Token": token}
        client.post(HEARTBEAT, headers=headers, json=heartbeat(plan, task))
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert job["leaseId"]
    second = create_app(settings)
    with TestClient(second) as client:
        assert second.state.jobs.node_token == token
        before = client.get("/api/v1/nodes").json()["nodes"][0]["leases"]
        assert len(before) == 1
        report = {
            "jobId": job["jobId"],
            "leaseId": job["leaseId"],
            "status": "running",
            "iteration": 3,
            "elapsedSeconds": 2,
            "meanReward": 1.5,
            "logs": ["actual first line"],
        }
        replied = client.post(HEARTBEAT, headers=headers, json=heartbeat(plan, task, [report]))
        assert replied.status_code == 200, replied.text
        assert replied.json()["assignments"][0]["leaseId"] == job["leaseId"]
        actual = client.get(f"/api/v1/jobs/{job['jobId']}").json()
        assert actual["status"] == "running" and actual["metrics"]["iteration"] == 3
        assert (
            client.post(HEARTBEAT, headers=headers, json=heartbeat(plan, task, [report])).status_code == 200
        )
        logs = client.get(f"/api/v1/jobs/{job['jobId']}/logs").json()
        assert logs["lines"] == ["actual first line"] and logs["cursor"] == 1
        client.post(f"/api/v1/jobs/{job['jobId']}/cancel")
        finished = {
            "jobId": job["jobId"],
            "leaseId": job["leaseId"],
            "status": "cancelled",
            "elapsedSeconds": 3,
            "logAfter": 1,
        }
        assert (
            client.post(HEARTBEAT, headers=headers, json=heartbeat(plan, task, [finished])).status_code == 200
        )
        # A lost HTTP reply must not cause an already-cleaned attempt to be launched again.
        duplicate = client.post(HEARTBEAT, headers=headers, json=heartbeat(plan, task, [finished]))
        assert duplicate.status_code == 200 and duplicate.json()["assignments"] == []


def test_retained_local_upgrade_requires_original_owner_and_gpu():
    settings = Settings(node_token=TOKEN)
    with TestClient(create_app(settings)) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        body["hardware"] = "a" * 64
        body["profiles"][0]["key"] = profile_key(plan, task["contentHash"], body["hardware"], "gpu-fixture")
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert job["leaseId"]
    path = settings.state_root / "jobs.json"
    state = json.loads(path.read_bytes())
    for field in ("registrations", "nodeCredentials", "leaseSequence", "gpuQuarantine", "cleanupKey"):
        state.pop(field, None)
    for field in ("trainerVersions", "isaaclabVersions", "compilerHash", "trainingEntries", "legacyHardware"):
        state["nodes"]["local"].pop(field, None)
    state["jobs"][job["jobId"]].pop("backend", None)
    path.write_text(json.dumps(state), encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        upgraded = body | {
            "hardware": "node-hardware/2:fixture", "legacyHardware": body["hardware"], "profiles": [],
        }
        for headers, changes in (
            (NODE, {"legacyHardware": "b" * 64}),
            (NODE, {"gpus": [{"id": "other-gpu", "name": "fixture"}]}),
            (NODE | {"X-Node-Instance": "wrong-owner"}, {}),
            (NODE | {"X-Node-Instance": "wrong-owner"}, {"instanceId": "wrong-owner"}),
        ):
            assert client.post(HEARTBEAT, headers=headers, json=upgraded | changes).status_code == 409
        assert client.post(HEARTBEAT, json=upgraded).status_code == 403
        reply = client.post(HEARTBEAT, headers=NODE, json=upgraded)
        assert reply.is_success, reply.text
        (assignment,) = reply.json()["assignments"]
        assert assignment["leaseId"] == job["leaseId"] and assignment["cleanupOnly"]
        url = f"/api/v1/jobs/{job['jobId']}/inputs"
        stale = client.get(url, headers=NODE | {"X-Lease-Id": "wrong-lease"})
        assert stale.status_code == 403
        inputs = client.get(url, headers=NODE | {"X-Lease-Id": job["leaseId"]}).json()
        assert inputs["job"]["profileKey"] == job["profileKey"] and inputs["trainerVersions"] is None
        report = {"jobId": job["jobId"], "leaseId": job["leaseId"], "status": "cleaned"}
        reply = client.post(HEARTBEAT, headers=NODE, json=upgraded | {"reports": [report]})
        assert reply.is_success and reply.json()["assignments"] == []
        assert client.get(url, headers=NODE | {"X-Lease-Id": job["leaseId"]}).status_code == 403


@pytest.mark.parametrize("retained", [True, False], ids=["retained-local", "modern-local"])
def test_terminal_report_preserves_progress_without_new_metrics(retained):
    from tests.gateway.test_remote_training_nodes import ISAAC_ENTRY, ISAAC_VERSIONS, node_profile_key

    settings = Settings(node_token=TOKEN)
    now = [100.0]

    def gateway():
        app = create_app(settings)
        app.state.jobs.clock = lambda: now[0]
        return TestClient(app)

    with gateway() as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        if not retained:
            body.update(hardware="node-hardware/2:fixture", trainerVersions=ISAAC_VERSIONS, **ISAAC_ENTRY)
            body["profiles"][0]["key"] = node_profile_key(
                plan, task["contentHash"], {"id": "local", **body}, body["gpus"][0], backend="isaaclab",
            )
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        scope = {"jobId": job["jobId"], "leaseId": job["leaseId"]}
        progress = scope | {
            "status": "running", "iteration": 4, "meanReward": 2.5, "memoryUsedMb": 128,
            "elapsedSeconds": 42, "logs": ["previous output"],
        }
        assert client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [progress]}).is_success
        if retained:
            now[0] = 161
            assert client.get(f"/api/v1/jobs/{job['jobId']}").json()["reason"] == "node_lost"

    if retained:
        path = settings.state_root / "jobs.json"
        saved = json.loads(path.read_bytes())
        del saved["nodes"]["local"]["leases"]["gpu-fixture"]["cleanupOnly"]
        path.write_text(json.dumps(saved), encoding="utf-8")

    with gateway() as client:
        terminal = scope | {"status": "cancelled", "elapsedSeconds": 45,
                            "logAfter": 1, "logs": ["last owned line"]}
        if not retained:
            del terminal["elapsedSeconds"]
        response = client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [terminal]})
        assert response.is_success and response.json()["assignments"] == []
        view = f"/api/v1/jobs/{job['jobId']}"
        result = client.get(view).json()
        assert result["status"] == ("failed" if retained else "cancelled")
        assert result["reason"] == ("node_lost" if retained else None)
        retained_metrics = {"iteration": 4, "meanReward": 2.5, "memoryUsedMb": 128}
        assert result["metrics"] | retained_metrics == result["metrics"]
        assert result["metrics"]["elapsedSeconds"] == (45 if retained else 42)
        assert client.get(view + "/logs", params={"after": 1}).json() == {
            "lines": ["last owned line"], "complete": True, "cursor": 2,
        }


def test_unrelated_measured_profile_never_authorizes_an_estimated_gpu_launch():
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        wrong = heartbeat(plan, task)
        wrong["profiles"][0]["key"] = "0" * 64
        client.post(HEARTBEAT, headers=NODE, json=wrong)
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert job["reason"] == "estimate_unavailable" and job["estimatedGpuHours"] is None
        assert client.get("/api/v1/nodes").json()["nodes"][0]["leases"] == {}


def test_remote_operator_is_not_a_node_and_node_identity_is_not_public_operator_authority():
    app = create_app(Settings(remote=True, operator_token="fixture-operator", node_token=TOKEN))
    with TestClient(app) as client:
        body = {"instanceId": "worker-one", "hardware": "fixture", "gpus": [], "profiles": []}
        operator = {"Authorization": "Bearer fixture-operator"}
        assert client.post(HEARTBEAT, headers=operator, json=body).status_code == 403
        assert client.post(HEARTBEAT, headers=NODE, json=body).status_code == 200
        assert client.post("/api/v1/jobs", headers=NODE, json={"planId": "unapproved"}).status_code == 401
        assert client.get("/api/v1/nodes", headers=operator).status_code == 200


def test_budget_reserves_pending_work_once_and_node_loss_keeps_physical_lease_quarantined():
    app = create_app(Settings(node_token=TOKEN))
    clock = [100.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as client:
        plan, task = setup_plan(client, budget=0.004)
        approve(client, plan["approvalId"])
        client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task))
        first = client.post(
            "/api/v1/jobs", json={"planId": plan["id"], "seed": 42, "requestId": "first"}
        ).json()
        assert first["leaseId"] and first["estimatedGpuHours"] == 10 / 3600
        second = client.post(
            "/api/v1/jobs", json={"planId": plan["id"], "seed": 43, "requestId": "second"}
        ).json()
        assert second["reason"] == "pending_approval" and second["leaseId"] is None
        repeated = client.post(
            "/api/v1/jobs", json={"planId": plan["id"], "seed": 42, "requestId": "first"}
        ).json()
        assert repeated["jobId"] == first["jobId"]
        clock[0] = 161
        lost = client.get(f"/api/v1/jobs/{first['jobId']}").json()
        assert lost["status"] == "failed" and lost["reason"] == "node_lost"
        leases = client.get("/api/v1/nodes").json()["nodes"][0]["leases"]
        assert leases["gpu-fixture"]["quarantined"] is True


def test_queued_gpu_budget_uses_fresh_same_key_profile_before_lease():
    app = create_app(Settings(node_token=TOKEN))
    clock = [100.0]
    app.state.jobs.clock = lambda: clock[0]
    with TestClient(app) as client:
        plan, task = setup_plan(client, budget=0.2)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        body["profiles"][0]["iterationSeconds"] = 10
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        clock[0] += 61
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert job["leaseId"] is None and job["estimatedGpuHours"] == pytest.approx(100 / 3600)
        body["profiles"][0].update(iterationSeconds=120, measuredAt=clock[0])
        reply = client.post(HEARTBEAT, headers=NODE, json=body).json()
        assert reply["assignments"] == []
        refreshed = client.get(f"/api/v1/jobs/{job['jobId']}").json()
        assert refreshed["estimatedGpuHours"] == pytest.approx(1 / 3)
        assert refreshed["reason"] == "pending_approval"
        approval = client.get(f"/api/v1/approvals/{refreshed['approvalId']}").json()
        assert approval["details"]["estimatedGpuHours"] == pytest.approx(1 / 3)
        clock[0] += 1
        body["profiles"][0].update(iterationSeconds=36, measuredAt=clock[0])
        reply = client.post(HEARTBEAT, headers=NODE, json=body).json()
        assert [lease["jobId"] for lease in reply["assignments"]] == [job["jobId"]]
        launched = client.get(f"/api/v1/jobs/{job['jobId']}").json()
        assert launched["estimatedGpuHours"] == pytest.approx(0.1)
        assert launched["approvalId"] is None
        retired = f"/api/v1/approvals/{approval['approvalId']}"
        assert client.get(retired).json()["status"] == "unavailable"
        assert client.post(f"{retired}/decision", json={"decision": "approve"}).status_code == 409


@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_job_approval_covers_only_the_extra_gpu_hours_reviewed(decision):
    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        plan, task = setup_plan(client, budget=0.2, seeds=[41, 42, 43])
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        body["profiles"][0].update(iterationSeconds=18, measuredAt=100)
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        prior = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 41}).json()
        charged = {
            "jobId": prior["jobId"], "leaseId": prior["leaseId"],
            "status": "cancelled", "elapsedSeconds": 180,
        }
        assert client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [charged]}).is_success
        first = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert first["leaseId"] and first["estimatedGpuHours"] == pytest.approx(0.05)
        body["profiles"][0].update(iterationSeconds=63, measuredAt=101)
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        waiting = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 43}).json()
        original = waiting["approvalId"]
        assert original and waiting["estimatedGpuHours"] == pytest.approx(0.175)
        approval = client.get(f"/api/v1/approvals/{original}").json()
        assert approval["details"]["remainingGpuHours"] == pytest.approx(0.1)
        assert approval["details"]["extraGpuHours"] == pytest.approx(0.075)
        approve(client, original)
        settled = {
            "jobId": first["jobId"], "leaseId": first["leaseId"],
            "status": "cancelled", "elapsedSeconds": 360,
        }
        reply = client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [settled]})
        assert reply.is_success and reply.json()["assignments"] == []
        updated = client.get(f"/api/v1/jobs/{waiting['jobId']}").json()
        assert updated["approvalId"] != original and updated["reason"] == "pending_approval"
        replacement = client.get(f"/api/v1/approvals/{updated['approvalId']}").json()
        assert replacement["details"]["remainingGpuHours"] == pytest.approx(0.05)
        assert replacement["details"]["estimatedGpuHours"] == pytest.approx(0.175)
        assert replacement["details"]["extraGpuHours"] == pytest.approx(0.125)
        assert client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [settled]}).is_success
        same = client.get(f"/api/v1/jobs/{waiting['jobId']}").json()
        assert same["approvalId"] == updated["approvalId"]
        action = f"/api/v1/approvals/{same['approvalId']}/decision"
        assert client.post(action, json={"decision": decision}).is_success
        decided = client.get(f"/api/v1/jobs/{waiting['jobId']}").json()
        assert bool(decided["leaseId"]) == (decision == "approve")
        if decision == "deny":
            assert decided["reason"] == "approval_denied"


@pytest.mark.parametrize("earlier_approval", [True, False])
def test_dispatch_charges_reported_overrun_of_active_gpu_reservation(earlier_approval):
    from tests.gateway.test_remote_training_nodes import ISAAC_ENTRY, ISAAC_VERSIONS, node_profile_key

    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        plan, task = setup_plan(client, budget=0.2 if earlier_approval else 0.1)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        body.update(hardware="node-hardware/2:fixture", trainerVersions=ISAAC_VERSIONS, **ISAAC_ENTRY)
        node = {"id": "local", **body}
        body["profiles"][0]["key"] = node_profile_key(
            plan, task["contentHash"], node, body["gpus"][0], backend="isaaclab",
        )
        body["profiles"][0].update(iterationSeconds=18, measuredAt=100)
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        first = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert first["leaseId"] and first["estimatedGpuHours"] == pytest.approx(0.05)
        if earlier_approval:
            body["profiles"][0].update(iterationSeconds=63, measuredAt=101)
            assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        waiting = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 43}).json()
        original = waiting["approvalId"]
        if earlier_approval:
            assert original
            prior = client.get(f"/api/v1/approvals/{original}").json()
            assert prior["details"]["extraGpuHours"] == pytest.approx(0.025)
            approve(client, original)
        else:
            assert original is None
        gpu = body["gpus"][0] | {"id": "second-card"}
        body["gpus"].append(gpu)
        seconds = 540 if earlier_approval else 270
        report = {
            "jobId": first["jobId"], "leaseId": first["leaseId"], "status": "running",
            "elapsedSeconds": seconds,
        }
        packet = body | {"reports": [report]}
        reply = client.post(HEARTBEAT, headers=NODE, json=packet)
        assert reply.is_success and [row["jobId"] for row in reply.json()["assignments"]] == [first["jobId"]]
        url = f"/api/v1/jobs/{waiting['jobId']}"
        queued = client.get(url).json()
        assert queued["reason"] == "pending_approval" and queued["approvalId"] != original
        details = client.get(f"/api/v1/approvals/{queued['approvalId']}").json()["details"]
        assert details["extraGpuHours"] == pytest.approx(0.125 if earlier_approval else 0.025)
        settled = report | {"status": "cancelled"}
        assert client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [settled]}).is_success
        assert client.get(url).json()["approvalId"] == queued["approvalId"]
        approve(client, queued["approvalId"])
        assert client.get(url).json()["leaseId"]


def test_pending_job_reconsiders_a_different_eligible_gpu():
    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        plan, task = setup_plan(client, budget=0.2)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        body["profiles"][0].update(iterationSeconds=108, measuredAt=100)
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success
        request = {"planId": plan["id"], "seed": 42, "requestId": "original"}
        waiting = client.post("/api/v1/jobs", json=request).json()
        assert waiting["reason"] == "pending_approval" and waiting["estimatedGpuHours"] == pytest.approx(0.3)
        cheaper = {"id": "gpu-cheaper", "name": "Alternative device"}
        key = profile_key(plan, task["contentHash"], body["hardware"], cheaper["id"])
        profile = body["profiles"][0] | {"key": key, "iterationSeconds": 18, "measuredAt": 101}
        reply = client.post(HEARTBEAT, headers=NODE, json=body | {
            "gpus": [*body["gpus"], cheaper], "profiles": [profile],
        })
        assert reply.is_success
        (lease,) = reply.json()["assignments"]
        assert (lease["jobId"], lease["gpuId"]) == (waiting["jobId"], cheaper["id"])
        launched = client.post("/api/v1/jobs", json=request).json()
        assert launched["jobId"] == waiting["jobId"] and launched["estimatedGpuHours"] == pytest.approx(0.05)
        assert client.get(f"/api/v1/approvals/{waiting['approvalId']}").json()["status"] == "unavailable"


def test_concurrent_gpu_requests_share_one_plan_budget():
    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        plan, task = setup_plan(client, budget=0.2)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)
        gpu = {"id": "gpu-other", "name": "Second card"}
        body["gpus"].append(gpu)
        key = profile_key(plan, task["contentHash"], body["hardware"], gpu["id"])
        body["profiles"][0].update(iterationSeconds=45, measuredAt=100)
        body["profiles"].append(body["profiles"][0] | {"key": key})
        assert client.post(HEARTBEAT, headers=NODE, json=body).is_success

        def submit(seed):
            return client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": seed}).json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs = list(executor.map(submit, [42, 43]))
        assert sum(bool(job["leaseId"]) for job in jobs) == 1
        (queued,) = [job for job in jobs if not job["leaseId"]]
        assert queued["reason"] == "pending_approval" and not queued["budgetReserved"]
        details = client.get(f"/api/v1/approvals/{queued['approvalId']}").json()["details"]
        assert details["remainingGpuHours"] == pytest.approx(0.075)
        assert details["extraGpuHours"] == pytest.approx(0.05)


def test_profile_refresh_keeps_active_reservation_and_requires_current_over_budget_approval():
    settings = Settings(node_token=TOKEN)
    app = create_app(settings)
    with TestClient(app) as client:
        plan, task = setup_plan(client, budget=0.2)
        approve(client, plan["approvalId"])
        body = heartbeat(plan, task)

        def measure(client, seconds, measured_at):
            body["profiles"][0].update(iterationSeconds=seconds, measuredAt=measured_at)
            reply = client.post(HEARTBEAT, headers=NODE, json=body)
            assert reply.is_success, reply.text
            return reply.json()["assignments"]

        measure(client, 36, 100)
        first = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert first["leaseId"] and first["estimatedGpuHours"] == pytest.approx(0.1)
        request = {"planId": plan["id"], "seed": 43, "requestId": "queued"}
        second = client.post("/api/v1/jobs", json=request).json()
        assert second["budgetReserved"] and second["leaseId"] is None
        assert second["approvalId"] is None
        assert len(measure(client, 120, 101)) == 1
        first_url = f"/api/v1/jobs/{first['jobId']}"
        second_url = f"/api/v1/jobs/{second['jobId']}"
        waiting = client.get(second_url).json()
        assert not waiting["budgetReserved"] and waiting["estimatedGpuHours"] == pytest.approx(1 / 3)
        old_approval = waiting["approvalId"]
        assert old_approval
        approve(client, old_approval)
        approve(client, old_approval)
        assert len(client.get(f"/api/v1/approvals/{old_approval}").json()["decisions"]) == 1
        assert client.get(second_url).json()["budgetReserved"]
        assert len(measure(client, 144, 102)) == 1
        waiting = client.get(second_url).json()
        assert waiting["approvalId"] != old_approval and waiting["leaseId"] is None
        assert client.get(f"/api/v1/approvals/{old_approval}").json()["status"] == "unavailable"
        repeated = client.post(f"/api/v1/approvals/{old_approval}/decision", json={"decision": "approve"})
        assert repeated.status_code == 409
        assert client.get(first_url).json()["estimatedGpuHours"] == pytest.approx(0.1)
        assert len(measure(client, 36, 100)) == 1
        assert client.get(second_url).json()["approvalId"] == waiting["approvalId"]
        assert client.get(second_url).json()["estimatedGpuHours"] == pytest.approx(0.4)

    with TestClient(create_app(settings)) as client:
        assert client.get(second_url).json()["approvalId"] == waiting["approvalId"]
        measure(client, 65, 103)
        lowered = client.get(second_url).json()
        assert lowered["approvalId"] != waiting["approvalId"]
        assert lowered["estimatedGpuHours"] == pytest.approx(65 / 360)
        approve(client, lowered["approvalId"])
        measure(client, 62, 104)
        after = client.post("/api/v1/jobs", json=request).json()
        assert after["jobId"] == second["jobId"] and after["approvalId"] == lowered["approvalId"]
        assert after["estimatedGpuHours"] == pytest.approx(62 / 360)
        assert after["leaseId"] is None and after["budgetReserved"]
        completed = {
            "jobId": first["jobId"], "leaseId": first["leaseId"],
            "status": "cancelled", "elapsedSeconds": 360,
        }
        reply = client.post(HEARTBEAT, headers=NODE, json=body | {"reports": [completed]})
        assert [lease["jobId"] for lease in reply.json()["assignments"]] == [second["jobId"]]
        assert client.get(second_url).json()["estimatedGpuHours"] == pytest.approx(62 / 360)


def test_existing_gpu_session_blocks_training_and_training_lease_overrides_only_new_cuda_requests():
    received = []

    class CaptureNewton(SyntheticSource):
        configure_scene = None

        async def start(self, scene_id, options):
            received.append(options.device)
            return await super().start("lab-demo", options)

    app = create_app(
        Settings(node_token=TOKEN), source_factory=lambda kind, identity: CaptureNewton(identity)
    )
    with TestClient(app) as client:
        manager = app.state.manager
        options = SessionOptions(device="cuda:0")
        original = client.portal.call(lambda: manager.create("falling-boxes", "newton", options))
        assert received == ["cuda:0"] and original.options.device == "cuda:0"
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task))
        queued = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert queued["reason"] == "session_gpu_busy" and queued["leaseId"] is None
        assert original.options.device == "cuda:0"
        client.portal.call(original.close)
        assignment = client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task)).json()["assignments"][0]
        with pytest.raises(HTTPException) as blocked:
            client.portal.call(lambda: manager.reload(original.session_id))
        assert blocked.value.status_code == 409 and blocked.value.detail["code"] == "session_gpu_busy"
        fresh = client.portal.call(lambda: manager.create("falling-boxes", "newton", options))
        assert received == ["cuda:0", "cpu"] and fresh.options.device == "cpu"
        assert options.device == "cuda:0"
        client.portal.call(fresh.close)
        client.post(f"/api/v1/jobs/{assignment['jobId']}/cancel")
        client.post(
            HEARTBEAT,
            headers=NODE,
            json=heartbeat(
                plan,
                task,
                [
                    {
                        **{key: assignment[key] for key in ("jobId", "leaseId")},
                        "status": "cancelled",
                    }
                ],
            ),
        )

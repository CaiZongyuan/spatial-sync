"""Public training admission and authenticated, socket-free node ownership."""

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


def setup_plan(client, *, budget=1):
    uploaded = client.post(
        "/api/v1/tasks", content=task_archive(), headers={"Content-Type": "application/x-tar"}
    )
    assert uploaded.status_code == 201, uploaded.text
    task = uploaded.json()
    created = client.post("/api/v1/plans", json=training_plan(task["id"], budget={"gpuHours": budget}))
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

"""Public contracts for operators and outbound Compute Nodes."""

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

OPERATOR = {"Authorization": "Bearer operator-fixture"}
VIEWER = {"Authorization": "Bearer viewer-fixture"}
ISAAC_VERSIONS = {
    "isaaclab": "3.0.0", "newton": "1.6.0rc1", "warp": "1.17.0", "torch": "2.11.0+cu128", "rsl_rl": "5.4.1",
}
COMPILER = "a" * 64


def remote_settings(**kwargs):
    return Settings(remote=True, operator_token="operator-fixture", viewer_token="viewer-fixture", **kwargs)


def register(client, node_id):
    issued = client.post("/api/v1/nodes/registrations", headers=OPERATOR, json={"nodeId": node_id})
    assert issued.status_code == 201, issued.text
    request = {"code": issued.json()["code"]}
    exchanged = client.post("/api/v1/nodes/register", json=request)
    assert exchanged.status_code == 201, exchanged.text
    assert exchanged.json()["nodeId"] == node_id
    assert client.post("/api/v1/nodes/register", json=request).status_code == 403
    return {"X-Node-Token": exchanged.json()["token"], "X-Node-Id": node_id, "X-Node-Instance": "test-1"}


def beat(client, headers, *, path_id=None, **values):
    return client.post(
        f"/api/v1/nodes/{path_id or headers['X-Node-Id']}/heartbeat",
        headers=headers,
        json={"instanceId": headers["X-Node-Instance"], "hardware": "fixture", "gpus": [], **values},
    )


def test_remote_node_credentials_are_one_use_scoped_durable_and_revocable():
    settings = remote_settings()
    with TestClient(create_app(settings)) as client:
        assert (
            client.post("/api/v1/nodes/registrations", headers=VIEWER, json={"nodeId": "gpu-a"}).status_code
            == 403
        )
        first, second = register(client, "gpu-a"), register(client, "gpu-b")
        for headers in (first, second):
            assert beat(client, headers).status_code == 200
            assert client.get("/api/v1/nodes", headers=headers).status_code == 401
        assert beat(client, first, path_id="gpu-b").status_code in (401, 403)
        assert beat(client, first | {"X-Node-Id": "gpu-b"}).status_code in (401, 403)
        listed = client.get("/api/v1/nodes", headers=OPERATOR)
        assert {node["id"] for node in listed.json()["nodes"]} == {"gpu-a", "gpu-b"}
        assert all(headers["X-Node-Token"] not in listed.text for headers in (first, second))
    with TestClient(create_app(settings)) as client:
        assert beat(client, first).status_code == 200
        assert client.delete("/api/v1/nodes/gpu-a", headers=OPERATOR).status_code == 204
        assert beat(client, first).status_code in (401, 403)
        assert beat(client, second).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert beat(client, first).status_code in (401, 403)


def test_validation_profiles_pending_plan_without_opening_training_gate(tmp_path):
    from tests.fixtures.training_task import task_archive, training_plan
    from tests.gateway.test_mjlab_node_jobs import _build_mjlab_task_package

    settings = remote_settings()
    versions = {"mjlab": "1.3.0", "rsl_rl": "5.0.1", "mujoco_warp": "3.8.1", "torch": "2.9.1"}
    gpu = {"id": "GPU-a", "name": "NVIDIA L4", "memoryTotalMb": 23034.0, "computeCapability": "8.9"}
    with TestClient(create_app(settings)) as client:
        first, second = register(client, "gpu-a"), register(client, "gpu-b")
        operator = OPERATOR | {"X-Actor": "agent:trainer"}
        task = client.post(
            "/api/v1/tasks",
            headers={**operator, "Content-Type": "application/x-tar"},
            content=task_archive(_build_mjlab_task_package(tmp_path / "task")),
        ).json()
        plan = client.post(
            "/api/v1/plans",
            headers=operator,
            json=training_plan(
                task["id"],
                algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
                hyperparameters={"num_envs": 128, "max_iterations": 100},
            ),
        ).json()
        request = {"planId": plan["id"], "seed": 42}
        assert client.post("/api/v1/jobs", headers=operator, json=request).status_code == 409
        assert beat(client, first, gpus=[gpu], trainerVersions=versions | {"rsl_rl": "4.0.0"}).is_success
        validate = request | {"kind": "plan_validation", "targetNodeId": "gpu-a"}
        validation = client.post("/api/v1/jobs", headers=operator, json=validate)
        assert validation.status_code == 202, validation.text
        job = validation.json()
        assert job["reason"] == "trainer_version_mismatch" and job["leaseId"] is None

        assert beat(client, first, gpus=[gpu], trainerVersions=versions).json()["assignments"] == []
        invalid = beat(client, first, gpus=[gpu], trainerVersions=versions, compilerHash="invalid")
        assert invalid.status_code == 422
        runtime = {"trainerVersions": versions, "compilerHash": COMPILER}
        (assignment,) = beat(client, first, gpus=[gpu], hostname="temp-a", **runtime).json()["assignments"]
        lease = first | {"X-Lease-Id": assignment["leaseId"]}
        inputs = f"/api/v1/jobs/{job['jobId']}/inputs"
        dispatch = client.get(inputs, headers=lease).json()
        assert dispatch["compilerHash"] == COMPILER
        assert dispatch["plan"]["hyperparameters"]["num_envs"] == 128
        assert dispatch["job"]["validationLimits"] == {"iterations": 3, "timeoutSeconds": 180}
        assert client.get(inputs, headers=second | {"X-Lease-Id": assignment["leaseId"]}).status_code == 403

        report = {
            "jobId": job["jobId"],
            "leaseId": assignment["leaseId"],
            "status": "succeeded",
            "elapsedSeconds": 10,
            "iteration": 3,
            "validation": {
                "numEnvs": 128,
                "samplesSeconds": [0.75, 1.0],
                "setupSeconds": 5.0,
                "warmupSeconds": 1.0,
            },
        }
        wrong = report | {"validation": report["validation"] | {"numEnvs": 8}}
        assert beat(client, first, gpus=[gpu], reports=[wrong], **runtime).status_code == 422
        assert beat(client, first, gpus=[gpu], reports=[report], **runtime).is_success
        plan_view = client.get(f"/api/v1/plans/{plan['id']}", headers=operator).json()
        assert plan_view["estimatedWallSeconds"] == 105
        assert plan_view["estimatedGpuHours"] == 105 / 3600
        approval_url = f"/api/v1/approvals/{plan['approvalId']}"
        approval = client.get(approval_url, headers=operator).json()
        assert approval["status"] == "pending"
        assert approval["details"]["estimatedGpuHours"] == 105 / 3600
        assert client.post("/api/v1/jobs", headers=operator, json=request).status_code == 409

        # A fresh machine with the same model can use the receipt despite a new hostname and GPU UUID.
        second_gpu = [gpu | {"id": "GPU-b"}]
        other_runtime = runtime | {"compilerHash": "b" * 64}
        assert beat(client, second, gpus=second_gpu, **other_runtime).is_success
        assert client.post(
            f"{approval_url}/decision", headers=OPERATOR, json={"decision": "approve"}
        ).is_success
        training = client.post(
            "/api/v1/jobs", headers=operator, json=request | {"targetNodeId": "gpu-b"}
        ).json()
        assert training["leaseId"] is None and training["reason"] == "estimate_unavailable"
        assert beat(client, second, hostname="temp-b", gpus=second_gpu, **runtime).is_success
        training = client.get(f"/api/v1/jobs/{training['jobId']}", headers=operator).json()
        assert training["nodeId"] == "gpu-b" and training["estimatedGpuHours"] == 105 / 3600
        assert int(training["leaseId"]) > int(assignment["leaseId"])
        old_lease = second | {"X-Lease-Id": training["leaseId"]}
        training_inputs = f"/api/v1/jobs/{training['jobId']}/inputs"
        assert client.get(training_inputs, headers=old_lease).is_success

        restarted = second | {"X-Node-Instance": "test-2"}
        assert beat(client, restarted, gpus=second_gpu, **runtime).is_success
        assert beat(client, second).status_code == 409
        assert client.get(training_inputs, headers=old_lease).status_code == 403
        stale = report | {
            "jobId": training["jobId"],
            "leaseId": training["leaseId"],
            "status": "failed",
            "validation": None,
        }
        assert beat(client, restarted, reports=[stale]).status_code == 403

        extra = client.post("/api/v1/jobs", headers=operator, json=request | {"targetNodeId": "gpu-b"}).json()
        assert int(extra["leaseId"]) > int(training["leaseId"])
        assert client.delete("/api/v1/nodes/gpu-b", headers=operator).status_code == 204
        assert beat(client, restarted).status_code in (401, 403)
        assert (
            client.get(f"/api/v1/jobs/{extra['jobId']}", headers=operator).json()["reason"] == "node_revoked"
        )

    with TestClient(create_app(settings)) as client:
        assert (
            client.get(f"/api/v1/plans/{plan['id']}", headers=OPERATOR).json()["estimatedGpuHours"]
            == 105 / 3600
        )


def test_gateway_cuda_session_does_not_block_automatic_remote_scheduling():
    from apps.api.app.sources.synthetic_source import SyntheticSource
    from packages.protocol.internal import SessionOptions
    from packages.training.jobs import node_profile_key
    from tests.fixtures.training_task import task_archive, training_plan
    from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, heartbeat

    class CaptureNewton(SyntheticSource):
        configure_scene = None

        async def start(self, scene_id, options):
            return await super().start("lab-demo", options)

    app = create_app(
        remote_settings(node_token=TOKEN), source_factory=lambda kind, identity: CaptureNewton(identity)
    )
    with TestClient(app) as client:
        session = client.portal.call(
            lambda: app.state.manager.create("falling-boxes", "newton", SessionOptions(device="cuda:0"))
        )
        assert session.options.device == "cuda:0"
        task = client.post(
            "/api/v1/tasks", headers=OPERATOR | {"Content-Type": "application/x-tar"}, content=task_archive()
        ).json()
        plan = client.post("/api/v1/plans", headers=OPERATOR, json=training_plan(task["id"])).json()
        assert client.post(
            f"/api/v1/approvals/{plan['approvalId']}/decision", headers=OPERATOR, json={"decision": "approve"}
        ).is_success
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task)).is_success
        job = client.post("/api/v1/jobs", headers=OPERATOR, json={"planId": plan["id"], "seed": 42}).json()
        assert job["reason"] == "session_gpu_busy"
        worker = register(client, "gpu-a")
        gpu = {"id": "GPU-remote", "name": "Remote GPU"}
        measured = {
            "iterationSeconds": 2.0,
            "samples": 2,
            "measuredAt": 10,
            "evidence": "contract double",
        }

        def announce(**versions):
            identity = {"id": "gpu-a", "hardware": "fixture", **versions}
            key = node_profile_key(plan, task["contentHash"], identity, gpu, backend="isaaclab")
            return beat(client, worker, gpus=[gpu], profiles=[measured | {"key": key}], **versions).json()

        assert announce()["assignments"] == []
        assert announce(trainerVersions=ISAAC_VERSIONS | {"newton": "1.7.0"})["assignments"] == []
        both = announce(trainerVersions={"mjlab": "1.3.0"}, isaaclabVersions=ISAAC_VERSIONS)
        (lease,) = both["assignments"]
        assert lease["jobId"] == job["jobId"]
        loaded = client.get(
            f"/api/v1/jobs/{job['jobId']}/inputs", headers=worker | {"X-Lease-Id": lease["leaseId"]}
        )
        assert loaded.json()["trainerVersions"] == ISAAC_VERSIONS
        assert (
            client.get(f"/api/v1/jobs/{job['jobId']}", headers=OPERATOR).json()["estimatedWallSeconds"] == 20
        )
        client.portal.call(session.close)
        fresh = client.portal.call(
            lambda: app.state.manager.create("falling-boxes", "newton", SessionOptions(device="cuda:0"))
        )
        assert fresh.options.device == "cuda:0"
        client.portal.call(fresh.close)

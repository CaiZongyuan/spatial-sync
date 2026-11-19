"""Public contracts for operators and outbound Compute Nodes."""

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

OPERATOR = {"Authorization": "Bearer operator-fixture"}
VIEWER = {"Authorization": "Bearer viewer-fixture"}
ISAAC_VERSIONS = {
    "isaaclab": "3.0.0", "newton": "1.6.0rc1", "warp": "1.17.0", "torch": "2.11.0+cu128", "rsl_rl": "5.4.1",
}
ISAAC_ENTRY = {"trainingEntries": {"isaaclab": "isaaclab:cli"}}
COMPILER = "a" * 64
MJLAB_VERSIONS = {
    "mjlab": "1.3.0", "rsl_rl": "5.0.1", "mujoco_warp": "3.8.1",
    "torch": "2.9.1", "mujoco": "4.0.1", "warp": "1.12.0",
}


def node_profile_key(plan, task_hash, node, resource, *, backend):
    from packages.training.runtime import runtime_profile_key, training_runtime

    runtime = training_runtime(node, {"plan": plan}, backend)
    return runtime_profile_key(plan, task_hash, node, resource, runtime)


def remote_settings(**kwargs):
    return Settings(remote=True, operator_token="operator-fixture", viewer_token="viewer-fixture", **kwargs)


def register(client, node_id, *, colocated=False):
    issued = client.post(
        "/api/v1/nodes/registrations", headers=OPERATOR, json={"nodeId": node_id, "colocated": colocated},
    )
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


@pytest.mark.parametrize("captured", [True, False], ids=["rtx3090-receipt", "small-projection"])
def test_validation_profiles_pending_plan_without_opening_training_gate(tmp_path, captured):
    from tests.fixtures.training_task import task_archive, training_plan
    from tests.gateway.test_mjlab_node_jobs import _build_mjlab_task_package

    settings = remote_settings()
    versions = MJLAB_VERSIONS
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
                hyperparameters={"num_envs": 128, "max_iterations": 10 if captured else 100},
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
        missing = {name: version for name, version in versions.items() if name != "mujoco"}
        entries = {"trainingEntries": {"mjlab": "mjlab:managed"}}
        for wrong_versions in (missing, versions | {"warp": "1.18.0"}, versions | {"rsl_rl": "5.0.1+local"}):
            assert beat(
                client, first, gpus=[gpu], trainerVersions=wrong_versions, compilerHash=COMPILER, **entries,
            ).json()["assignments"] == []
        runtime = {"trainerVersions": versions, "compilerHash": COMPILER, **entries}
        unsupported = runtime | {"trainingEntries": {"mjlab": "mjlab:custom"}}
        assert beat(client, first, gpus=[gpu], **unsupported).json()["assignments"] == []
        assert client.get(f"/api/v1/jobs/{job['jobId']}", headers=operator).json()["reason"] == (
            "trainer_entry_unsupported"
        )
        (assignment,) = beat(client, first, gpus=[gpu], hostname="temp-a", **runtime).json()["assignments"]
        assert beat(client, first, gpus=[gpu], reports=[{
            "jobId": job["jobId"], "leaseId": assignment["leaseId"], "status": "failed",
            "reason": "Validation produced non-finite PPO gradients or weights",
        }], **runtime).json()["assignments"] == []
        assert "estimatedGpuHours" not in client.get(f"/api/v1/plans/{plan['id']}", headers=operator).json()
        job = client.post("/api/v1/jobs", headers=operator, json=validate).json()
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
            "elapsedSeconds": 113.7138013809963,
            "iteration": 3,
            "validation": {
                "numEnvs": 128,
                "samplesSeconds": [1.4445639499972458, 1.4597025679977378],
                "setupSeconds": 90.10211506400083,
                "warmupSeconds": 3.818951481000113,
            },
        }
        if not captured:
            report.update(elapsedSeconds=10, validation={
                "numEnvs": 128, "setupSeconds": 5.0, "warmupSeconds": 1.0, "samplesSeconds": [0.75, 1.0],
            })
        wrong = report | {"validation": report["validation"] | {"numEnvs": 8}}
        assert beat(client, first, gpus=[gpu], reports=[wrong], **runtime).status_code == 422
        assert beat(
            client, first, gpus=[gpu], reports=[report | {"elapsedSeconds": 96.8 if captured else 7.0}],
            **runtime,
        ).status_code == 422
        for absent in ("elapsedSeconds", "iteration"):
            incomplete = {key: value for key, value in report.items() if key != absent}
            assert beat(client, first, gpus=[gpu], reports=[incomplete], **runtime).status_code == 422
        assert beat(client, first, gpus=[gpu], reports=[report], **runtime).is_success
        expected = 123.946857974983 if captured else 107.25
        plan_view = client.get(f"/api/v1/plans/{plan['id']}", headers=operator).json()
        assert plan_view["estimatedWallSeconds"] == pytest.approx(expected)
        assert plan_view["estimatedGpuHours"] == pytest.approx(expected / 3600)
        approval_url = f"/api/v1/approvals/{plan['approvalId']}"
        approval = client.get(approval_url, headers=operator).json()
        assert approval["status"] == "pending"
        assert approval["details"]["estimatedGpuHours"] == pytest.approx(expected / 3600)
        assert client.post("/api/v1/jobs", headers=operator, json=request).status_code == 409

        # A fresh machine with the same model can use the receipt despite a new hostname and GPU UUID.
        second_gpu = [gpu | {"id": "GPU-b"}]
        other_runtime = runtime | {"compilerHash": "b" * 64}
        suffixed = runtime | {"trainerVersions": versions | {"rsl_rl": "5.0.1+local"}}
        assert beat(client, second, gpus=second_gpu, **suffixed).is_success
        assert client.post(
            f"{approval_url}/decision", headers=OPERATOR, json={"decision": "approve"}
        ).is_success
        training = client.post(
            "/api/v1/jobs", headers=operator, json=request | {"targetNodeId": "gpu-b"}
        ).json()
        assert training["leaseId"] is None and training["reason"] == "trainer_version_mismatch"
        assert beat(client, second, gpus=second_gpu, **other_runtime).json()["assignments"] == []
        queued = client.get(f"/api/v1/jobs/{training['jobId']}", headers=operator).json()
        assert queued["reason"] == "estimate_unavailable"
        assert beat(client, second, hostname="temp-b", gpus=second_gpu, **runtime).is_success
        training = client.get(f"/api/v1/jobs/{training['jobId']}", headers=operator).json()
        assert training["nodeId"] == "gpu-b"
        assert training["estimatedGpuHours"] == pytest.approx(expected / 3600)
        assert int(training["leaseId"]) > int(assignment["leaseId"])
        (lease_assignment,) = beat(client, second, gpus=second_gpu, **runtime).json()["assignments"]
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

        receipt = {
            "schemaVersion": 1, "nodeId": "gpu-b", "instanceId": second["X-Node-Instance"],
            "jobId": training["jobId"], "leaseId": training["leaseId"], "gpuId": "GPU-b",
        }
        assert client.post(
            "/api/v1/nodes/cleanup", headers={"X-Node-Cleanup-Token": lease_assignment["cleanup"]["token"]},
            json=receipt,
        ).status_code == 204
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
            == pytest.approx(expected / 3600)
        )


def test_pending_job_keeps_its_explicit_node_target():
    from tests.fixtures.training_task import task_archive, training_plan

    with TestClient(create_app(remote_settings())) as client:
        task = client.post(
            "/api/v1/tasks", headers=OPERATOR | {"Content-Type": "application/x-tar"}, content=task_archive(),
        ).json()
        document = training_plan(task["id"], budget={"gpuHours": 0.2})
        plan = client.post("/api/v1/plans", headers=OPERATOR, json=document).json()
        approval_url = f"/api/v1/approvals/{plan['approvalId']}/decision"
        assert client.post(approval_url, headers=OPERATOR, json={"decision": "approve"}).is_success
        target, other = register(client, "original"), register(client, "another")

        def announce(headers, gpu, rate, measured):
            node = {"id": headers["X-Node-Id"], "hardware": "fixture",
                    "trainerVersions": ISAAC_VERSIONS, **ISAAC_ENTRY}
            key = node_profile_key(plan, task["contentHash"], node, gpu, backend="isaaclab")
            profile = {
                "key": key, "iterationSeconds": rate, "samples": 2, "measuredAt": measured,
                "evidence": "fixture",
            }
            return beat(client, headers, gpus=[gpu], profiles=[profile],
                        trainerVersions=ISAAC_VERSIONS, **ISAAC_ENTRY).json()["assignments"]

        original_card = {"id": "GPU-original", "name": "Original card"}
        assert announce(target, original_card, 108, 100) == []
        request = {"planId": plan["id"], "seed": 42, "targetNodeId": "original", "requestId": "targeted"}
        waiting = client.post("/api/v1/jobs", headers=OPERATOR, json=request).json()
        assert waiting["reason"] == "pending_approval"
        assert announce(other, {"id": "GPU-another", "name": "Cheaper card"}, 18, 101) == []
        seen = client.post("/api/v1/jobs", headers=OPERATOR, json=request).json()
        assert seen["jobId"] == waiting["jobId"] and seen["approvalId"] == waiting["approvalId"]
        (lease,) = announce(target, original_card, 18, 102)
        assert lease["jobId"] == waiting["jobId"]


def test_gateway_cuda_session_does_not_block_automatic_remote_scheduling():
    from apps.api.app.sources.synthetic_source import SyntheticSource
    from packages.protocol.internal import SessionOptions
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

        identity = {"id": "gpu-a", "hardware": "fixture", "trainerVersions": ISAAC_VERSIONS, **ISAAC_ENTRY}
        key = node_profile_key(plan, task["contentHash"], identity, gpu, backend="isaaclab")

        def announce(**versions):
            return beat(client, worker, gpus=[gpu], profiles=[measured | {"key": key}], **versions).json()

        assert announce()["assignments"] == []
        assert announce(trainerVersions=ISAAC_VERSIONS | {"newton": "1.7.0"})["assignments"] == []
        suffixed = ISAAC_VERSIONS | {"rsl_rl": "5.4.1+local"}
        assert announce(trainerVersions=suffixed, **ISAAC_ENTRY)["assignments"] == []
        both = announce(trainerVersions={"mjlab": "1.3.0"}, isaaclabVersions=ISAAC_VERSIONS, **ISAAC_ENTRY)
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
        assert client.delete("/api/v1/nodes/gpu-a", headers=OPERATOR).status_code == 204
        fresh = client.portal.call(
            lambda: app.state.manager.create("falling-boxes", "newton", SessionOptions(device="cuda:0"))
        )
        assert fresh.options.device == "cuda:0"
        client.portal.call(fresh.close)


@pytest.mark.parametrize("fence", ["revoke", "replace", "expire", "duplicate_uuid"])
def test_colocated_gpu_releases_only_for_its_cleanup_receipt(fence):
    from apps.api.app.sources.synthetic_source import SyntheticSource
    from packages.protocol.internal import SessionOptions
    from tests.fixtures.training_task import task_archive, training_plan

    class CaptureNewton(SyntheticSource):
        configure_scene = None

        async def start(self, scene_id, options):
            return await super().start("lab-demo", options)

    settings = remote_settings()
    now = [100.0]

    def gateway():
        app = create_app(settings, source_factory=lambda kind, identity: CaptureNewton(identity))
        app.state.jobs.clock = lambda: now[0]
        return TestClient(app)

    def session_device(client):
        async def read():
            manager = client.app.state.manager
            session = await manager.create("falling-boxes", "newton", SessionOptions(device="cuda:0"))
            try:
                return session.options.device
            finally:
                await session.close()

        return client.portal.call(read)

    url = "/api/v1/nodes/cleanup"
    with gateway() as client:
        task = client.post(
            "/api/v1/tasks", headers=OPERATOR | {"Content-Type": "application/x-tar"}, content=task_archive()
        ).json()
        plan = client.post("/api/v1/plans", headers=OPERATOR, json=training_plan(task["id"])).json()
        approval_url = f"/api/v1/approvals/{plan['approvalId']}/decision"
        assert client.post(approval_url, headers=OPERATOR, json={"decision": "approve"}).is_success
        original = register(client, "workstation", colocated=fence != "duplicate_uuid")
        gpu = {"id": "GPU-shared", "name": "Fixture CUDA GPU", "memoryUsedMb": 0.0}

        def announce(client, headers):
            identity = {
                "id": headers["X-Node-Id"], "hardware": "fixture",
                "trainerVersions": ISAAC_VERSIONS, **ISAAC_ENTRY,
            }
            key = node_profile_key(plan, task["contentHash"], identity, gpu, backend="isaaclab")
            profile = {
                "key": key, "iterationSeconds": 1.0, "samples": 2, "measuredAt": now[0], "evidence": "fake",
            }
            return beat(client, headers, gpus=[gpu], trainerVersions=ISAAC_VERSIONS,
                        profiles=[profile], **ISAAC_ENTRY).json()["assignments"]

        def queue(client, node_id, seed):
            response = client.post(
                "/api/v1/jobs", headers=OPERATOR,
                json={"planId": plan["id"], "targetNodeId": node_id, "seed": seed},
            )
            assert response.status_code == 202, response.text
            return response.json()

        assert announce(client, original) == []
        first = queue(client, "workstation", 42)
        (assignment,) = announce(client, original)
        old = original | {"X-Lease-Id": first["leaseId"]}
        job_url = f"/api/v1/jobs/{first['jobId']}"
        assert client.get(f"{job_url}/inputs", headers=old).is_success
        cleanup = assignment["cleanup"]
        receipt = {
            "schemaVersion": cleanup["schemaVersion"], "nodeId": original["X-Node-Id"],
            "instanceId": original["X-Node-Instance"], "jobId": first["jobId"],
            "leaseId": first["leaseId"], "gpuId": gpu["id"],
        }
        proof = {"X-Node-Cleanup-Token": cleanup["token"]}
        assert client.post(url, headers=proof, json=receipt).status_code == 409
        replacement = original | {"X-Node-Instance": "test-2"}
        if fence == "duplicate_uuid":
            replacement = register(client, "replacement")
            assert announce(client, replacement) == []
            assert queue(client, "replacement", 43)["leaseId"] is None
            assert session_device(client) == "cuda:0"
        if fence in ("revoke", "duplicate_uuid"):
            assert client.delete("/api/v1/nodes/workstation", headers=OPERATOR).status_code == 204
            if fence == "revoke":
                replacement = register(client, "replacement", colocated=True)
        if fence == "expire":
            now[0] += 61
        assert announce(client, replacement) == []
        if fence != "duplicate_uuid":
            assert queue(client, replacement["X-Node-Id"], 43)["leaseId"] is None
        assert session_device(client) == ("cuda:0" if fence == "duplicate_uuid" else "cpu")
        stale = {"jobId": first["jobId"], "leaseId": first["leaseId"], "status": "cleaned", "logs": ["stale"]}
        assert beat(client, original, reports=[stale]).status_code in (401, 403, 409)
        assert beat(client, original, reports=[stale | {"tailEnd": 1}]).status_code in (401, 403, 409)
        assert client.get(f"{job_url}/inputs", headers=old).status_code in (401, 403)
        assert client.post(f"{job_url}/artifacts", headers=old, content=b"stale").status_code in (401, 403)
        assert client.get(f"{job_url}/logs", headers=OPERATOR).json()["lines"] == []
        assert cleanup["token"] not in client.get("/api/v1/nodes", headers=OPERATOR).text
        assert cleanup["token"] not in client.get(job_url, headers=OPERATOR).text

    with gateway() as client:
        now[0] += 900
        assert announce(client, replacement) == []
        assert session_device(client) == ("cuda:0" if fence == "duplicate_uuid" else "cpu")
        nodes = client.get("/api/v1/nodes", headers=OPERATOR).json()["nodes"]
        old_node = next(node for node in nodes if node["id"] == "workstation")
        (held,) = old_node["quarantinedGpus"]
        assert (held["jobId"], held["gpuId"], held["instanceId"], held["releaseAt"]) == (
            first["jobId"], gpu["id"], original["X-Node-Instance"], None,
        )
        assert cleanup["token"] not in str(nodes)
        assert client.post(url, headers=original, json=receipt).status_code == 403
        bad = receipt | {"instanceId": "test-2"}
        assert client.post(url, headers=proof, json=bad).status_code == 403
        bad = receipt | {"gpuId": "GPU-other"}
        assert client.post(url, headers=proof, json=bad).status_code == 403
        assert client.get("/api/v1/nodes", headers=proof).status_code == 401
        assert announce(client, replacement) == []
        assert client.post(url, headers=proof, json=receipt).status_code == 204
        (new_lease,) = announce(client, replacement)
        assert new_lease["leaseId"] != receipt["leaseId"]
        assert client.post(url, headers=proof, json=receipt).status_code == 204
        wrong_lease = receipt | {"leaseId": new_lease["leaseId"]}
        assert client.post(url, headers=proof, json=wrong_lease).status_code == 403
        assert announce(client, replacement) == [new_lease]

    with gateway() as client:
        assert client.post(url, headers=proof, json=receipt).status_code == 204
        assert announce(client, replacement) == [new_lease]


@pytest.mark.parametrize("modern", [False, True], ids=["retained-local", "modern-local"])
def test_legacy_mjlab_profile_is_only_used_by_pre_handshake_local_training(tmp_path, modern):
    from packages.training.jobs import profile_key
    from tests.fixtures.training_task import task_archive, training_plan
    from tests.gateway.test_mjlab_node_jobs import _build_mjlab_task_package
    from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, approve

    with TestClient(create_app(Settings(node_token=TOKEN))) as client:
        package = task_archive(_build_mjlab_task_package(tmp_path / "task"))
        task = client.post(
            "/api/v1/tasks", headers={"Content-Type": "application/x-tar"}, content=package,
        ).json()
        plan = client.post("/api/v1/plans", json=training_plan(
            task["id"], algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
        )).json()
        approve(client, plan["approvalId"])
        old_hardware = "9" * 64
        gpu = {"id": "GPU-local", "name": "fixture"}
        beat_body = {
            "instanceId": NODE["X-Node-Instance"], "hardware": old_hardware, "gpus": [gpu],
            "profiles": [{"key": profile_key(plan, task["contentHash"], old_hardware, gpu["id"]),
                          "iterationSeconds": 1.0, "samples": 1, "measuredAt": 100.0, "evidence": "legacy"}],
        }
        if modern:
            beat_body.update(
                hardware="node-hardware/2:fixture", legacyHardware=old_hardware, compilerHash=COMPILER,
                trainerVersions=MJLAB_VERSIONS, trainingEntries={"mjlab": "mjlab:managed"},
            )
        assert client.post(HEARTBEAT, headers=NODE, json=beat_body).is_success
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
        assert job["reason"] == ("estimate_unavailable" if modern else "node_starting")
        assert (job["leaseId"] is None) == modern


def test_retained_isaac_job_import_resolves_the_same_backend_as_dispatch():
    import json

    from packages.training.profiles import measured_profile
    from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, setup_plan

    settings = Settings(node_token=TOKEN)
    with TestClient(create_app(settings)) as client:
        plan, task = setup_plan(client)
        approve(client, plan["approvalId"])
        job = client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42}).json()
    path = settings.state_root / "jobs.json"
    retained = json.loads(path.read_bytes())
    del retained["jobs"][job["jobId"]]["backend"]
    path.write_text(json.dumps(retained), encoding="utf-8")

    with TestClient(create_app(settings)) as client:
        hardware = "node-hardware/2:fixture"
        heartbeat = {
            "instanceId": NODE["X-Node-Instance"], "hardware": hardware, "gpus": [
                {"id": "GPU-local", "name": "fixture"},
            ],
            "trainerVersions": MJLAB_VERSIONS, "isaaclabVersions": ISAAC_VERSIONS,
            "compilerHash": COMPILER, **ISAAC_ENTRY,
        }
        assert client.post(HEARTBEAT, headers=NODE, json=heartbeat).is_success
        node = client.get("/api/v1/nodes/local").json()
        retained_job = client.get(f"/api/v1/jobs/{job['jobId']}").json()
        name, revision = task["id"].removeprefix("task/").split("@")
        pinned_task = client.get(f"/api/v1/tasks/{name}/{revision}").json()["task"]
        identity = {
            "backend": "isaaclab", "entry": "isaaclab:cli", "compilerHash": None,
            "trainerVersions": ISAAC_VERSIONS,
        }
        raw = {
            "schema": "lw.training-profile/2", "taskContentHash": task["contentHash"],
            **{name: plan[name] for name in ("algorithm", "model", "hyperparameters")},
            "hardware": hardware, "resourceId": "GPU-local", "samplesSeconds": [1, 2],
            "setupSeconds": 5, "dispatchOverheadSeconds": 3, "runtimeIdentity": identity, "measuredAt": 10,
        }
        profile, _ = measured_profile(
            json.dumps(raw).encode(), retained_job, plan, node, "GPU-local", task=pinned_task,
        )
        reply = client.post(HEARTBEAT, headers=NODE, json=heartbeat | {"profiles": [profile]}).json()
        (lease,) = reply["assignments"]
        assert lease["jobId"] == job["jobId"]
        loaded = client.get(
            f"/api/v1/jobs/{job['jobId']}/inputs", headers=NODE | {"X-Lease-Id": lease["leaseId"]},
        )
        assert loaded.json()["runtimeIdentity"] == identity

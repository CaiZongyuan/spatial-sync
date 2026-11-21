"""Plan admission and leased calibration receipts for managed reference tracking."""

import hashlib
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.node.mjlab_launcher import compiler_hash
from packages.policies.qualification import verify_initialized_normalizer
from packages.training import jobs
from packages.training.checkpoints import identity as checkpoint_identity
from packages.training.runtime import (
    INITIALIZATION_R2_COMPILER,
    INITIALIZED_CHECKPOINT_MIGRATION_HEADER,
    PRE_INITIALIZATION_COMPILER,
    compatible_checkpoint_runtime,
    profile_key,
)
from tests.fixtures.training_task import training_plan
from tests.gateway.test_checkpoint_jobs import checkpoint_archive
from tests.gateway.test_reference_motion import URL, imitation_request, keyframes
from tests.gateway.test_remote_training_nodes import (
    MJLAB_VERSIONS,
    OPERATOR,
    beat,
    node_profile_key,
    register,
    remote_settings,
)


def sign(plan):
    init = {key: value for key, value in plan["initialization"].items() if key != "sha256"}
    raw = {
        "initialization": init,
        "model": plan["model"],
        "learningRate": float(plan["hyperparameters"]["learning_rate"]),
    }
    plan["initialization"]["sha256"] = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return plan


def test_initialized_checkpoint_migration_requires_current_node_advertisement(monkeypatch):
    app = create_app(remote_settings())
    now = [1000.0]
    app.state.jobs.clock = lambda: now[0]
    with TestClient(app) as client:
        reference = client.post(URL, headers=OPERATOR, json=keyframes(app.state.manager.library_assets))
        assert reference.status_code == 201, reference.text
        response = client.post("/api/v1/tasks/reference-motion", headers=OPERATOR,
                               json=imitation_request(reference.json()))
        assert response.status_code == 201, response.text
        task = response.json()
        raw = app.state.manager.tasks.observation_bytes(task)
        width = sum(term["shape"][0] for term in json.loads(raw)["observations"])
        plan = sign(training_plan(
            task["id"],
            algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
            model={"actor": [32, 16], "critic": [32, 16], "activation": "elu", "obs_normalization": True},
            hyperparameters={"num_envs": 4, "max_iterations": 20, "learning_rate": 1e-5, "device": "cpu"},
            evaluation={"scene": task["task"]["scene"], "episodes": 30, "threshold": 24},
            initialization={
                "schema": "lw.reference-actor-init/1", "taskContentHash": task["contentHash"],
                "referenceMotionSha256": task["task"]["referenceMotionRef"]["sha256"],
                "observationSha256": hashlib.sha256(raw).hexdigest(), "shift": 8.5, "initialStd": 0.1,
                "fitActionAbsMax": 0.5,
                "normalization": {"mean": [0.0] * width, "variance": [1.0] * width, "count": 3},
            },
        ))
        created = client.post("/api/v1/plans", headers=OPERATOR, json=plan)
        assert created.status_code == 201, created.text
        plan = created.json()
        approval = client.post(f"/api/v1/approvals/{plan['approvalId']}/decision", headers=OPERATOR,
                               json={"decision": "approve"})
        assert approval.status_code == 200, approval.text

        def capabilities(compiler):
            identity = {
                "compilerHash": compiler, "trainerVersions": MJLAB_VERSIONS,
                "trainingEntries": {"mjlab": "mjlab:managed"},
            }
            node = identity | {"id": "fixture", "hardware": "fixture"}
            key = node_profile_key(plan, task["contentHash"], node, {"id": "cpu"}, backend="mjlab")
            return identity | {
                "checkpointVersion": 1, "cpuAvailable": True,
                "profiles": [{"key": key, "iterationSeconds": 1, "samples": 2,
                              "measuredAt": 999, "evidence": "fixture"}],
            }

        def start(seed, node):
            reply = client.post("/api/v1/jobs", headers=OPERATOR,
                                json={"planId": plan["id"], "seed": seed, "targetNodeId": node["X-Node-Id"]})
            assert reply.status_code == 202, reply.text
            return reply.json()

        def commit(job, node):
            lease = node | {"X-Lease-Id": job["leaseId"]}
            url = f"/api/v1/jobs/{job['jobId']}"
            dispatch = client.get(url + "/inputs", headers=lease)
            assert dispatch.status_code == 200, dispatch.text
            archive = checkpoint_archive(dispatch.json())
            reply = client.post(url + "/checkpoints", headers=lease, content=archive)
            assert reply.status_code == 201, reply.text
            return reply.json()

        old, updated = register(client, "older"), register(client, "updated")
        prior = capabilities(INITIALIZATION_R2_COMPILER)
        with monkeypatch.context() as r2:
            r2.setattr(jobs, "compiler_hash", lambda: INITIALIZATION_R2_COMPILER)
            assert beat(client, old, **prior).is_success
            source = start(42, old)
            checkpoint = commit(source, old)

        current = capabilities(compiler_hash())
        now[0] += 61  # Expire the R2 connection with a committed checkpoint.
        read = client.get(f"/api/v1/jobs/{source['jobId']}", headers=OPERATOR)
        assert read.json()["status"] == "suspended"
        old |= {"X-Node-Instance": "after-r2"}
        reply = beat(client, old, **current)
        assert reply.status_code == 200 and reply.json()["assignments"] == []
        assert beat(client, updated | {INITIALIZED_CHECKPOINT_MIGRATION_HEADER: "2"},
                    **current).json()["assignments"] == []

        finalize = client.post("/api/v1/jobs", headers=OPERATOR, json={
            "kind": "checkpoint_finalize", "planId": plan["id"],
            "checkpoint": f"{source['jobId']}/{checkpoint['sha256']}",
        })
        assert finalize.status_code == 202 and finalize.json()["leaseId"] is None
        # The same older node still runs an ordinary Job and owns its checkpoint.
        ordinary = start(43, old)
        assert ordinary["nodeId"] == old["X-Node-Id"]
        same_runtime_checkpoint = commit(ordinary, old)

        capable = updated | {INITIALIZED_CHECKPOINT_MIGRATION_HEADER: "1"}
        reply = beat(client, capable, **current)
        assert reply.status_code == 200, reply.text
        (assignment,) = reply.json()["assignments"]
        assert assignment["jobId"] == source["jobId"] and assignment["startIteration"] == 4
        lease = capable | {"X-Lease-Id": assignment["leaseId"]}
        path = f"/api/v1/jobs/{source['jobId']}/inputs"
        dispatch = client.get(path, headers=lease).json()
        assert dispatch["resumeCheckpoint"]["sha256"] == checkpoint["sha256"]
        assert dispatch["checkpointIdentity"]["inputsSha256"] == checkpoint["inputsSha256"]
        assert beat(client, updated, **current).json()["assignments"] == []
        assert client.get(path, headers=lease).status_code == 403

        cancelled = client.post(f"/api/v1/jobs/{ordinary['jobId']}/cancel", headers=OPERATOR)
        assert cancelled.status_code == 200, cancelled.text
        report = {"jobId": ordinary["jobId"], "leaseId": ordinary["leaseId"],
                  "status": "cancelled", "iteration": 4}
        assert beat(client, old, **current, reports=[report]).is_success
        same_runtime = client.post("/api/v1/jobs", headers=OPERATOR, json={
            "kind": "checkpoint_finalize", "planId": plan["id"], "targetNodeId": old["X-Node-Id"],
            "checkpoint": f"{ordinary['jobId']}/{same_runtime_checkpoint['sha256']}",
        })
        assert same_runtime.status_code == 202 and same_runtime.json()["nodeId"] == old["X-Node-Id"]


@pytest.mark.parametrize(
    "offsets,action_scale", [((-999999.0, 999999.0), 0.05), ((999999.0, -999998.0), 0.35),
                             ((0.1, 0.0), 0.0001), ((0.0, 0.0), 1e5)],
)
def test_ill_conditioned_reference_actor_is_rejected_before_approval(offsets, action_scale):
    app = create_app(remote_settings())
    with TestClient(app) as client:
        manager = app.state.manager
        reference = client.post(URL, headers=OPERATOR, json=keyframes(manager.library_assets))
        assert reference.status_code == 201, reference.text
        body = imitation_request(reference.json())
        observation = body["observation"]
        observation["action"]["scale"][0] = action_scale
        terms = {term["source"]: term for term in observation["observations"]}
        terms["joint_position"]["offset"][0], terms["reference_motion_frame"]["offset"][7] = offsets
        response = client.post("/api/v1/tasks/reference-motion", headers=OPERATOR, json=body)
        assert response.status_code == 201, response.text
        task = response.json()
        raw = manager.tasks.observation_bytes(task)
        spec = json.loads(raw)
        means = [0.0] * sum(term["shape"][0] for term in spec["observations"])
        cursor = 0
        for term in spec["observations"]:
            if term["source"] == "joint_position":
                means[cursor] = -offsets[0]
            if term["source"] == "reference_motion_frame":
                means[cursor + 7] = -offsets[1]
            cursor += term["shape"][0]
        plan = sign(training_plan(
            task["id"],
            algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
            model={"actor": [32, 16], "critic": [32, 16], "activation": "elu", "obs_normalization": True},
            hyperparameters={"num_envs": 4, "max_iterations": 20, "learning_rate": 1e-5, "device": "cpu"},
            evaluation={"scene": task["task"]["scene"], "episodes": 30, "threshold": 24},
            initialization={
                "schema": "lw.reference-actor-init/1", "taskContentHash": task["contentHash"],
                "referenceMotionSha256": task["task"]["referenceMotionRef"]["sha256"],
                "observationSha256": hashlib.sha256(raw).hexdigest(), "shift": 8.0, "initialStd": 0.1,
                "fitActionAbsMax": 0.0,
                "normalization": {"mean": means, "variance": [0.0] * len(means), "count": 2},
            },
        ))
        rejected = client.post("/api/v1/plans", headers=OPERATOR, json=plan)
        assert rejected.status_code == 422, rejected.text
        assert "float32" in rejected.text
        assert client.get("/api/v1/approvals", headers=OPERATOR).json()["pendingCount"] == 0


def test_reference_initialization_is_checked_before_lease_and_reported_after_validation():
    settings = remote_settings()
    app = create_app(settings)
    with TestClient(app) as client:
        manager = app.state.manager
        response = client.post(URL, headers=OPERATOR, json=keyframes(manager.library_assets))
        assert response.status_code == 201, response.text
        response = client.post("/api/v1/tasks/reference-motion", headers=OPERATOR,
                               json=imitation_request(response.json()))
        assert response.status_code == 201, response.text
        task = response.json()
        raw = manager.tasks.observation_bytes(task)
        width = sum(term["shape"][0] for term in json.loads(raw)["observations"])
        plan = sign(training_plan(
            task["id"],
            algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
            model={"actor": [32, 16], "critic": [32, 16], "activation": "elu", "obs_normalization": True},
            hyperparameters={"num_envs": 4, "max_iterations": 20, "learning_rate": 1e-5, "device": "cpu"},
            evaluation={"scene": task["task"]["scene"], "episodes": 30, "threshold": 24},
            initialization={
                "schema": "lw.reference-actor-init/1", "taskContentHash": task["contentHash"],
                "referenceMotionSha256": task["task"]["referenceMotionRef"]["sha256"],
                "observationSha256": hashlib.sha256(raw).hexdigest(), "shift": 8.5, "initialStd": 0.1,
                "fitActionAbsMax": 0.5,
                "normalization": {"mean": [0.0] * width, "variance": [1.0] * width, "count": 3},
            },
        ))

        for key in ("taskContentHash", "referenceMotionSha256", "observationSha256", "sha256"):
            wrong = deepcopy(plan)
            wrong["initialization"][key] = "0" * 64
            response = client.post(
                "/api/v1/plans", headers=OPERATOR, json=wrong if key == "sha256" else sign(wrong),
            )
            assert response.status_code == 422, response.text
        wrong = deepcopy(plan)
        wrong["initialization"]["normalization"]["variance"][0] = -1
        response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(wrong))
        assert response.status_code == 422, response.text
        wrong = deepcopy(plan)
        wrong["hyperparameters"]["schedule"] = "adaptive"
        response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(wrong))
        assert response.status_code == 422, response.text
        for shift in (0.001, 8.499):
            wrong = deepcopy(plan)
            wrong["initialization"]["shift"] = shift
            response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(wrong))
            assert response.status_code == 422, response.text
        wrong = deepcopy(plan)
        wrong["initialization"]["shift"] = 1000.0
        response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(wrong))
        assert response.status_code == 422 and "float32 folding" in response.text
        wrong = deepcopy(plan)
        cursor = 0
        for term in json.loads(raw)["observations"]:
            if term["source"] == "joint_position":
                wrong["initialization"]["normalization"]["mean"][cursor] = 999999.0
                break
            cursor += term["shape"][0]
        response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(wrong))
        assert response.status_code == 422 and "float32 folding" in response.text
        missing = deepcopy(plan)
        del missing["initialization"]["fitActionAbsMax"]
        response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(missing))
        assert response.status_code == 422, response.text
        for envs, batches in ((1, 1), (2, 4), (2, None)):
            wrong = deepcopy(plan)
            wrong["hyperparameters"].update(num_envs=envs, num_steps_per_env=1)
            if batches is not None:
                wrong["hyperparameters"]["num_mini_batches"] = batches
            response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(wrong))
            assert response.status_code == 422, response.text
        assert client.get("/api/v1/approvals", headers=OPERATOR).json()["pendingCount"] == 0

        # The pinned PPO normalizes advantage over the whole rollout.
        edge = deepcopy(plan)
        edge["hyperparameters"].update(num_envs=2, num_steps_per_env=1, num_mini_batches=2)
        response = client.post("/api/v1/plans", headers=OPERATOR, json=sign(edge))
        assert response.status_code == 201, response.text
        response = client.post("/api/v1/plans", headers=OPERATOR, json=plan)
        assert response.status_code == 201, response.text
        submitted = response.json()
        assert submitted["initialization"] == plan["initialization"]
        job = client.post("/api/v1/jobs", headers=OPERATOR, json={
            "kind": "plan_validation", "planId": submitted["id"],
        })
        assert job.status_code == 202, job.text
        job_id = job.json()["jobId"]

        node = register(client, "managed-init")
        capabilities = {
            "cpuAvailable": True, "trainerVersions": MJLAB_VERSIONS,
            "trainingEntries": {"mjlab": "mjlab:managed"},
            "compilerHash": PRE_INITIALIZATION_COMPILER,
        }
        assert beat(client, node, **capabilities).json()["assignments"] == []
        assert client.get(f"/api/v1/jobs/{job_id}", headers=OPERATOR).json()["reason"] == (
            "initialization_runtime_unsupported"
        )
        capabilities["compilerHash"] = compiler_hash()
        assert beat(client, node, **capabilities).json()["assignments"] == []
        capabilities["checkpointVersion"] = 1
        (assignment,) = beat(client, node, **capabilities).json()["assignments"]
        inputs = client.get(f"/api/v1/jobs/{job_id}/inputs",
                            headers=node | {"X-Lease-Id": assignment["leaseId"]}).json()
        assert inputs["plan"]["initialization"] == plan["initialization"]
        assert inputs["job"]["validationLimits"] == {"iterations": 3, "timeoutSeconds": 180}

        init = {key: value for key, value in plan["initialization"].items() if key != "normalization"}
        init.update(
            normalizationCount=3, normalizationWidth=width, normalizationFrozen=True,
            schedule="fixed", learningRate=1e-5, model=plan["model"],
        )
        lease_record = {"leaseId": assignment["leaseId"], "runtimeIdentity": inputs["runtimeIdentity"]}
        fixed = inputs["job"] | {"plan": plan}
        without_init = {key: value for key, value in plan.items() if key != "initialization"}
        plain = fixed | {"plan": without_init}
        assert checkpoint_identity(fixed, lease_record)["inputsSha256"] != (
            checkpoint_identity(plain, lease_record)["inputsSha256"]
        )
        assert profile_key(plan, task["contentHash"], "fixture", "cpu") != (
            profile_key(without_init, task["contentHash"], "fixture", "cpu")
        )
        older = inputs["runtimeIdentity"] | {"compilerHash": PRE_INITIALIZATION_COMPILER}
        assert compatible_checkpoint_runtime(older, inputs["runtimeIdentity"], without_init, compiler_hash())
        assert not compatible_checkpoint_runtime(older, inputs["runtimeIdentity"], plan, compiler_hash())

        stats = {"_mean": [[0.0] * width], "_var": [[1.0] * width], "_std": [[1.0] * width], "count": 3}
        verify_initialized_normalizer(plan, {"initialization": init}, stats)
        with pytest.raises(ValueError, match="approved frozen statistics"):
            verify_initialized_normalizer(plan, {"initialization": init}, stats | {"count": 4})

        report = {
            "jobId": job_id, "leaseId": assignment["leaseId"], "status": "succeeded", "iteration": 3,
            "elapsedSeconds": 6.0,
            "validation": {
                "numEnvs": 4, "setupSeconds": 1.0, "warmupSeconds": 1.0, "samplesSeconds": [1.0, 1.0],
            },
        }
        assert beat(client, node, reports=[report], **capabilities).status_code == 422
        report["validation"]["initialization"] = init
        assert beat(client, node, reports=[report], **capabilities).status_code == 200

    with TestClient(create_app(settings)) as client:
        saved = client.get(f"/api/v1/jobs/{job_id}", headers=OPERATOR).json()
        assert saved["status"] == "succeeded" and saved["initialization"] == init
        assert saved["validationEstimate"]["estimatedGpuHours"] == 0
        plan_url = f"/api/v1/plans/{submitted['id']}"
        legacy = {key: value for key, value in plan.items() if key != "initialization"}
        revised = client.post(f"{plan_url}/revisions", headers=OPERATOR, json=legacy)
        assert revised.status_code == 201, revised.text
        revised = revised.json()
        assert "initialization" not in revised
        removed = {"field": "initialization", "before": plan["initialization"], "after": None}
        assert removed in revised["changes"]
        original = client.get(f"{plan_url}?revision=1", headers=OPERATOR).json()
        assert original["initialization"] == plan["initialization"]
        assert original["goalSuite"] == revised["goalSuite"]

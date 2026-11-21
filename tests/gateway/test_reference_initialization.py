"""Plan admission and leased calibration receipts for managed reference tracking."""

import hashlib
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.node.mjlab_launcher import compiler_hash
from packages.policies.qualification import verify_initialized_normalizer
from packages.training.checkpoints import identity as checkpoint_identity
from packages.training.runtime import PRE_INITIALIZATION_COMPILER, compatible_checkpoint_runtime, profile_key
from tests.fixtures.training_task import training_plan
from tests.gateway.test_reference_motion import URL, imitation_request, keyframes
from tests.gateway.test_remote_training_nodes import MJLAB_VERSIONS, OPERATOR, beat, register, remote_settings


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
                "observationSha256": hashlib.sha256(raw).hexdigest(), "shift": 8.0, "initialStd": 0.1,
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
        assert client.get("/api/v1/approvals", headers=OPERATOR).json()["pendingCount"] == 0

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

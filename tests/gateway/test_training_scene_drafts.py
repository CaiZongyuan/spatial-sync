"""Public deterministic Scene Draft publication."""

import json
import os
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from tests.fixtures.training_scene import draft


def test_scene_draft_publishes_exact_baked_primitive_geometry():
    with TestClient(create_app()) as http:
        value = draft()
        dressing = http.get("/api/v1/library/assets/environment/fr3-culture-laboratory-1/1").json()
        value["assets"] = [
            {
                "name": "Backdrop",
                "asset": "environment/fr3-culture-laboratory-1@1",
                "contentHash": dressing["contentHash"],
            }
        ]
        staged = http.post("/api/v1/library/scene-drafts", json=value)
        assert staged.status_code == 201, staged.text
        identifier = staged.json()["id"]
        url = f"/api/v1/library/scene-drafts/{identifier}/publish"
        published = http.post(url)
        assert published.status_code == 200, published.text
        result = published.json()
        assert result["sceneVersion"] == "training-stairs@1"
        assert http.post(url).json()["sceneVersion"] == result["sceneVersion"]
        stages = http.get("/api/v1/library/files/user/scenes/training-stairs/1/stage.usda")
        assert stages.is_success, stages.text
        from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

        layer = Sdf.Layer.CreateAnonymous()
        assert layer.ImportFromString(stages.text)
        stage = Usd.Stage.Open(layer)
        assert stage.GetPrimAtPath("/World/Dressing/Backdrop").HasAuthoredReferences()
        assert not stage.GetPrimAtPath("/World/Lab/Backdrop")
        cache = UsdGeom.XformCache()
        step = stage.GetPrimAtPath("/World/Lab/Terrain/Step2")
        shifted_top = cache.GetLocalToWorldTransform(step).Transform(Gf.Vec3d(0, 0, 1))
        assert tuple(shifted_top) == pytest.approx((1.5, 0.0, 0.2))
        ball = stage.GetPrimAtPath("/World/Lab/Ball")
        assert UsdPhysics.RigidBodyAPI(ball)
        assert UsdPhysics.MassAPI(ball).GetMassAttr().Get() == 0.25
        assert (
            http.post(
                "/api/v1/library/scene-drafts",
                json=value
                | {
                    "objects": [{"name": "Fluid", "shape": "fluid"}],
                },
            ).status_code
            == 422
        )


@contextmanager
def training_gateway(tmp_path):
    from tests.gateway.test_remote_training_nodes import remote_settings

    python = os.environ.get("LW_SCENE_DRAFT_MJLAB_PYTHON")
    if not python:
        with TestClient(create_app(remote_settings())) as client:
            yield client, False
        return

    import httpx

    from tests.gateway.test_mjlab_node_jobs import _start_server

    capabilities = tmp_path / "capabilities.json"
    capabilities.write_text(
        json.dumps(
            {
                "hardware": "scene-draft-cpu",
                "gpus": [],
                "cpuAvailable": True,
                "profiles": [],
            }
        )
    )
    settings = remote_settings(
        node_mjlab_trainer=(python,),
        node_capabilities=capabilities,
        node_interval=0.1,
        remote_requests_per_minute=5000,
    )
    app, server, thread = _start_server(settings)
    try:
        with httpx.Client(base_url=settings.node_gateway_url, trust_env=False, timeout=150) as client:
            yield client, True
    finally:
        if app.state.jobs.node.root.exists():
            (app.state.jobs.node.root / "stop").write_text("owned witness teardown")
        server.should_exit = True
        thread.join(30)
        assert not thread.is_alive()
        process = app.state.jobs.node.process
        if process is not None:
            process.wait(timeout=5)


def test_authenticated_actor_preflights_layer_b_before_training(tmp_path):
    import yaml

    from apps.node.mjlab_launcher import compiler_hash
    from packages.library.common import BUILTIN_ROOT
    from packages.training.inputs import pack_package
    from tests.fixtures.training_task import training_plan
    from tests.gateway.test_remote_training_nodes import (
        MJLAB_VERSIONS,
        OPERATOR,
        VIEWER,
        beat,
        register,
    )

    actor = OPERATOR | {"X-Actor": "agent:scene-trainer"}
    with training_gateway(tmp_path) as (http, real_node):
        value = draft()
        asset = http.get("/api/v1/library/assets/robot/microduck/1", headers=actor).json()
        value["assets"] = [
            {"name": "Microduck", "asset": "robot/microduck@1", "contentHash": asset["contentHash"]}
        ]
        value["bindings"] = [{"key": "/World/Lab/Microduck", "template": "robot/microduck@1"}]
        draft_url = "/api/v1/library/scene-drafts"
        assert http.post(draft_url, json=value, headers=VIEWER).status_code == 403
        staged = http.post(draft_url, json=value, headers=actor)
        assert staged.status_code == 201, staged.text
        result = http.post(f"{draft_url}/{staged.json()['id']}/publish", headers=actor)
        assert result.is_success, result.text
        scene = result.json()
        assert not [row for row in scene["validation"]["checks"] if row["status"] == "fail"], scene

        reference = BUILTIN_ROOT / "tasks/microduck-stand-up/1/package"
        task = yaml.safe_load((reference / "task.yaml").read_bytes())
        spec = yaml.safe_load((reference / "obs_spec.yaml").read_bytes())
        spec["schema"] = "lw.obs-spec/2"
        task.update(
            name="layer-b-probe",
            scene=scene["sceneVersion"],
            declarative={
                "baseBody": task["robot"] + "/trunk_base",
                "episodeSeconds": 3.0,
                "targetSites": ["/World/Lab/Sites/Goal"],
                "rewards": [],
                "layerB": {
                    "state": "lw.reward-state/1",
                    "rewards": [
                        {
                            "weight": 1.0,
                            "expression": {"op": "norm", "args": [{"state": "target_position_world"}]},
                        }
                    ],
                },
            },
        )
        task["evaluation"]["scene"] = scene["sceneVersion"]
        source = tmp_path / "task"
        source.mkdir()
        (source / "obs_spec.yaml").write_text(yaml.safe_dump(spec))
        layer = task["declarative"]["layerB"]
        valid = layer["rewards"][0]["expression"]
        layer["rewards"][0]["expression"] = {"op": "read_csv", "args": []}
        (source / "task.yaml").write_text(yaml.safe_dump(task))
        headers = actor | {"Content-Type": "application/x-tar"}
        rejected = http.post("/api/v1/tasks", headers=headers, content=pack_package(source))
        assert rejected.status_code == 422 and "layerB.rewards[0].expression" in rejected.text
        layer["rewards"][0] = {
            "weight": 0.0,
            "expression": {"op": "add", "args": [1, {"op": "div", "args": [0, 0]}]},
        }
        (source / "task.yaml").write_text(yaml.safe_dump(task))
        pushed = http.post("/api/v1/tasks", headers=headers, content=pack_package(source))
        assert pushed.status_code == 201, pushed.text

        def preflight(task_id, *, revision_of=None, environments=2):
            route = f"/api/v1/plans/{revision_of}/revisions" if revision_of else "/api/v1/plans"
            submitted = http.post(
                route,
                headers=actor,
                json=training_plan(
                    task_id,
                    algorithm={"library": "rsl_rl", "name": "PPO", "version": "5.0.1"},
                    hyperparameters={
                        "num_envs": environments,
                        "max_iterations": 4,
                        "num_steps_per_env": 4,
                        "num_learning_epochs": 1,
                        "num_mini_batches": 1,
                        **({"device": "cpu"} if real_node else {}),
                    },
                    model={"actor": [8], "critic": [8]},
                    evaluation={"scene": scene["sceneVersion"], "episodes": 30, "threshold": 1},
                ),
            )
            assert submitted.status_code == 201, submitted.text
            plan = submitted.json()
            start = {"planId": plan["id"], "seed": 42}
            assert http.post("/api/v1/jobs", headers=actor, json=start).status_code == 409
            request = http.post("/api/v1/jobs", headers=actor, json=start | {"kind": "plan_validation"})
            assert request.status_code == 202, request.text
            job = request.json()
            assert job["requestedBy"] == {"kind": "agent", "name": "scene-trainer"}
            return plan, start, job

        plan, start, job = preflight(pushed.json()["id"])
        decision_url = f"/api/v1/approvals/{plan['approvalId']}/decision"
        blocked = http.post(decision_url, headers=OPERATOR, json={"decision": "approve"})
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "plan_validation_required"
        assert http.get(f"/api/v1/plans/{plan['id']}", headers=actor).json()["status"] == "submitted"
        assert "contentHash" not in http.get(f"/api/v1/plans/{plan['id']}", headers=actor).json()["goalSuite"]
        if real_node:
            from tests.gateway.test_mjlab_node_jobs import _wait_for

            def terminal(job):
                url = f"/api/v1/jobs/{job['jobId']}"

                def read():
                    response = http.get(url, headers=actor)
                    assert response.is_success, response.text
                    return response.json()

                return _wait_for(
                    read,
                    lambda row: row["status"] in ("succeeded", "failed", "cancelled"),
                    timeout=135,
                )

            failed = terminal(job)
            assert failed["status"] == "failed"
            assert failed["reason"] == "layerB.rewards[0].expression.args[1]: div produced a non-finite value"
            assert http.post("/api/v1/jobs", headers=actor, json=start).status_code == 409
            assert http.post(decision_url, headers=OPERATOR, json={"decision": "approve"}).status_code == 409
            layer["rewards"][0] = {"weight": 1.0, "expression": valid}
            (source / "task.yaml").write_text(yaml.safe_dump(task))
            passed_task = http.post("/api/v1/tasks", headers=headers, content=pack_package(source))
            assert passed_task.status_code == 201, passed_task.text
            plan, start, job = preflight(passed_task.json()["id"])
            checked = terminal(job)
            assert checked["status"] == "succeeded" and checked["metrics"]["iteration"] == 3, checked
            assert checked["metrics"]["validation"]["numEnvs"] == 2
            assert checked["metrics"]["validation"].get("peakMemoryMb") is None
            assert len(checked["metrics"]["validation"]["samplesSeconds"]) == 2
            approval = http.post(
                f"/api/v1/approvals/{plan['approvalId']}/decision",
                headers=OPERATOR,
                json={"decision": "approve"},
            )
            assert approval.is_success, approval.text
            assert approval.json()["details"]["validationJobId"] == checked["jobId"]
            assert "contentHash" in approval.json()["details"]["goalSuite"]
            started = http.post("/api/v1/jobs", headers=actor, json=start)
            assert started.status_code == 202, started.text
            trained = terminal(started.json())
            assert trained["status"] == "succeeded" and trained["metrics"]["iteration"] == 4, trained
            assert {"policy.onnx", "checkpoint.safetensors"} <= {row["path"] for row in trained["artifacts"]}
            print(
                "CPU HTTP: path-aware fault, two-env preflight of 3 updates, approved training of 4 updates"
            )
            return

        node = register(http, "proof")
        runtime = {
            "gpus": [{"id": "GPU-proof", "name": "Contract fixture"}],
            "trainerVersions": MJLAB_VERSIONS,
            "compilerHash": compiler_hash(),
            "trainingEntries": {"mjlab": "mjlab:managed"},
        }
        (assignment,) = beat(http, node, **runtime).json()["assignments"]
        lease = node | {"X-Lease-Id": assignment["leaseId"]}
        inputs = http.get(f"/api/v1/jobs/{job['jobId']}/inputs", headers=lease).json()
        assert inputs["plan"]["hyperparameters"]["num_envs"] == 2
        assert inputs["job"]["sceneContentHash"] == scene["contentHash"]
        assert any(
            entry["kind"] == "asset" and entry["id"] == "robot/microduck@1" for entry in inputs["packages"]
        )
        import io
        import tarfile

        import numpy as np

        from packages.training.reward_state import compile_expression, evaluate

        task_input = next(entry for entry in inputs["packages"] if entry["kind"] == "task")
        downloaded = http.get(task_input["url"], headers=lease)
        assert downloaded.is_success, downloaded.text
        with tarfile.open(fileobj=io.BytesIO(downloaded.content)) as archive:
            leased_task = yaml.safe_load(archive.extractfile("task.yaml").read())
        expression = compile_expression(
            leased_task["declarative"]["layerB"]["rewards"][0]["expression"],
            path="layerB.rewards[0].expression",
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            with pytest.raises(ValueError, match="div produced a non-finite value") as fault:
                evaluate(expression, None, np.zeros(2, dtype=np.float32), arrays=np, reject_nonfinite=True)
        report = {
            "jobId": job["jobId"],
            "leaseId": assignment["leaseId"],
            "status": "failed",
            "reason": str(fault.value),
        }
        assert beat(http, node, reports=[report], **runtime).is_success
        failed = http.get(f"/api/v1/jobs/{job['jobId']}", headers=actor).json()
        assert failed["status"] == "failed" and failed["reason"] == report["reason"]
        assert http.post(decision_url, headers=OPERATOR, json={"decision": "approve"}).status_code == 409
        assert http.post("/api/v1/jobs", headers=actor, json=start).status_code == 409

        layer["rewards"][0] = {"weight": 1.0, "expression": valid}
        (source / "task.yaml").write_text(yaml.safe_dump(task))
        revised = http.post("/api/v1/tasks", headers=headers, content=pack_package(source))
        assert revised.status_code == 201, revised.text
        previous, _, first_preflight = preflight(revised.json()["id"], revision_of=plan["id"])

        def complete(job, environments, *, peak_memory=32.0):
            (lease_assignment,) = beat(http, node, **runtime).json()["assignments"]
            receipt = {
                "jobId": job["jobId"],
                "leaseId": lease_assignment["leaseId"],
                "status": "succeeded",
                "elapsedSeconds": 8.0,
                "iteration": 3,
                "validation": {
                    "numEnvs": environments,
                    "setupSeconds": 2.0,
                    "warmupSeconds": 1.0,
                    "samplesSeconds": [1.0, 1.0],
                    **({"peakMemoryMb": peak_memory} if peak_memory is not None else {}),
                },
            }
            response = beat(http, node, reports=[receipt], **runtime)
            return response

        first = complete(first_preflight, 2)
        assert first.is_success, first.text
        current, _, current_preflight = preflight(
            revised.json()["id"],
            revision_of=previous["id"],
            environments=4,
        )
        revision_decision = f"/api/v1/approvals/{current['approvalId']}/decision"
        denied = http.post(revision_decision, headers=OPERATOR, json={"decision": "approve"})
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "plan_validation_required"
        assert complete(current_preflight, 2).status_code == 422
        missing_peak = complete(current_preflight, 4, peak_memory=None)
        assert missing_peak.status_code == 422
        assert missing_peak.json()["detail"]["code"] == "invalid_validation"
        assert complete(current_preflight, 4, peak_memory=0.0).status_code == 422
        assert http.post(revision_decision, headers=OPERATOR, json={"decision": "approve"}).status_code == 409
        final_report = complete(current_preflight, 4)
        assert final_report.is_success, final_report.text
        measured = http.get(f"/api/v1/jobs/{current_preflight['jobId']}", headers=actor).json()
        assert measured["metrics"]["validation"]["peakMemoryMb"] == 32.0
        allowed = http.post(revision_decision, headers=OPERATOR, json={"decision": "approve"})
        assert allowed.is_success, allowed.text
        detail = allowed.json()["details"]
        assert detail["validationJobId"] == current_preflight["jobId"]
        assert detail["estimatedWallSeconds"] == 9
        assert detail["estimatedGpuHours"] == pytest.approx(9 / 3600)
        assert "contentHash" in detail["goalSuite"]

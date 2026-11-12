"""End-to-end public HTTP closed-loop control, Evaluation Suite, and multi-action Controller promotion
for a custom non-arm multi-joint robot on real NewtonSimulation(device="cpu")."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time

import httpx
import numpy as np
import pytest
import yaml
from onnx import TensorProto, helper, numpy_helper

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.suites import SuiteLibrary
from packages.library.transactions import durable_json
from packages.programs.embodied import EmbodiedPolicyProgram, EmbodiedPostureProgram
from packages.training.inputs import pack_package
from tests.fixtures.controller_promotion_evidence import controlled_qualification
from tests.fixtures.policy import artifact_archive, policy_task
from tests.fixtures.training_task import task_document, training_plan
from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, heartbeat

TILT_CART_TYPE = {
    "type": "tilt-cart/v1",
    "title": "Two-DoF balancing tilt-cart robot",
    "description": "Custom non-arm multi-joint balancing and planar cart robot.",
    "properties": {
        "phase": {"schema": {"enum": ["idle", "active", "fault"]}},
        "jointPositions": {"schema": {"type": "array", "items": {"type": "number"}}},
        "jointVelocities": {"schema": {"type": "array", "items": {"type": "number"}}},
        "basePosition": {"schema": {"type": "array", "items": {"type": "number"}}},
        "baseX": {"schema": {"type": "number"}, "unit": "m"},
        "baseY": {"schema": {"type": "number"}, "unit": "m"},
        "baseHeight": {"schema": {"type": "number"}, "unit": "m"},
        "tiltDeg": {"schema": {"type": "number"}, "unit": "deg"},
        "pitchDeg": {"schema": {"type": "number"}, "unit": "deg"},
        "planarSpeed": {"schema": {"type": "number"}, "unit": "m/s"},
        "angularVelocity": {"schema": {"type": "array", "items": {"type": "number"}}},
    },
    "actions": {
        "stabilize": {
            "description": "Reach and damp target mast tilt angle",
            "params": {},
            "required": [],
            "longRunning": True,
        },
        "drive": {
            "description": "Drive along bounded velocity and yaw-rate command",
            "params": {
                "vx": {"type": "number", "unit": "m/s", "minimum": -1.0, "maximum": 1.0},
                "wz": {"type": "number", "unit": "rad/s", "minimum": -2.0, "maximum": 2.0},
            },
            "required": ["vx", "wz"],
            "longRunning": True,
        },
        "stop": {
            "description": "Hold current joint targets",
            "params": {},
            "required": [],
        },
    },
}

ASSET_USDA = """#usda 1.0
(
    defaultPrim = "Asset"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"])
{
    def Xform "Carriage" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 2.0
        double3 xformOp:translate = (0, 0, 0.12)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.16
        }
    }
    def Xform "Mast" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.5
        double3 xformOp:translate = (0, 0, 0.32)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.1
        }
    }
    def PhysicsPrismaticJoint "slide" (prepend apiSchemas = ["PhysicsDriveAPI:linear"])
    {
        rel physics:body1 = </Asset/Carriage>
        uniform token physics:axis = "X"
        point3f physics:localPos0 = (0, 0, 0.12)
        point3f physics:localPos1 = (0, 0, 0)
        float physics:lowerLimit = -0.8
        float physics:upperLimit = 0.8
        float drive:linear:physics:stiffness = 120.0
        float drive:linear:physics:damping = 24.0
        float drive:linear:physics:maxForce = 200.0
    }
    def PhysicsRevoluteJoint "pitch" (prepend apiSchemas = ["PhysicsDriveAPI:angular"])
    {
        rel physics:body0 = </Asset/Carriage>
        rel physics:body1 = </Asset/Mast>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, 0, 0.10)
        point3f physics:localPos1 = (0, 0, -0.10)
        float physics:lowerLimit = -45.0
        float physics:upperLimit = 45.0
        float drive:angular:physics:stiffness = 1.5
        float drive:angular:physics:damping = 0.3
        float drive:angular:physics:maxForce = 50.0
    }
}
"""

SCENE_USDA = """#usda 1.0
(
    defaultPrim = "World"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "World"
{
    def PhysicsScene "PhysicsScene"
    {
        vector3f physics:gravityDirection = (0, 0, -1)
        float physics:gravityMagnitude = 9.81
    }
    def Xform "Lab"
    {
        def Cube "Floor" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 1.0
            double3 xformOp:translate = (0, 0, -0.05)
            float3 xformOp:scale = (4.0, 4.0, 0.1)
            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
        }
        def Xform "Bot" (
            prepend references = @library:assets/robot/tilt-cart/1/asset.usda@
        )
        {
        }
    }
}
"""


def _linear_onnx_bytes(weights: np.ndarray) -> bytes:
    in_dim, out_dim = weights.shape
    w = numpy_helper.from_array(weights.astype(np.float32), "weight")
    model = helper.make_model(
        helper.make_graph(
            [helper.make_node("MatMul", ["observations", "weight"], ["actions"])],
            "custom-embodied-feedback",
            [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, in_dim])],
            [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, out_dim])],
            [w],
        ),
        opset_imports=[helper.make_opsetid("", 18)],
    )
    model.ir_version = 10
    return model.SerializeToString()


def _asset_tar(stage_dir) -> bytes:
    stage_dir.mkdir(parents=True, exist_ok=True)
    raw = ASSET_USDA.encode("utf-8")
    (stage_dir / "asset.usda").write_bytes(raw)
    (stage_dir / "LICENSE.txt").write_text("MIT\n", encoding="utf-8")
    durable_json(
        stage_dir / "provenance.json",
        {
            "source": "custom-embodied-test-fixture",
            "files": {"asset.usda": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}},
        },
    )
    (stage_dir / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "robot/tilt-cart",
                "kind": "robot",
                "title": "Tilt Cart 2-DoF",
                "stage": "asset.usda",
                "physics": {"massKg": 2.5, "massSource": "measured", "collision": "authored"},
                "implements": [
                    {
                        "type": "tilt-cart/v1",
                        "programs": {"newton": EmbodiedPostureProgram.id},
                        "config": {
                            "jointKeys": ["slide", "pitch"],
                            "prismaticJointKeys": ["slide"],
                            "baseBody": "Mast",
                            "defaultPosition": [0.0, 0.0],
                            "actionDuration": 0.4,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return pack_package(stage_dir)


def _scene_tar(stage_dir) -> bytes:
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "scene.usda").write_text(SCENE_USDA, encoding="utf-8")
    (stage_dir / "scene.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "tilt-cart-arena",
                "title": "Tilt Cart Arena",
                "sources": ["newton"],
                "stage": "scene.usda",
                "simulation": {"solver": "mujoco", "physicsDt": 1.0 / 240.0, "warmupSteps": 5},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (stage_dir / "devices.yaml").write_text(
        yaml.safe_dump(
            {
                "scene": "tilt-cart-arena",
                "entities": [
                    {
                        "key": "/World/Lab/Bot",
                        "template": "robot/tilt-cart@1",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (stage_dir / "locations.yaml").write_text(yaml.safe_dump({"locations": {}}), encoding="utf-8")
    return pack_package(stage_dir)


async def _upload_policy(
    client: httpx.AsyncClient, stage_dir, *, task_doc: dict, spec_doc: dict, weights: np.ndarray
):
    task_resp = await client.post(
        "/api/v1/tasks",
        content=policy_task(stage_dir, spec=spec_doc, task=task_doc),
        headers={"Content-Type": "application/x-tar"},
    )
    assert task_resp.status_code == 201, task_resp.text
    task = task_resp.json()
    plan_body = training_plan(
        task["id"],
        budget={"gpuHours": 1, "episodes": 20},
        evaluation={"scene": task_doc["scene"], "episodes": 1, "threshold": 1},
    )
    plan_resp = await client.post("/api/v1/plans", json=plan_body)
    assert plan_resp.status_code == 201, plan_resp.text
    plan = plan_resp.json()
    dec = await client.post(f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"})
    assert dec.status_code == 200, dec.text
    hb = await client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task))
    assert hb.status_code == 200, hb.text
    job_resp = await client.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42})
    assert job_resp.status_code == 202, job_resp.text
    job = job_resp.json()
    up = await client.post(
        f"/api/v1/jobs/{job['jobId']}/artifacts",
        headers=NODE | {"X-Lease-Id": job["leaseId"]},
        content=artifact_archive(job, model=_linear_onnx_bytes(weights)),
    )
    assert up.status_code == 200, up.text
    report = {
        "jobId": job["jobId"],
        "leaseId": job["leaseId"],
        "status": "succeeded",
        "elapsedSeconds": 1.0,
    }
    fin = await client.post(
        HEARTBEAT,
        headers=NODE,
        json=heartbeat(plan, task, [report]),
    )
    assert fin.status_code == 200, fin.text
    pol_resp = await client.post(
        "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
    )
    assert pol_resp.status_code == 201, pol_resp.text
    return pol_resp.json(), plan


def _write_suite(user_lib, roots, *, name: str, scene, implements: str, goal: dict, params: dict):
    suite_dir = user_lib / "suites" / name / "1"
    (suite_dir / "private").mkdir(parents=True, exist_ok=True)
    metadata = {
        "id": "suite/" + name,
        "title": f"Custom embodied suite {name}",
        "layer": "embodied",
        "scene": scene.id,
        "sceneContentHash": scene.content_hash,
        "source": "newton",
        "episodes": [
            {"id": "train-1", "split": "train", "params": params},
            {"id": "test-1", "split": "test", "params": params},
        ],
    }
    task = {
        "name": name,
        "scene": scene.id,
        "robot": "/World/Lab/Bot",
        "implements": implements,
        "timeoutSeconds": 10.0,
        "success": goal,
        "milestones": [{"id": "goal", "when": goal}],
    }
    (suite_dir / "suite.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    (suite_dir / "task.yaml").write_text(yaml.safe_dump(task, sort_keys=False), encoding="utf-8")
    (suite_dir / "private/seeds.yaml").write_text(
        yaml.safe_dump({"train-1": 7, "test-1": 11}, sort_keys=False), encoding="utf-8"
    )
    suites = SuiteLibrary(roots)
    suites.refresh()
    return suites.resolve(metadata["id"] + "@1")


async def _evaluate(client: httpx.AsyncClient, suite_id: str, subject: str, *, plan=None):
    body = {"subject": subject, "suite": suite_id}
    if plan is not None:
        body.update(planId=plan["id"], planRevision=plan["revision"])
    sub = await client.post("/api/v1/evaluations", json=body)
    assert sub.status_code == 202, sub.text
    eval_id = sub.json()["evaluationId"]
    deadline = time.monotonic() + 60.0
    while True:
        read = await client.get(f"/api/v1/evaluations/{eval_id}")
        assert read.status_code == 200, read.text
        val = read.json()
        if val["status"] in ("completed", "failed", "cancelled"):
            assert val["status"] == "completed", val
            return val
        assert time.monotonic() < deadline, val
        await asyncio.sleep(0.05)


async def _promote(client: httpx.AsyncClient, *, policy_id: str, scene_id: str, request_id: str):
    resp = await client.post(
        "/api/v1/controller-promotions",
        json={"subject": policy_id, "scene": scene_id, "requestId": request_id},
    )
    assert resp.status_code == 202, resp.text
    promo = resp.json()
    route = f"/api/v1/controller-promotions/{promo['id']}"
    deadline = time.monotonic() + 60.0
    while True:
        progress = (await client.get(route)).json()
        if progress["status"] != "testing":
            assert progress["status"] == "awaiting_human", progress
            break
        assert time.monotonic() < deadline, progress
        await asyncio.sleep(0.05)
    review = (await client.get(route + "/review")).json()
    assert set(review["test"]) == {"reference", "candidate"}
    approved = await client.post(
        f"/api/v1/approvals/{review['approvalId']}/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    published = approved.json()
    assert published["status"] == "published", published
    return published


async def test_custom_non_arm_embodied_robot_closed_loop_evaluation_and_multi_action_promotion(
    tmp_path, monkeypatch
):
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    rec_root = tmp_path / "recordings"
    extra_types = tmp_path / "extra-types"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SPATIAL_RECORDINGS_ROOT", str(rec_root))

    # Isolated explicit SPATIAL_EXTRA_DEVICE_TYPES_DIR (never touches repo devices/types/ or LW_LIBRARY/types)
    extra_types.mkdir(parents=True, exist_ok=True)
    (extra_types / "tilt-cart.v1.yaml").write_text(
        yaml.safe_dump(TILT_CART_TYPE, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("SPATIAL_EXTRA_DEVICE_TYPES_DIR", str(extra_types))

    settings = Settings(state_root=state_root, recordings_root=rec_root, node_token=TOKEN)
    app = create_app(settings, controller_promotion_qualification=controlled_qualification)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned") as client:
            # 1. Public HTTP Asset & Scene import (with real StructuralValidator + Newton CPU physics checks)
            asset_imp = await client.post(
                "/api/v1/library/imports",
                content=_asset_tar(tmp_path / "stage-asset"),
                headers={"Content-Type": "application/x-tar"},
            )
            assert asset_imp.status_code in (200, 201), asset_imp.text
            assert asset_imp.json()["assetVersion"] == "robot/tilt-cart@1"
            assert asset_imp.json()["validation"]["physics"]["status"] == "completed"

            scene_imp = await client.post(
                "/api/v1/library/imports",
                content=_scene_tar(tmp_path / "stage-scene"),
                headers={"Content-Type": "application/x-tar"},
            )
            assert scene_imp.status_code in (200, 201), scene_imp.text
            assert scene_imp.json()["sceneVersion"] == "tilt-cart-arena@1"
            assert scene_imp.json()["validation"]["launchable"] is True
            assert scene_imp.json()["validation"]["physics"]["status"] == "completed"

            # Register Embodiment & Controller in isolated user library
            durable_json(
                user_lib / "embodiments/tilt-cart-sim.yaml",
                {
                    "id": "tilt-cart-sim",
                    "title": "Tilt Cart 2-DoF (Sim)",
                    "kind": "sim",
                    "asset": "robot/tilt-cart@1",
                    "controller": "tilt-cart-ctrl",
                    "bindings": [
                        {"scene": "tilt-cart-arena@1", "entity": "/World/Lab/Bot", "source": "newton"}
                    ],
                },
            )
            durable_json(
                user_lib / "controllers/tilt-cart-ctrl/controller.yaml",
                {
                    "id": "tilt-cart-ctrl",
                    "title": "Tilt Cart Controller",
                    "embodiment": "tilt-cart-sim",
                    "currentVersion": None,
                },
            )

            manager = app.state.manager
            scene_v1 = manager.library.resolve("tilt-cart-arena@1", launch=True, source="newton")
            joints = ["/World/Lab/Bot/slide", "/World/Lab/Bot/pitch"]
            mast = "/World/Lab/Bot/Mast"

            # 2. Action 1: stabilize (projected_gravity + body_angular_velocity + joint_position_offset)
            stabilize_spec = {
                "schema": "lw.obs-spec/1",
                "observations": [
                    {
                        "name": "gravity",
                        "source": "projected_gravity",
                        "key": mast,
                        "unit": "1",
                        "shape": [3],
                        "offset": [0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                    },
                    {
                        "name": "ang_vel",
                        "source": "body_angular_velocity",
                        "key": mast,
                        "frame": "body",
                        "unit": "rad/s",
                        "shape": [3],
                        "offset": [0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                    },
                    {
                        "name": "slide_pos",
                        "source": "joint_position",
                        "keys": [joints[0]],
                        "frame": "world",
                        "unit": "m",
                        "shape": [1],
                        "offset": [0.0],
                        "scale": [1.0],
                    },
                    {
                        "name": "pitch_pos",
                        "source": "joint_position",
                        "keys": [joints[1]],
                        "frame": "world",
                        "unit": "rad",
                        "shape": [1],
                        "offset": [0.0],
                        "scale": [1.0],
                    },
                ],
                "action": {
                    "space": "joint_position_offset",
                    "jointKeys": joints,
                    "units": ["m", "rad"],
                    "defaultPosition": [0.0, 0.25],
                    "scale": [0.1, 0.1],
                    "lower": [-0.8, -0.75],
                    "upper": [0.8, 0.75],
                },
            }
            stabilize_goal = {"entity": "/World/Lab/Bot", "property": "pitchDeg", "gte": 10.0}
            stabilize_task = task_document("tilt-stabilize") | {
                "title": "Tilt Stabilize",
                "scene": "tilt-cart-arena@1",
                "robot": "/World/Lab/Bot",
                "implements": "tilt-cart/v1.stabilize",
                "episode": {"params": {}},
                "success": stabilize_goal,
                "control": {"hz": 30.0, "action": "joint_position_offset"},
                "evaluation": {"scene": "tilt-cart-arena@1", "episodes": 1, "seeds": [7], "params": {}},
            }
            w_stab = np.zeros((8, 2), dtype=np.float32)
            w_stab[4, 1] = -0.05  # rate damping on body_angular_velocity[1] (wy)
            pol_stab, plan_stab = await _upload_policy(
                client,
                tmp_path / "task-stabilize",
                task_doc=stabilize_task,
                spec_doc=stabilize_spec,
                weights=w_stab,
            )

            suite_stab = _write_suite(
                user_lib,
                manager.library_assets.roots,
                name="tilt-stabilize-suite",
                scene=scene_v1,
                implements="tilt-cart/v1.stabilize",
                goal=stabilize_goal,
                params={},
            )
            manager.evaluations.suites.refresh()

            ref_stab = await _evaluate(client, suite_stab.id, EmbodiedPostureProgram.id, plan=plan_stab)
            cand_stab = await _evaluate(client, suite_stab.id, pol_stab["id"], plan=plan_stab)
            assert ref_stab["result"]["successes"] == 0
            assert cand_stab["result"]["successes"] == 1
            assert cand_stab["result"]["decision"] == "accepted"
            ep_stab = cand_stab["episodes"][0]
            assert ep_stab["policyExecution"]["providers"] == ["CPUExecutionProvider"]
            assert ep_stab["policyExecution"]["inferenceCalls"] >= 1

            promo_stab = await _promote(
                client,
                policy_id=pol_stab["id"],
                scene_id=scene_v1.id,
                request_id="promote-tilt-stabilize",
            )
            scene_v2_id = promo_stab["deployment"]["scene"]["id"]
            assert scene_v2_id == "tilt-cart-arena@2"
            scene_v2 = manager.library.resolve(scene_v2_id, launch=True, source="newton")

            # 3. Action 2 on promoted scene_v2: drive (command + projected_gravity + body_angular_velocity)
            # Evaluates & promotes ONLY 'drive' while preserving 'stabilize' in policies & ControllerVersion!
            drive_spec = {
                "schema": "lw.obs-spec/1",
                "observations": [
                    {
                        "name": "cmd",
                        "source": "command",
                        "frame": "body",
                        "unit": "m/s",
                        "shape": [2],
                        "offset": [0.0, 0.0],
                        "scale": [1.0, 1.0],
                        "lower": [-0.5, -1.0],
                        "upper": [0.5, 1.0],
                    },
                    {
                        "name": "gravity",
                        "source": "projected_gravity",
                        "key": mast,
                        "unit": "1",
                        "shape": [3],
                        "offset": [0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                    },
                    {
                        "name": "ang_vel",
                        "source": "body_angular_velocity",
                        "key": mast,
                        "frame": "body",
                        "unit": "rad/s",
                        "shape": [3],
                        "offset": [0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                    },
                ],
                "action": {
                    "space": "joint_position_offset",
                    "jointKeys": joints,
                    "units": ["m", "rad"],
                    "defaultPosition": [0.0, 0.0],
                    "scale": [1.0, 0.1],
                    "lower": [-0.8, -0.75],
                    "upper": [0.8, 0.75],
                },
            }
            drive_goal = {"entity": "/World/Lab/Bot", "property": "baseX", "gte": 0.25}
            drive_task = task_document("tilt-drive") | {
                "title": "Tilt Drive",
                "scene": scene_v2_id,
                "robot": "/World/Lab/Bot",
                "implements": "tilt-cart/v1.drive",
                "episode": {
                    "params": {},
                    "paramBounds": {"vx": [0.1, 0.45], "wz": [-0.5, 0.5]},
                    "commandEnvelope": [[-0.45, 0.45], [-0.5, 0.5]],
                },
                "success": drive_goal,
                "control": {"hz": 30.0, "action": "joint_position_offset"},
                "evaluation": {
                    "scene": scene_v2_id,
                    "episodes": 1,
                    "seeds": [7],
                    "params": {"vx": [0.35], "wz": [0.0]},
                },
            }
            w_drive = np.zeros((8, 2), dtype=np.float32)
            w_drive[0, 0] = 1.2   # vx command -> +X slide target
            w_drive[6, 1] = -0.05  # wy damping -> pitch target
            pol_drive, plan_drive = await _upload_policy(
                client,
                tmp_path / "task-drive",
                task_doc=drive_task,
                spec_doc=drive_spec,
                weights=w_drive,
            )

            suite_drive = _write_suite(
                user_lib,
                manager.library_assets.roots,
                name="tilt-drive-suite",
                scene=scene_v2,
                implements="tilt-cart/v1.drive",
                goal=drive_goal,
                params={"vx": 0.35, "wz": 0.0},
            )
            manager.evaluations.suites.refresh()

            ref_drive = await _evaluate(client, suite_drive.id, EmbodiedPolicyProgram.id, plan=plan_drive)
            cand_drive = await _evaluate(client, suite_drive.id, pol_drive["id"], plan=plan_drive)
            assert ref_drive["result"]["successes"] == 0
            assert cand_drive["result"]["successes"] == 1
            assert cand_drive["result"]["decision"] == "accepted"
            ep_drive = cand_drive["episodes"][0]
            assert ep_drive["policyExecution"]["policy"] == pol_drive["id"]
            assert ep_drive["policyExecution"]["inferenceCalls"] >= 1

            # Verify recording catalog preserved sibling policy 'stabilize' while replacing 'drive'
            rec_path = manager.recordings.resolve(ep_drive["recordingId"], "recording.json")
            rec_cat = json.loads((rec_path.parent / "catalog.json").read_bytes())
            assert rec_cat["entities"]["/World/Lab/Bot"]["config"]["policies"] == {
                "stabilize": pol_stab["id"],
                "drive": pol_drive["id"],
            }

            promo_drive = await _promote(
                client,
                policy_id=pol_drive["id"],
                scene_id=scene_v2_id,
                request_id="promote-tilt-drive",
            )
            scene_v3_id = promo_drive["deployment"]["scene"]["id"]
            assert scene_v3_id == "tilt-cart-arena@3"

            # 4. Public HTTP interactive Session & multi-action closed-loop execution on Controller v2
            emb = (await client.get("/api/v1/embodiments/tilt-cart-sim")).json()
            assert emb["currentController"]["version"] == 2
            assert emb["currentController"]["scene"] == scene_v3_id
            assert set(emb["currentController"]["policies"]) == {"stabilize", "drive"}
            assert emb["currentController"]["policies"]["stabilize"]["policy"] == pol_stab["id"]
            assert emb["currentController"]["policies"]["drive"]["policy"] == pol_drive["id"]

            sess_resp = await client.post(
                "/api/v1/sessions",
                json={
                    "sceneVersion": scene_v3_id,
                    "source": "newton",
                    "continueWithoutObservers": True,
                },
            )
            assert sess_resp.status_code == 201, sess_resp.text
            session_id = sess_resp.json()["sessionId"]
            try:
                emb_live = (await client.get("/api/v1/embodiments/tilt-cart-sim")).json()
                assert [s["sessionId"] for s in emb_live["activeSessions"]] == [session_id]
                assert emb_live["activeSessions"][0]["controller"]["version"] == 2

                # Out-of-envelope action parameters MUST be rejected over HTTP!
                oob = await client.post(
                    f"/api/v1/sessions/{session_id}/actions",
                    json={
                        "requestId": "act-oob",
                        "entityKey": "/World/Lab/Bot",
                        "action": "drive",
                        "params": {"vx": 0.9, "wz": 0.0},
                        "wait": True,
                    },
                )
                assert oob.status_code == 409, oob.text
                assert oob.json()["error"]["code"] == "precondition_failed"
                assert "training coverage" in oob.json()["error"]["message"]

                # Execute Policy 1 ('stabilize') in live session
                act_stab = await client.post(
                    f"/api/v1/sessions/{session_id}/actions",
                    json={
                        "requestId": "act-stabilize",
                        "entityKey": "/World/Lab/Bot",
                        "action": "stabilize",
                        "params": {},
                        "wait": True,
                        "timeoutSeconds": 10.0,
                    },
                )
                assert act_stab.status_code == 200, act_stab.text
                assert act_stab.json()["status"] == "succeeded", act_stab.text

                # Execute Policy 2 ('drive') in the SAME live session
                act_drive = await client.post(
                    f"/api/v1/sessions/{session_id}/actions",
                    json={
                        "requestId": "act-drive",
                        "entityKey": "/World/Lab/Bot",
                        "action": "drive",
                        "params": {"vx": 0.35, "wz": 0.0},
                        "wait": True,
                        "timeoutSeconds": 10.0,
                    },
                )
                assert act_drive.status_code == 200, act_drive.text
                assert act_drive.json()["status"] == "succeeded", act_drive.text

                state = (await client.get(f"/api/v1/sessions/{session_id}/state")).json()
                bot_vals = state["entities"]["/World/Lab/Bot"]["values"]
                assert bot_vals["baseX"] >= 0.25
            finally:
                closed = await client.delete(f"/api/v1/sessions/{session_id}")
                assert closed.status_code == 204, closed.text

            assert all(not s.holds_resources for s in manager.sessions.values())

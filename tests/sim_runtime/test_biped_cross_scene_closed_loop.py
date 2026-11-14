"""End-to-end CPU Newton verification of Waypoints, biped/v1, hierarchical velocity guidance,
and zero-shot cross-scene Policy bundle reuse across public HTTP API, lw CLI, and MCP."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np
import pytest
import uvicorn
import yaml
from lw.client import GatewayClient
from lw.mcp_server import create_server
from mcp import Client, Implementation
from onnx import TensorProto, helper, numpy_helper

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.suites import SuiteLibrary
from packages.library.transactions import durable_json
from packages.programs.biped import BipedPolicyProgram, BipedPostureProgram
from packages.training.inputs import pack_package
from tests.fixtures.controller_promotion_evidence import controlled_qualification
from tests.fixtures.policy import artifact_archive, policy_task
from tests.fixtures.training_task import task_document, training_plan
from tests.gateway.test_training_jobs import HEARTBEAT, NODE, TOKEN, heartbeat

ROOT = Path(__file__).resolve().parents[2]
LW = Path(sys.executable).parent / ("lw.exe" if os.name == "nt" else "lw")

BIPED_ASSET_USDA = """#usda 1.0
(
    defaultPrim = "Asset"
    metersPerUnit = 1
    upAxis = "Z"
)
def Xform "Asset" (prepend apiSchemas = ["PhysicsArticulationRootAPI"])
{
    def Xform "StageX" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.4
        double3 xformOp:translate = (0, 0, 0.08)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.05
        }
    }
    def Xform "StageY" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.4
        double3 xformOp:translate = (0, 0, 0.15)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.05
        }
    }
    def Xform "Pelvis" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 0.6
        double3 xformOp:translate = (0, 0, 0.23)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 0.07
        }
    }
    def Xform "Trunk" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"])
    {
        float physics:mass = 1.1
        double3 xformOp:translate = (0, 0, 0.39)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        def Cube "Geom" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 1.0
            float3 xformOp:scale = (0.08, 0.12, 0.16)
            uniform token[] xformOpOrder = ["xformOp:scale"]
        }
    }
    def PhysicsPrismaticJoint "stride_x" (prepend apiSchemas = ["PhysicsDriveAPI:linear"])
    {
        rel physics:body1 = </Asset/StageX>
        uniform token physics:axis = "X"
        point3f physics:localPos0 = (0, 0, 0.08)
        point3f physics:localPos1 = (0, 0, 0)
        float physics:lowerLimit = -1.5
        float physics:upperLimit = 1.5
        float drive:linear:physics:stiffness = 140.0
        float drive:linear:physics:damping = 28.0
        float drive:linear:physics:maxForce = 250.0
    }
    def PhysicsPrismaticJoint "stride_y" (prepend apiSchemas = ["PhysicsDriveAPI:linear"])
    {
        rel physics:body0 = </Asset/StageX>
        rel physics:body1 = </Asset/StageY>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, 0, 0.07)
        point3f physics:localPos1 = (0, 0, 0)
        float physics:lowerLimit = -1.5
        float physics:upperLimit = 1.5
        float drive:linear:physics:stiffness = 140.0
        float drive:linear:physics:damping = 28.0
        float drive:linear:physics:maxForce = 250.0
    }
    def PhysicsRevoluteJoint "turn_yaw" (prepend apiSchemas = ["PhysicsDriveAPI:angular"])
    {
        rel physics:body0 = </Asset/StageY>
        rel physics:body1 = </Asset/Pelvis>
        uniform token physics:axis = "Z"
        point3f physics:localPos0 = (0, 0, 0.08)
        point3f physics:localPos1 = (0, 0, 0)
        float physics:lowerLimit = -180.0
        float physics:upperLimit = 180.0
        float drive:angular:physics:stiffness = 24.0
        float drive:angular:physics:damping = 4.5
        float drive:angular:physics:maxForce = 80.0
    }
    def PhysicsRevoluteJoint "hip_pitch" (prepend apiSchemas = ["PhysicsDriveAPI:angular"])
    {
        rel physics:body0 = </Asset/Pelvis>
        rel physics:body1 = </Asset/Trunk>
        uniform token physics:axis = "Y"
        point3f physics:localPos0 = (0, 0, 0.06)
        point3f physics:localPos1 = (0, 0, -0.10)
        float physics:lowerLimit = -75.0
        float physics:upperLimit = 75.0
        float drive:angular:physics:stiffness = 0.08
        float drive:angular:physics:damping = 0.02
        float drive:angular:physics:maxForce = 12.0
    }
}
"""

ARENA_SCENE_USDA = """#usda 1.0
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
        def Xform "ArenaBiped" (
            prepend references = @library:assets/robot/test-biped/1/asset.usda@
        )
        {
        }
    }
}
"""

PATROL_SCENE_USDA = """#usda 1.0
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
            float3 xformOp:scale = (6.0, 6.0, 0.1)
            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
        }
        def Cube "CrateBench" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 1.0
            double3 xformOp:translate = (-0.8, -0.8, 0.20)
            float3 xformOp:scale = (0.4, 0.4, 0.4)
            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
        }
        def Cube "LowBeam" (prepend apiSchemas = ["PhysicsCollisionAPI"])
        {
            double size = 1.0
            double3 xformOp:translate = (0.35, -0.35, 0.44)
            float3 xformOp:scale = (0.14, 0.30, 0.08)
            uniform token[] xformOpOrder = ["xformOp:translate", "xformOp:scale"]
        }
        def Xform "PatrolBiped" (
            prepend references = @library:assets/robot/test-biped/1/asset.usda@
        )
        {
        }
    }
}
"""


def _walk_onnx() -> bytes:
    inits = [
        numpy_helper.from_array(np.array([0], dtype=np.int64), "i0"),
        numpy_helper.from_array(np.array([1], dtype=np.int64), "i1"),
        numpy_helper.from_array(np.array([2], dtype=np.int64), "i2"),
        numpy_helper.from_array(np.array([3], dtype=np.int64), "i3"),
        numpy_helper.from_array(np.array([4], dtype=np.int64), "i4"),
        numpy_helper.from_array(np.array([5], dtype=np.int64), "i5"),
        numpy_helper.from_array(np.array([6], dtype=np.int64), "i6"),
        numpy_helper.from_array(np.array([11], dtype=np.int64), "i11"),
        numpy_helper.from_array(np.array([12], dtype=np.int64), "i12"),
        numpy_helper.from_array(np.array([0.55], dtype=np.float32), "dt_lead"),
        numpy_helper.from_array(np.array([-0.04], dtype=np.float32), "kd_wy"),
    ]
    nodes = [
        helper.make_node("Slice", ["observations", "i0", "i1", "i1"], ["vx"]),
        helper.make_node("Slice", ["observations", "i1", "i2", "i1"], ["vy"]),
        helper.make_node("Slice", ["observations", "i2", "i3", "i1"], ["wz"]),
        helper.make_node("Slice", ["observations", "i3", "i4", "i1"], ["sx"]),
        helper.make_node("Slice", ["observations", "i4", "i5", "i1"], ["sy"]),
        helper.make_node("Slice", ["observations", "i5", "i6", "i1"], ["yaw"]),
        helper.make_node("Slice", ["observations", "i11", "i12", "i1"], ["wy"]),
        helper.make_node("Cos", ["yaw"], ["cy"]),
        helper.make_node("Sin", ["yaw"], ["sy_t"]),
        helper.make_node("Mul", ["vx", "cy"], ["vx_cy"]),
        helper.make_node("Mul", ["vy", "sy_t"], ["vy_sy"]),
        helper.make_node("Sub", ["vx_cy", "vy_sy"], ["vx_w"]),
        helper.make_node("Mul", ["vx", "sy_t"], ["vx_sy"]),
        helper.make_node("Mul", ["vy", "cy"], ["vy_cy"]),
        helper.make_node("Add", ["vx_sy", "vy_cy"], ["vy_w"]),
        helper.make_node("Mul", ["vx_w", "dt_lead"], ["dsx"]),
        helper.make_node("Mul", ["vy_w", "dt_lead"], ["dsy"]),
        helper.make_node("Mul", ["wz", "dt_lead"], ["dyaw"]),
        helper.make_node("Add", ["sx", "dsx"], ["out_sx"]),
        helper.make_node("Add", ["sy", "dsy"], ["out_sy"]),
        helper.make_node("Add", ["yaw", "dyaw"], ["out_yaw"]),
        helper.make_node("Mul", ["wy", "kd_wy"], ["out_pitch"]),
        helper.make_node("Concat", ["out_sx", "out_sy", "out_yaw", "out_pitch"], ["actions"], axis=1),
    ]
    model = helper.make_model(
        helper.make_graph(
            nodes,
            "biped-walk",
            [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, 13])],
            [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 4])],
            inits,
        ),
        opset_imports=[helper.make_opsetid("", 18)],
    )
    model.ir_version = 10
    return model.SerializeToString()


def _standup_onnx() -> bytes:
    inits = [
        numpy_helper.from_array(np.array([0], dtype=np.int64), "i0"),
        numpy_helper.from_array(np.array([1], dtype=np.int64), "i1"),
        numpy_helper.from_array(np.array([2], dtype=np.int64), "i2"),
        numpy_helper.from_array(np.array([3], dtype=np.int64), "i3"),
        numpy_helper.from_array(np.array([4], dtype=np.int64), "i4"),
        numpy_helper.from_array(np.array([5], dtype=np.int64), "i5"),
        numpy_helper.from_array(np.array([8], dtype=np.int64), "i8"),
        numpy_helper.from_array(np.array([9], dtype=np.int64), "i9"),
        numpy_helper.from_array(np.array([-0.18], dtype=np.float32), "k_step"),
        numpy_helper.from_array(np.array([-0.05], dtype=np.float32), "kd_wy"),
    ]
    nodes = [
        helper.make_node("Slice", ["observations", "i0", "i1", "i1"], ["sx"]),
        helper.make_node("Slice", ["observations", "i1", "i2", "i1"], ["sy"]),
        helper.make_node("Slice", ["observations", "i2", "i3", "i1"], ["yaw"]),
        helper.make_node("Slice", ["observations", "i4", "i5", "i1"], ["gx"]),
        helper.make_node("Slice", ["observations", "i8", "i9", "i1"], ["wy"]),
        helper.make_node("Cos", ["yaw"], ["cy"]),
        helper.make_node("Sin", ["yaw"], ["sy_t"]),
        helper.make_node("Mul", ["gx", "k_step"], ["step_b"]),
        helper.make_node("Mul", ["step_b", "cy"], ["dsx"]),
        helper.make_node("Mul", ["step_b", "sy_t"], ["dsy"]),
        helper.make_node("Add", ["sx", "dsx"], ["out_sx"]),
        helper.make_node("Add", ["sy", "dsy"], ["out_sy"]),
        helper.make_node("Mul", ["wy", "kd_wy"], ["out_pitch"]),
        helper.make_node("Concat", ["out_sx", "out_sy", "yaw", "out_pitch"], ["actions"], axis=1),
    ]
    model = helper.make_model(
        helper.make_graph(
            nodes,
            "biped-standup",
            [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, 10])],
            [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 4])],
            inits,
        ),
        opset_imports=[helper.make_opsetid("", 18)],
    )
    model.ir_version = 10
    return model.SerializeToString()


def _bow_onnx() -> bytes:
    w = np.zeros((11, 4), dtype=np.float32)
    w[1, 0] = 1.0
    w[2, 1] = 1.0
    w[3, 2] = 1.0
    w[0, 3] = 1.6
    w[9, 3] = -0.04
    init = numpy_helper.from_array(w, "w")
    model = helper.make_model(
        helper.make_graph(
            [helper.make_node("MatMul", ["observations", "w"], ["actions"])],
            "biped-bow",
            [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, 11])],
            [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, 4])],
            [init],
        ),
        opset_imports=[helper.make_opsetid("", 18)],
    )
    model.ir_version = 10
    return model.SerializeToString()


def _asset_tar(stage_dir: Path) -> bytes:
    stage_dir.mkdir(parents=True, exist_ok=True)
    raw = BIPED_ASSET_USDA.encode("utf-8")
    (stage_dir / "asset.usda").write_bytes(raw)
    (stage_dir / "LICENSE.txt").write_text("Apache-2.0\n", encoding="utf-8")
    durable_json(
        stage_dir / "provenance.json",
        {
            "source": "generic-biped-closed-loop-fixture",
            "files": {"asset.usda": {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}},
        },
    )
    (stage_dir / "asset.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "robot/test-biped",
                "kind": "robot",
                "title": "Generic Test Biped",
                "stage": "asset.usda",
                "physics": {"massKg": 2.5, "massSource": "measured", "collision": "authored"},
                "implements": [
                    {
                        "type": "biped/v1",
                        "programs": {"newton": BipedPostureProgram.id},
                        "config": {
                            "jointKeys": ["stride_x", "stride_y", "turn_yaw", "hip_pitch"],
                            "prismaticJointKeys": ["stride_x", "stride_y"],
                            "baseBody": "Trunk",
                            "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                            "fallPostures": {"front": [0.0, 0.0, 0.0, 0.90]},
                            "maxTiltDeg": 32.0,
                            "uprightTiltDeg": 10.0,
                            "bowMinPitchDeg": 10.0,
                            "xyTolerance": 0.06,
                            "yawTolerance": 0.15,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return pack_package(stage_dir)


def _scene_tar(
    stage_dir: Path, *, name: str, usda: str, entities: list[dict], waypoints: dict
) -> bytes:
    stage_dir.mkdir(parents=True, exist_ok=True)
    (stage_dir / "scene.usda").write_text(usda, encoding="utf-8")
    (stage_dir / "scene.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "title": f"Scene {name}",
                "sources": ["newton"],
                "stage": "scene.usda",
                "simulation": {"solver": "mujoco", "physicsDt": 1.0 / 240.0, "warmupSteps": 5},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (stage_dir / "devices.yaml").write_text(
        yaml.safe_dump({"scene": name, "entities": entities}, sort_keys=False),
        encoding="utf-8",
    )
    (stage_dir / "locations.yaml").write_text(
        yaml.safe_dump({"waypoints": waypoints}, sort_keys=False),
        encoding="utf-8",
    )
    return pack_package(stage_dir)


async def _upload_policy(
    client: httpx.AsyncClient, stage_dir: Path, *, task_doc: dict, spec_doc: dict, onnx_bytes: bytes
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
        content=artifact_archive(job, model=onnx_bytes),
    )
    assert up.status_code == 200, up.text
    report = {
        "jobId": job["jobId"],
        "leaseId": job["leaseId"],
        "status": "succeeded",
        "elapsedSeconds": 1.0,
    }
    fin = await client.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task, [report]))
    assert fin.status_code == 200, fin.text
    pol_resp = await client.post(
        "/api/v1/policies", json={"checkpoint": job["jobId"] + "/checkpoints/model_0.pt"}
    )
    assert pol_resp.status_code == 201, pol_resp.text
    return pol_resp.json(), plan


def _write_suite(
    user_lib: Path,
    roots: dict,
    *,
    name: str,
    scene,
    robot: str,
    implements: str,
    goal: dict,
    params: dict,
    extra_milestones: list[dict] | None = None,
):
    suite_dir = user_lib / "suites" / name / "1"
    (suite_dir / "private").mkdir(parents=True, exist_ok=True)
    metadata = {
        "id": "suite/" + name,
        "title": f"Biped suite {name}",
        "layer": "embodied",
        "scene": scene.id,
        "sceneContentHash": scene.content_hash,
        "source": "newton",
        "episodes": [
            {"id": "train-1", "split": "train", "params": params},
            {"id": "test-1", "split": "test", "params": params},
        ],
    }
    milestones = list(extra_milestones or []) + [{"id": "goal", "when": goal}]
    task = {
        "name": name,
        "scene": scene.id,
        "robot": robot,
        "implements": implements,
        "timeoutSeconds": 8.0,
        "success": goal,
        "milestones": milestones,
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
    deadline = time.monotonic() + 90.0
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
    deadline = time.monotonic() + 90.0
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


async def _mcp_call(mcp: Client, name: str, **arguments):
    response = await asyncio.wait_for(mcp.call_tool(name, arguments), 30.0)
    assert not response.is_error, response.content
    document = response.structured_content
    return document["result"] if set(document) == {"result"} else document


async def _lw_cli(*arguments: str, env: dict[str, str], code: int = 0):
    proc = await asyncio.to_thread(
        subprocess.run,
        [str(LW), *arguments, "--json"],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert proc.returncode == code, (arguments, proc.returncode, proc.stdout, proc.stderr)
    return json.loads(proc.stdout)


async def test_biped_waypoint_guidance_and_cross_scene_policy_reuse_across_http_cli_and_mcp(
    tmp_path, monkeypatch
):
    assert LW.is_file(), "Installed lw CLI entrypoint is required"
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    rec_root = tmp_path / "recordings"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))
    monkeypatch.setenv("SPATIAL_RECORDINGS_ROOT", str(rec_root))

    settings = Settings(state_root=state_root, recordings_root=rec_root, node_token=TOKEN)
    app = create_app(settings, controller_promotion_qualification=controlled_qualification)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(64)
    port = sock.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"

    async with app.router.lifespan_context(app):
        uv_cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
        server = uvicorn.Server(uv_cfg)
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=60.0) as client:
                for _ in range(100):
                    try:
                        if (await client.get("/api/v1/health")).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.02)
                else:
                    raise AssertionError("Loopback gateway server did not become ready")

                # 1. Import generic test-biped Asset and Scene 1 (biped-arena@1) via public HTTP
                asset_imp = await client.post(
                    "/api/v1/library/imports",
                    content=_asset_tar(tmp_path / "stage-asset"),
                    headers={"Content-Type": "application/x-tar"},
                )
                assert asset_imp.status_code in (200, 201), asset_imp.text
                assert asset_imp.json()["assetVersion"] == "robot/test-biped@1"
                assert asset_imp.json()["validation"]["physics"]["status"] == "completed"

                arena_bot = "/World/Lab/ArenaBiped"
                arena_imp = await client.post(
                    "/api/v1/library/imports",
                    content=_scene_tar(
                        tmp_path / "stage-arena",
                        name="biped-arena",
                        usda=ARENA_SCENE_USDA,
                        entities=[
                            {
                                "key": arena_bot,
                                "template": "robot/test-biped@1",
                                "config": {"locations": "locations.yaml"},
                            }
                        ],
                        waypoints={"arena_east": {"p": [0.28, 0.18, 0.0], "yaw": 0.50}},
                    ),
                    headers={"Content-Type": "application/x-tar"},
                )
                assert arena_imp.status_code in (200, 201), arena_imp.text
                assert arena_imp.json()["sceneVersion"] == "biped-arena@1"
                assert arena_imp.json()["validation"]["launchable"] is True
                assert arena_imp.json()["validation"]["physics"]["status"] == "completed"

                durable_json(
                    user_lib / "embodiments/test-biped-sim.yaml",
                    {
                        "id": "test-biped-sim",
                        "title": "Generic Test Biped (Sim)",
                        "kind": "sim",
                        "asset": "robot/test-biped@1",
                        "controller": "test-biped-ctrl",
                        "bindings": [
                            {"scene": "biped-arena@1", "entity": arena_bot, "source": "newton"}
                        ],
                    },
                )
                durable_json(
                    user_lib / "controllers/test-biped-ctrl/controller.yaml",
                    {
                        "id": "test-biped-ctrl",
                        "title": "Test Biped Controller",
                        "embodiment": "test-biped-sim",
                        "currentVersion": None,
                    },
                )

                manager = app.state.manager
                arena_v1 = manager.library.resolve("biped-arena@1", launch=True, source="newton")
                arena_joints = [
                    f"{arena_bot}/stride_x",
                    f"{arena_bot}/stride_y",
                    f"{arena_bot}/turn_yaw",
                    f"{arena_bot}/hip_pitch",
                ]
                arena_trunk = f"{arena_bot}/Trunk"
                action_spec = {
                    "space": "joint_position_offset",
                    "jointKeys": arena_joints,
                    "units": ["m", "m", "rad", "rad"],
                    "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                    "scale": [1.0, 1.0, 1.0, 1.0],
                    "lower": [-1.5, -1.5, -3.14, -1.3],
                    "upper": [1.5, 1.5, 3.14, 1.3],
                }
                common_obs = [
                    {
                        "name": "p_joints",
                        "source": "joint_position",
                        "keys": arena_joints[:2],
                        "frame": "world",
                        "unit": "m",
                        "shape": [2],
                        "offset": [0.0, 0.0],
                        "scale": [1.0, 1.0],
                    },
                    {
                        "name": "r_joints",
                        "source": "joint_position",
                        "keys": arena_joints[2:],
                        "frame": "world",
                        "unit": "rad",
                        "shape": [2],
                        "offset": [0.0, 0.0],
                        "scale": [1.0, 1.0],
                    },
                    {
                        "name": "grav",
                        "source": "projected_gravity",
                        "key": arena_trunk,
                        "unit": "1",
                        "shape": [3],
                        "offset": [0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                    },
                    {
                        "name": "ang",
                        "source": "body_angular_velocity",
                        "key": arena_trunk,
                        "frame": "body",
                        "unit": "rad/s",
                        "shape": [3],
                        "offset": [0.0, 0.0, 0.0],
                        "scale": [1.0, 1.0, 1.0],
                    },
                ]

                # 2a. Promote walkTo on Scene 1 (biped-arena@1 -> biped-arena@2)
                walk_spec = {
                    "schema": "lw.obs-spec/1",
                    "observations": [
                        {
                            "name": "cmd",
                            "source": "command",
                            "frame": "body",
                            "unit": "m/s",
                            "shape": [3],
                            "offset": [0.0, 0.0, 0.0],
                            "scale": [1.0, 1.0, 1.0],
                            "lower": [-0.4, -0.3, -1.2],
                            "upper": [0.4, 0.3, 1.2],
                        },
                        *common_obs,
                    ],
                    "action": action_spec,
                }
                walk_goal = {"entity": arena_bot, "property": "currentWaypoint", "equals": "arena_east"}
                walk_task = task_document("biped-walk") | {
                    "title": "Biped Walk",
                    "scene": arena_v1.id,
                    "robot": arena_bot,
                    "implements": "biped/v1.walkTo",
                    "episode": {
                        "params": {"waypoint": "arena_east"},
                        "paramBounds": {"speed": [0.05, 0.40]},
                        "commandEnvelope": [[-0.4, 0.4], [-0.3, 0.3], [-1.2, 1.2]],
                    },
                    "success": walk_goal,
                    "control": {"hz": 30.0, "action": "joint_position_offset"},
                    "evaluation": {
                        "scene": arena_v1.id,
                        "episodes": 1,
                        "seeds": [7],
                        "params": {"waypoint": ["arena_east"]},
                    },
                }
                pol_walk, plan_walk = await _upload_policy(
                    client,
                    tmp_path / "task-walk",
                    task_doc=walk_task,
                    spec_doc=walk_spec,
                    onnx_bytes=_walk_onnx(),
                )
                suite_walk = _write_suite(
                    user_lib,
                    manager.library_assets.roots,
                    name="biped-walk-suite",
                    scene=arena_v1,
                    robot=arena_bot,
                    implements="biped/v1.walkTo",
                    goal=walk_goal,
                    params={"waypoint": "arena_east"},
                )
                manager.evaluations.suites.refresh()
                ref_walk = await _evaluate(client, suite_walk.id, BipedPostureProgram.id, plan=plan_walk)
                cand_walk = await _evaluate(client, suite_walk.id, pol_walk["id"], plan=plan_walk)
                assert ref_walk["result"]["successes"] == 0
                assert cand_walk["result"]["successes"] == 1
                assert cand_walk["result"]["decision"] == "accepted"
                promo_walk = await _promote(
                    client, policy_id=pol_walk["id"], scene_id=arena_v1.id, request_id="promote-biped-walk"
                )
                arena_v2 = manager.library.resolve(
                    promo_walk["deployment"]["scene"]["id"], launch=True, source="newton"
                )
                assert arena_v2.id == "biped-arena@2"

                # 2b. Promote standUp on Scene 1 (biped-arena@2 -> biped-arena@3)
                stand_spec = {"schema": "lw.obs-spec/1", "observations": common_obs, "action": action_spec}
                stand_goal = {
                    "all": [
                        {"entity": arena_bot, "property": "fallen", "equals": False},
                        {"entity": arena_bot, "property": "lastCompletedAction", "equals": "standUp"},
                    ]
                }
                stand_fall_milestone = {
                    "id": "fell",
                    "when": {"entity": arena_bot, "property": "fallen", "equals": True},
                }
                stand_task = task_document("biped-stand") | {
                    "title": "Biped StandUp",
                    "scene": arena_v2.id,
                    "robot": arena_bot,
                    "implements": "biped/v1.standUp",
                    "episode": {"params": {"fromFall": "front"}},
                    "success": stand_goal,
                    "control": {"hz": 30.0, "action": "joint_position_offset"},
                    "evaluation": {
                        "scene": arena_v2.id,
                        "episodes": 1,
                        "seeds": [7],
                        "params": {"fromFall": ["front"]},
                    },
                }
                pol_stand, plan_stand = await _upload_policy(
                    client,
                    tmp_path / "task-stand",
                    task_doc=stand_task,
                    spec_doc=stand_spec,
                    onnx_bytes=_standup_onnx(),
                )
                suite_stand = _write_suite(
                    user_lib,
                    manager.library_assets.roots,
                    name="biped-stand-suite",
                    scene=arena_v2,
                    robot=arena_bot,
                    implements="biped/v1.standUp",
                    goal=stand_goal,
                    params={"fromFall": "front"},
                    extra_milestones=[stand_fall_milestone],
                )
                manager.evaluations.suites.refresh()
                ref_stand = await _evaluate(client, suite_stand.id, BipedPolicyProgram.id, plan=plan_stand)
                cand_stand = await _evaluate(client, suite_stand.id, pol_stand["id"], plan=plan_stand)
                assert ref_stand["result"]["successes"] == 0
                assert cand_stand["result"]["successes"] == 1
                assert cand_stand["result"]["decision"] == "accepted"
                promo_stand = await _promote(
                    client,
                    policy_id=pol_stand["id"],
                    scene_id=arena_v2.id,
                    request_id="promote-biped-stand",
                )
                arena_v3 = manager.library.resolve(
                    promo_stand["deployment"]["scene"]["id"], launch=True, source="newton"
                )
                assert arena_v3.id == "biped-arena@3"

                # 2c. Promote bow on Scene 1 (biped-arena@3 -> biped-arena@4)
                bow_spec = {
                    "schema": "lw.obs-spec/1",
                    "observations": [
                        {
                            "name": "cmd",
                            "source": "command",
                            "frame": "body",
                            "unit": "rad",
                            "shape": [1],
                            "offset": [0.0],
                            "scale": [1.0],
                            "lower": [0.0],
                            "upper": [0.6],
                        },
                        *common_obs,
                    ],
                    "action": action_spec,
                }
                bow_goal = {
                    "all": [
                        {"entity": arena_bot, "property": "fallen", "equals": False},
                        {"entity": arena_bot, "property": "lastCompletedAction", "equals": "bow"},
                    ]
                }
                bow_pitch_milestone = {
                    "id": "bowed",
                    "when": {"entity": arena_bot, "property": "pitchDeg", "gte": 10.0},
                }
                bow_task = task_document("biped-bow") | {
                    "title": "Biped Bow",
                    "scene": arena_v3.id,
                    "robot": arena_bot,
                    "implements": "biped/v1.bow",
                    "episode": {"params": {}, "commandEnvelope": [[0.0, 0.6]]},
                    "success": bow_goal,
                    "control": {"hz": 30.0, "action": "joint_position_offset"},
                    "evaluation": {"scene": arena_v3.id, "episodes": 1, "seeds": [7], "params": {}},
                }
                pol_bow, plan_bow = await _upload_policy(
                    client,
                    tmp_path / "task-bow",
                    task_doc=bow_task,
                    spec_doc=bow_spec,
                    onnx_bytes=_bow_onnx(),
                )
                suite_bow = _write_suite(
                    user_lib,
                    manager.library_assets.roots,
                    name="biped-bow-suite",
                    scene=arena_v3,
                    robot=arena_bot,
                    implements="biped/v1.bow",
                    goal=bow_goal,
                    params={},
                    extra_milestones=[bow_pitch_milestone],
                )
                manager.evaluations.suites.refresh()
                ref_bow = await _evaluate(client, suite_bow.id, BipedPolicyProgram.id, plan=plan_bow)
                cand_bow = await _evaluate(client, suite_bow.id, pol_bow["id"], plan=plan_bow)
                assert ref_bow["result"]["successes"] == 0
                assert cand_bow["result"]["successes"] == 1
                assert cand_bow["result"]["decision"] == "accepted"
                promo_bow = await _promote(
                    client, policy_id=pol_bow["id"], scene_id=arena_v3.id, request_id="promote-biped-bow"
                )
                arena_v4_id = promo_bow["deployment"]["scene"]["id"]
                assert arena_v4_id == "biped-arena@4"

                # 3. Verify Scene 1 (biped-arena@4) over public HTTP
                s1 = await client.post(
                    "/api/v1/sessions",
                    json={
                        "sceneVersion": arena_v4_id,
                        "source": "newton",
                        "realtimeFactor": 8.0,
                        "continueWithoutObservers": True,
                    },
                )
                assert s1.status_code == 201, s1.text
                s1_id = s1.json()["sessionId"]
                try:
                    w1 = await client.post(
                        f"/api/v1/sessions/{s1_id}/actions",
                        json={
                            "requestId": "arena-walk-1",
                            "entityKey": arena_bot,
                            "action": "walkTo",
                            "params": {"waypoint": "arena_east"},
                            "wait": True,
                            "timeoutSeconds": 15.0,
                        },
                    )
                    assert w1.status_code == 200, w1.text
                    assert w1.json()["status"] == "succeeded", w1.text
                    st1 = (await client.get(f"/api/v1/sessions/{s1_id}/state")).json()
                    vals1 = st1["entities"][arena_bot]["values"]
                    assert vals1["currentWaypoint"] == "arena_east"
                    assert vals1["phase"] == "idle"
                    assert vals1["fallen"] is False
                finally:
                    assert (await client.delete(f"/api/v1/sessions/{s1_id}")).status_code == 204

                # 4. Import Scene 2 (biped-patrol@1) with DIFFERENT layout, DIFFERENT entity key,
                # and DIFFERENT Waypoints reusing the exact promoted Policy bundle (zero retraining!)
                patrol_bot = "/World/Lab/PatrolBiped"
                promoted_bundle = {
                    "walkTo": pol_walk["id"],
                    "standUp": pol_stand["id"],
                    "bow": pol_bow["id"],
                }
                patrol_waypoints = {
                    "patrol_north": {"p": [0.28, 0.22, 0.0], "yaw": 0.75},
                    "patrol_west": {"p": [-0.28, 0.22, 0.0], "yaw": 2.20},
                    "pre_beam": {"p": [0.10, -0.35, 0.0], "yaw": 0.0},
                    "hazard_beam": {"p": [0.55, -0.35, 0.0], "yaw": 0.0},
                }
                patrol_imp = await client.post(
                    "/api/v1/library/imports",
                    content=_scene_tar(
                        tmp_path / "stage-patrol",
                        name="biped-patrol",
                        usda=PATROL_SCENE_USDA,
                        entities=[
                            {
                                "key": patrol_bot,
                                "type": "biped/v1",
                                "program": BipedPolicyProgram.id,
                                "config": {
                                    "jointKeys": [
                                        f"{patrol_bot}/stride_x",
                                        f"{patrol_bot}/stride_y",
                                        f"{patrol_bot}/turn_yaw",
                                        f"{patrol_bot}/hip_pitch",
                                    ],
                                    "prismaticJointKeys": [
                                        f"{patrol_bot}/stride_x",
                                        f"{patrol_bot}/stride_y",
                                    ],
                                    "baseBody": f"{patrol_bot}/Trunk",
                                    "defaultPosition": [0.0, 0.0, 0.0, 0.0],
                                    "fallPostures": {"front": [0.0, 0.0, 0.0, 0.90]},
                                    "maxTiltDeg": 32.0,
                                    "uprightTiltDeg": 10.0,
                                    "bowMinPitchDeg": 10.0,
                                    "xyTolerance": 0.06,
                                    "yawTolerance": 0.15,
                                    "locations": "locations.yaml",
                                    "policies": promoted_bundle,
                                },
                            }
                        ],
                        waypoints=patrol_waypoints,
                    ),
                    headers={"Content-Type": "application/x-tar"},
                )
                assert patrol_imp.status_code in (200, 201), patrol_imp.text
                assert patrol_imp.json()["sceneVersion"] == "biped-patrol@1"
                assert patrol_imp.json()["validation"]["launchable"] is True
                assert patrol_imp.json()["validation"]["physics"]["status"] == "completed"

                # 5. Exercise Scene 2 across public HTTP, installed lw CLI, and official MCP client!
                cli_env = os.environ | {"LW_URL": base_url, "PYTHONIOENCODING": "utf-8"}
                cli_desc = await _lw_cli("scene", "show", "biped-patrol@1", env=cli_env)
                assert set(cli_desc["waypoints"]) == {
                    "patrol_north",
                    "patrol_west",
                    "pre_beam",
                    "hazard_beam",
                }

                s2 = await client.post(
                    "/api/v1/sessions",
                    json={
                        "sceneVersion": "biped-patrol@1",
                        "source": "newton",
                        "realtimeFactor": 6.0,
                        "continueWithoutObservers": True,
                    },
                )
                assert s2.status_code == 201, s2.text
                s2_id = s2.json()["sessionId"]
                cli_env = cli_env | {"LW_SESSION": s2_id}

                try:
                    # Active session shows committed Controller v3 on cross-scene biped-patrol@1
                    emb_live = (await client.get("/api/v1/embodiments/test-biped-sim")).json()
                    assert [item["sessionId"] for item in emb_live["activeSessions"]] == [s2_id]
                    assert emb_live["activeSessions"][0]["scene"] == "biped-patrol@1"
                    assert emb_live["activeSessions"][0]["controller"]["version"] == 3

                    # Inspect via lw CLI
                    listed = await _lw_cli("ls", "--type", "biped/v1", env=cli_env)
                    assert [item["key"] for item in listed] == [patrol_bot]
                    shown = await _lw_cli("show", patrol_bot, env=cli_env)
                    assert set(shown["definition"]["actions"]) == {"walkTo", "standUp", "bow", "stop"}

                    async with GatewayClient(http=client, session=s2_id) as gw:
                        mcp_srv = await create_server(gw)
                        async with Client(
                            mcp_srv, client_info=Implementation(name="patrol-agent", version="1")
                        ) as mcp:
                            assert "Waypoints:" in mcp.session.instructions
                            assert "patrol_north" in mcp.session.instructions

                            mcp_desc = await _mcp_call(
                                mcp, "library_describe", id="biped-patrol@1", view="full"
                            )
                            assert set(mcp_desc["waypoints"]) == set(patrol_waypoints)

                            # Unknown waypoint rejected over MCP
                            bad_wp = await _mcp_call(
                                mcp,
                                "invoke_action",
                                key=patrol_bot,
                                action="walkTo",
                                params={"waypoint": "arena_east"},
                            )
                            assert bad_wp["status"] == "rejected"
                            assert bad_wp["error"]["code"] == "unknown_waypoint"

                            # Walk to patrol_north via lw CLI -> arrives & updates currentWaypoint
                            walk_north = await _lw_cli(
                                "do",
                                patrol_bot,
                                "walkTo",
                                "waypoint=patrol_north",
                                "--wait",
                                env=cli_env,
                            )
                            assert walk_north["status"] == "succeeded"
                            vals_north = await _lw_cli("get", patrol_bot, env=cli_env)
                            assert vals_north["currentWaypoint"] == "patrol_north"
                            assert vals_north["phase"] == "idle"
                            assert math.hypot(
                                vals_north["basePosition"][0] - 0.28,
                                vals_north["basePosition"][1] - 0.22,
                            ) <= 0.06

                            # Start walkTo(patrol_west) via lw CLI (--no-wait), cancel via concurrent MCP stop
                            pending_walk = await _lw_cli(
                                "do",
                                patrol_bot,
                                "walkTo",
                                "waypoint=patrol_west",
                                "--no-wait",
                                env=cli_env,
                            )
                            assert pending_walk["status"] in ("accepted", "running")
                            stopped = await _mcp_call(
                                mcp, "invoke_action", key=patrol_bot, action="stop"
                            )
                            assert stopped["status"] == "succeeded"
                            cancelled_walk = await _mcp_call(
                                mcp,
                                "get_invocation",
                                invocationId=pending_walk["invocationId"],
                                wait=True,
                            )
                            assert cancelled_walk["status"] == "cancelled"

                            # Bow via MCP -> pitches forward past bowMinPitchDeg and settles upright
                            bowed = await _mcp_call(
                                mcp, "invoke_action", key=patrol_bot, action="bow"
                            )
                            assert bowed["status"] == "succeeded"
                            assert (await _lw_cli("get", patrol_bot, "phase", env=cli_env)) == "idle"
                            assert (
                                await _lw_cli("get", patrol_bot, "lastCompletedAction", env=cli_env)
                            ) == "bow"

                            # Controlled fall + recovery via HTTP standUp(fromFall="front")
                            stand_front = await client.post(
                                f"/api/v1/sessions/{s2_id}/actions",
                                json={
                                    "requestId": "patrol-stand-front",
                                    "entityKey": patrol_bot,
                                    "action": "standUp",
                                    "params": {"fromFall": "front"},
                                    "wait": True,
                                    "timeoutSeconds": 15.0,
                                },
                            )
                            assert stand_front.status_code == 200, stand_front.text
                            assert stand_front.json()["status"] == "succeeded", stand_front.text
                            assert (await _lw_cli("get", patrol_bot, "fallen", env=cli_env)) is False
                            assert (
                                await _lw_cli("get", patrol_bot, "lastCompletedAction", env=cli_env)
                            ) == "standUp"

                            # Real physical hazard collision -> robot_fallen -> precondition guard -> standUp!
                            pre = await _lw_cli(
                                "do", patrol_bot, "walkTo", "waypoint=pre_beam", "--wait", env=cli_env
                            )
                            assert pre["status"] == "succeeded"

                            crashed = await _lw_cli(
                                "do",
                                patrol_bot,
                                "walkTo",
                                "waypoint=hazard_beam",
                                "--wait",
                                env=cli_env,
                                code=3,
                            )
                            assert crashed["status"] == "failed"
                            assert crashed["error"]["code"] == "robot_fallen"

                            fallen_vals = await _lw_cli("get", patrol_bot, env=cli_env)
                            assert fallen_vals["fallen"] is True
                            assert fallen_vals["phase"] == "fallen"

                            # While fallen==True, walkTo and bow are rejected by precondition!
                            blocked_walk = await _lw_cli(
                                "do",
                                patrol_bot,
                                "walkTo",
                                "waypoint=patrol_north",
                                env=cli_env,
                                code=2,
                            )
                            assert blocked_walk["error"]["code"] == "precondition_failed"

                            blocked_bow = await _mcp_call(
                                mcp, "invoke_action", key=patrol_bot, action="bow"
                            )
                            assert blocked_bow["status"] == "rejected"
                            assert blocked_bow["error"]["code"] == "precondition_failed"

                            # Recover via parameterless standUp on lw CLI, then walkTo patrol_north succeeds!
                            recovered = await _lw_cli(
                                "do", patrol_bot, "standUp", "--wait", env=cli_env
                            )
                            assert recovered["status"] == "succeeded"
                            after_rec = await _lw_cli("get", patrol_bot, env=cli_env)
                            assert after_rec["fallen"] is False
                            assert after_rec["phase"] == "idle"

                            walk_after = await _lw_cli(
                                "do",
                                patrol_bot,
                                "walkTo",
                                "waypoint=patrol_north",
                                "--wait",
                                env=cli_env,
                            )
                            assert walk_after["status"] == "succeeded"
                            assert (
                                await _lw_cli("get", patrol_bot, "currentWaypoint", env=cli_env)
                            ) == "patrol_north"
                finally:
                    assert (await client.delete(f"/api/v1/sessions/{s2_id}")).status_code == 204
        finally:
            server.should_exit = True
            await server_task
            sock.close()

        assert all(not s.holds_resources for s in app.state.manager.sessions.values())

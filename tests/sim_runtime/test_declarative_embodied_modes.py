"""Run all four declared completion modes on actual Newton state and CPU ONNX."""

from copy import deepcopy

import numpy as np
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from packages.info.program import HostCore
from packages.programs.embodied import EmbodiedPolicyProgram
from packages.protocol.schema import Actor, Catalog, InvokeRequest
from packages.sim_runtime.info_catalog import REGISTRY, load_info_catalog
from packages.sim_runtime.library_scene import prepare_scene_config
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.simulation import NewtonSimulation
from tests.sim_runtime.test_custom_embodied_closed_loop import TILT_CART_TYPE, _asset_tar, _scene_tar
from tests.unit.test_multi_policy_program import linear_onnx_bytes, write_policy_package


def test_custom_cart_four_modes_use_actual_newton_feedback(tmp_path):
    key = "/World/Lab/Bot"
    joints = [key + "/slide", key + "/pitch"]
    declaration = deepcopy(TILT_CART_TYPE)
    declaration["actions"].update(
        recovery={"longRunning": True},
        clip={"longRunning": True},
        navigate={"longRunning": True, "params": {"waypoint": {"type": "string"}}, "required": ["waypoint"]},
    )
    motion = {
        "loop": False,
        "jointKeys": ["/Asset/pitch"],
        "frames": [
            {"time": 0, "position": [0, 0, 0], "orientation": [0, 0, 0, 1], "joints": [0]},
            {"time": 1, "position": [0, 0, 0], "orientation": [0, 0, 0, 1], "joints": [0.25]},
        ],
    }
    app = create_app()
    with TestClient(app) as client:
        response = client.post("/api/v1/library/entity-types", json=declaration)
        assert response.status_code == 201, response.text
        for data in (_asset_tar(tmp_path / "asset"), _scene_tar(tmp_path / "scene")):
            response = client.post(
                "/api/v1/library/imports", content=data, headers={"Content-Type": "application/x-tar"}
            )
            assert response.status_code == 200, response.text
        scene = app.state.manager.library.resolve(response.json()["sceneVersion"], launch=True)
        user_library = app.state.manager.library_assets.roots["user"]
    action_base = {
        "space": "joint_position_offset",
        "jointKeys": joints,
        "units": ["m", "rad"],
        "scale": [1.0, 1.0],
        "lower": [-0.8, -0.76],
        "upper": [0.8, 0.76],
        "defaultPosition": [0.0, 0.0],
    }
    phase = {
        "name": "frame",
        "source": "reference_motion_frame",
        "frame": "world",
        "unit": "1",
        "shape": [8],
        "offset": [0.0] * 8,
        "scale": [1.0] * 8,
    }
    command = {
        "name": "command",
        "source": "command",
        "frame": "body",
        "unit": "m/s",
        "shape": [2],
        "offset": [0.0, 0.0],
        "scale": [1.0, 1.0],
        "lower": [-0.4, -1.0],
        "upper": [0.4, 1.0],
    }
    previous = {
        "name": "previous",
        "source": "previous_action",
        "frame": "world",
        "unit": "1",
        "shape": [2],
        "offset": [0.0, 0.0],
        "scale": [1.0, 1.0],
    }
    policies = {}
    for action, mode, term in (
        ("clip", "phase_clip", phase),
        ("recovery", "posture_recovery", previous),
        ("drive", "command_track", command),
        ("navigate", "waypoint_nav", command),
    ):
        action_spec = action_base.copy()
        weights = np.zeros((term["shape"][0], 2))
        episode = {"params": {}}
        if mode == "phase_clip":
            weights[-1, 1] = 1.0
        if mode in ("command_track", "waypoint_nav"):
            action_spec = {key: value for key, value in action_spec.items() if key != "defaultPosition"}
            action_spec |= {"space": "joint_position_delta", "maxDelta": [0.05, 0.05]}
            weights[0, 0] = 0.06
            episode = {
                "params": ({"vx": 0.15, "wz": 0.0} if action == "drive" else {"waypoint": "A"}),
                "paramBounds": {"vx": [-0.4, 0.4], "wz": [-1.0, 1.0]} if action == "drive" else {},
                "commandEnvelope": [[-0.4, 0.4], [-1.0, 1.0]],
            }
        task = {
            "title": action,
            "scene": scene.id,
            "robot": key,
            "implements": f"{declaration['type']}.{action}",
            "episode": episode,
            "success": {"property": "fallen", "equals": False},
            "control": {"hz": 60.0, "action": action_spec["space"]},
            "completion": {
                "mode": mode,
                "uprightTiltDeg": 16.0,
                "dwellSeconds": 0.10,
                "maxSpeed": 0.20,
                "maxAngularSpeed": 1.8,
                "trackingTolerance": 0.14,
            },
        }
        if mode == "phase_clip":
            (asset,) = scene.template_dependencies()
            task["referenceMotion"] = motion | {
                "asset": asset["id"],
                "assetContentHash": asset["contentHash"],
            }
        spec = {"schema": "lw.obs-spec/2", "observations": [term], "action": action_spec}
        policies[action] = write_policy_package(
            user_library, name=action, rev=1, task=task, spec=spec, onnx_bytes=linear_onnx_bytes(weights)
        )
    # Exercise the actual Newton PhysicsPort, Catalog and HostCore with known CPU Policy artifacts.
    with NewtonSimulation(
        scene.builder,
        device="cpu",
        physics_dt=1 / 240,
        solver="mujoco",
        config=prepare_scene_config(scene.runtime_payload()),
    ) as sim:
        manifest = export_manifest(sim, session_id="four-modes", scene_revision="rev", revision_numeric_id=1)
        catalog = load_info_catalog(manifest, scene.name, scene_package=scene)
        config = {
            "jointKeys": joints,
            "prismaticJointKeys": [joints[0]],
            "baseBody": key + "/Mast",
            "policies": policies,
            "commandParams": {"drive": ["vx", "wz"]},
            "locations": {"waypoints": {"A": {"p": [0.18, 0.0, 0.0], "yaw": 0.0}}},
            "xyTolerance": 0.035,
            "maxLinearSpeed": 0.2,
        }
        catalog.replace_program(key, EmbodiedPolicyProgram, config)
        assert Catalog.model_validate(catalog.model_dump()).entities[key].program == "embodied.policy"
        port = PhysicsPort(sim, manifest)
        host = HostCore(catalog, REGISTRY, physics=port, source="sim", publication_clock=lambda: sim.sim_time)

        def call(action, params=None):
            request = InvokeRequest(
                invocationId=action,
                epoch=0,
                entityKey=key,
                action=action,
                params=params or {},
                actor=Actor(kind="user", name="four-modes"),
                requestId=action,
                createdAtMs=1.0,
                createdSimTime=host.sim_time,
            )
            outcome = host.invoke(request)
            assert outcome.status == "accepted", outcome
            for _ in range(240):
                sim.step(4)
                port.capture()
                events = host.tick(sim.sim_time, 1 / 60)
                port.commit()
                assert all(event.type != "server.error" for event in events), events
                outcome = host.invoke(request)
                if outcome.status not in ("running", "accepted"):
                    break
            assert outcome.status == "succeeded", outcome
            assert host.program_execution(key, action=action)["inferenceCalls"] > 0
            assert host.values()[key]["lastCompletedAction"] == action

        call("clip")
        assert port.joint_q(joints[1])[0] > 0.11
        call("recovery")
        assert host.values()[key]["tiltDeg"] < 20.0
        call("drive", {"vx": 0.15, "wz": 0})
        assert port.joint_q(joints[0])[0] > 0.0
        call("navigate", {"waypoint": "A"})
        assert host.values()[key]["currentWaypoint"] == "A"
        assert abs(port.body_pose(key + "/Mast")[0][0] - 0.18) <= 0.035

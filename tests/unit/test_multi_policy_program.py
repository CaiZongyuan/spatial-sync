"""Reusable multi-policy Device Program contract, command injection and envelope admission."""

import hashlib
import json
import math

import numpy as np
import onnx
import pytest
import yaml
from onnx import TensorProto, helper, numpy_helper

from packages.info.catalog import compile_catalog
from packages.info.program import HostCore
from packages.library.common import content_hash
from packages.library.transactions import durable_json
from packages.programs.embodied import EmbodiedPolicyProgram
from packages.programs.policy_base import MultiPolicyConfig, MultiPolicyProgram
from packages.protocol.schema import Actor, InvokeRequest


def linear_onnx_bytes(weights: np.ndarray, bias: np.ndarray | None = None) -> bytes:
    w = np.asarray(weights, dtype=np.float32)
    in_dim, out_dim = w.shape
    initializers = [numpy_helper.from_array(w, "weight")]
    out_name = "linear" if bias is not None else "actions"
    nodes = [helper.make_node("MatMul", ["observations", "weight"], [out_name])]
    if bias is not None:
        b = np.asarray(bias, dtype=np.float32).reshape(1, out_dim)
        initializers.append(numpy_helper.from_array(b, "bias"))
        nodes.append(helper.make_node("Add", ["linear", "bias"], ["actions"]))
    graph = helper.make_graph(
        nodes,
        "multi-policy-fixture",
        [helper.make_tensor_value_info("observations", TensorProto.FLOAT, [1, in_dim])],
        [helper.make_tensor_value_info("actions", TensorProto.FLOAT, [1, out_dim])],
        initializers,
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 10
    onnx.checker.check_model(model)
    return model.SerializeToString()


def write_policy_package(user_root, *, name: str, rev: int, task: dict, spec: dict, onnx_bytes: bytes) -> str:
    policy_id = f"policy/{name}@{rev}"
    dest = user_root / "policies" / name / str(rev)
    pkg = dest / "package"
    (pkg / "artifacts").mkdir(parents=True, exist_ok=True)
    obs_bytes = yaml.safe_dump(spec, sort_keys=True).encode("utf-8")
    (pkg / "obs_spec.yaml").write_bytes(obs_bytes)
    (pkg / "task.json").write_text(json.dumps(task, sort_keys=True), encoding="utf-8")
    (pkg / "artifacts/policy.onnx").write_bytes(onnx_bytes)
    onnx_sha = hashlib.sha256(onnx_bytes).hexdigest()
    obs_sha = hashlib.sha256(obs_bytes).hexdigest()
    durable_json(
        pkg / "artifacts/export.json",
        {
            "checkpoint": "checkpoints/model_0.pt",
            "checkpointSha256": "a" * 64,
            "policy": "policy.onnx",
            "policySha256": onnx_sha,
            "afterLearn": True,
            "iteration": 1,
            "externalData": [],
        },
    )
    provenance = {
        "jobId": f"job-{name}-{rev}",
        "checkpoint": f"job-{name}-{rev}/checkpoints/model_0.pt",
        "checkpointSha256": "a" * 64,
        "onnx": "artifacts/policy.onnx",
        "onnxSha256": onnx_sha,
        "observation": "obs_spec.yaml",
        "observationSha256": obs_sha,
        "task": f"task/{name}@1",
        "taskContentHash": "b" * 64,
        "scene": task["scene"],
        "sceneContentHash": "c" * 64,
        "seed": 0,
    }
    durable_json(pkg / "policy.json", provenance)
    pkg_hash = content_hash(pkg)
    durable_json(
        dest / "metadata.json",
        {"id": policy_id, "name": name, "rev": rev, "title": task["title"], "contentHash": pkg_hash},
    )
    return policy_id


class FakeEmbodiedPort:
    def __init__(self, joints, bodies, *, kinds=None, limits=None):
        self.q = {key: 0.0 for key in joints}
        self.qd = {key: 0.0 for key in joints}
        self.targets = {key: 0.0 for key in joints}
        self.kinds = {key: (kinds or {}).get(key, "revolute") for key in joints}
        self.limits = {key: (limits or {}).get(key, (-0.8, 0.8)) for key in joints}
        self.poses = {
            key: (np.array(val["p"], dtype=float), np.array(val["q"], dtype=float))
            for key, val in bodies.items()
        }
        self.ang_vel = {
            key: np.array(val.get("w", [0.0, 0.0, 0.0]), dtype=float) for key, val in bodies.items()
        }

    def bind_joint(self, key):
        assert key in self.q
        return key

    def joint_metadata(self, key):
        lo, hi = self.limits[key]
        return {
            "kind": self.kinds[key],
            "qWidth": 1,
            "qdWidth": 1,
            "targetWidth": 1,
            "lower": lo,
            "upper": hi,
            "targetDtype": "float32",
        }

    def bind_body(self, key):
        assert key in self.poses
        return key

    def joint_q(self, key):
        return np.array([self.q[key]], dtype=np.float64)

    def joint_qd(self, key):
        return np.array([self.qd[key]], dtype=np.float64)

    def set_joint_target(self, key, values):
        self.targets[key] = float(values[0])

    def body_pose(self, key):
        return self.poses[key]

    def body_angular_velocity(self, key, *, frame="world"):
        assert frame == "world"
        return self.ang_vel[key].copy()


BALANCE_BOT_TYPE = {
    "type": "balance-bot/v1",
    "title": "Balance bot",
    "description": "Custom balancing robot fixture.",
    "properties": {
        "phase": {"schema": {"enum": ["idle", "active", "fault"]}},
        "tiltDeg": {"schema": {"type": "number"}},
        "distanceToGoal": {"schema": {"type": "number"}},
    },
    "actions": {
        "balance": {"params": {}, "required": [], "longRunning": True},
        "moveTo": {
            "params": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "speed": {"type": "number"},
            },
            "required": ["x", "y"],
            "longRunning": True,
        },
        "crouch": {"params": {}, "required": [], "longRunning": True},
        "stop": {"params": {}, "required": [], "concurrent": True},
    },
    "events": {},
}


class CustomBalancePolicyProgram(MultiPolicyProgram):
    id, type, tick_priority = "balance_bot.policy", "balance-bot/v1", 10

    class Config(MultiPolicyConfig):
        teacherGain: float | None = None

    def _facts(self, ctx):
        p, q = ctx.physics.body_pose(self.base_body)
        # Upright z component of local Z axis = 1 - 2*(qx^2 + qy^2)
        cos_tilt = max(-1.0, min(1.0, 1.0 - 2.0 * float(q[0] ** 2 + q[1] ** 2)))
        tilt_deg = math.degrees(math.acos(cos_tilt))
        goal = getattr(self, "_goal_xy", None)
        dist = float(np.hypot(goal[0] - p[0], goal[1] - p[1])) if goal is not None else 0.0
        ctx.set(tiltDeg=round(tilt_deg, 4), distanceToGoal=round(dist, 4))

    def _command(self, inv, ctx, slot):
        if inv.action != "moveTo":
            return None
        p, _ = ctx.physics.body_pose(self.base_body)
        dx = float(inv.params["x"]) - float(p[0])
        dy = float(inv.params["y"]) - float(p[1])
        speed = float(inv.params.get("speed", 0.15))
        norm = math.hypot(dx, dy)
        vx = (dx / norm) * speed if norm > 1e-6 else 0.0
        vy = (dy / norm) * speed if norm > 1e-6 else 0.0
        return [vx, vy, 0.0]

    def _on_action_start(self, inv, ctx, slot):
        self._goal_xy = (float(inv.params["x"]), float(inv.params["y"])) if inv.action == "moveTo" else None
        super()._on_action_start(inv, ctx, slot)


def test_multi_policy_override_replaces_only_target_action_and_preserves_siblings():
    base = {
        "jointKeys": ["/World/Lab/Bot/j1", "/World/Lab/Bot/j2"],
        "baseBody": "/World/Lab/Bot/Base",
        "policies": {"balance": "policy/balance@1", "moveTo": "policy/move@1"},
        "unsharedTeacherField": 42,
    }
    updated = CustomBalancePolicyProgram.apply_policy_override(base, "moveTo", "policy/move@2")
    assert updated["policies"] == {"balance": "policy/balance@1", "moveTo": "policy/move@2"}
    assert "unsharedTeacherField" not in updated
    assert CustomBalancePolicyProgram.bound_policy_for_action(updated, "moveTo") == "policy/move@2"
    assert CustomBalancePolicyProgram.bound_policy_for_action(updated, "balance") == "policy/balance@1"
    assert CustomBalancePolicyProgram.bound_policies(updated) == {"policy/balance@1", "policy/move@2"}
    assert CustomBalancePolicyProgram.policy_joints(updated) == {
        "/World/Lab/Bot/j1": "rad",
        "/World/Lab/Bot/j2": "rad",
    }


def test_multi_policy_runtime_dispatches_actions_injects_commands_and_isolates_evidence(
    tmp_path, monkeypatch
):
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))

    joints = ["/World/Lab/Bot/j1", "/World/Lab/Bot/j2"]
    base = "/World/Lab/Bot/Base"

    balance_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "gravity",
                "source": "projected_gravity",
                "key": base,
                "unit": "1",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "ang_vel",
                "source": "body_angular_velocity",
                "key": base,
                "frame": "body",
                "unit": "rad/s",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "prev",
                "source": "previous_action",
                "frame": "world",
                "unit": "1",
                "shape": [2],
                "offset": [0.0, 0.0],
                "scale": [1.0, 1.0],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad", "rad"],
            "defaultPosition": [0.1, -0.1],
            "scale": [0.2, 0.2],
            "lower": [-0.6, -0.6],
            "upper": [0.6, 0.6],
        },
    }
    balance_task = {
        "name": "balance-bot",
        "title": "Balance upright",
        "scene": "bot-arena@1",
        "robot": "/World/Lab/Bot",
        "implements": "balance-bot/v1.balance",
        "episode": {"params": {}},
        "success": {"property": "tiltDeg", "lte": 5.0},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    balance_id = write_policy_package(
        user_lib,
        name="balance-bot",
        rev=1,
        task=balance_task,
        spec=balance_spec,
        onnx_bytes=linear_onnx_bytes(np.zeros((8, 2), dtype=np.float32), bias=np.array([0.5, -0.5])),
    )

    move_spec = {
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
                "lower": [-0.3, -0.3, -0.6],
                "upper": [0.3, 0.3, 0.6],
            },
            {
                "name": "prev",
                "source": "previous_action",
                "frame": "world",
                "unit": "1",
                "shape": [2],
                "offset": [0.0, 0.0],
                "scale": [1.0, 1.0],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad", "rad"],
            "defaultPosition": [0.0, 0.0],
            "scale": [1.0, 1.0],
            "lower": [-0.5, -0.5],
            "upper": [0.5, 0.5],
        },
    }
    move_task = {
        "name": "move-bot",
        "title": "Move to XY",
        "scene": "bot-arena@1",
        "robot": "/World/Lab/Bot",
        "implements": "balance-bot/v1.moveTo",
        "episode": {
            "params": {"speed": 0.15},
            "paramBounds": {"x": [-1.0, 1.0], "y": [-1.0, 1.0], "speed": [0.05, 0.25]},
            "commandEnvelope": [[-0.25, 0.25], [-0.25, 0.25], [-0.5, 0.5]],
        },
        "success": {"property": "distanceToGoal", "lte": 0.05},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    # Map command [vx, vy, wz] directly to [j1=vx, j2=vy]
    w_move = np.zeros((5, 2), dtype=np.float32)
    w_move[0, 0] = 1.0
    w_move[1, 1] = 1.0
    move_id = write_policy_package(
        user_lib,
        name="move-bot",
        rev=1,
        task=move_task,
        spec=move_spec,
        onnx_bytes=linear_onnx_bytes(w_move),
    )

    # Start tilted 20 deg around X so balance does not immediately complete on tick 0
    tilt_rad = math.radians(20.0)
    port = FakeEmbodiedPort(
        joints,
        {
            base: {
                "p": [0.0, 0.0, 0.3],
                "q": [math.sin(tilt_rad / 2), 0.0, 0.0, math.cos(tilt_rad / 2)],
                "w": [0.1, -0.2, 0.0],
            }
        },
    )
    raw_cfg = {
        "jointKeys": joints,
        "baseBody": base,
        "policies": {"balance": balance_id, "moveTo": move_id},
        "maxSeconds": 5.0,
    }
    registry = {CustomBalancePolicyProgram.id: CustomBalancePolicyProgram}
    catalog = compile_catalog(
        "rev-1",
        {"balance-bot/v1": BALANCE_BOT_TYPE},
        {
            "/World/Lab/Bot": {
                "key": "/World/Lab/Bot",
                "type": "balance-bot/v1",
                "program": CustomBalancePolicyProgram.id,
                "config": raw_cfg,
            }
        },
        {"/World/Lab/Bot": CustomBalancePolicyProgram.Config.model_validate(raw_cfg)},
    )
    host = HostCore(catalog, registry, physics=port, source="sim", fact_hz=20.0)
    actor = Actor(kind="user", name="tester")

    def req(inv_id, action, params=None):
        return InvokeRequest(
            invocationId=inv_id,
            requestId="req-" + inv_id,
            entityKey="/World/Lab/Bot",
            action=action,
            params=params or {},
            actor=actor,
            epoch=0,
            createdSimTime=host.sim_time,
            createdAtMs=1000.0,
        )

    # 1. Reject unbound action
    unbound = host.invoke(req("inv-unbound", "crouch"))
    assert unbound.status == "rejected"

    # 2. Reject out-of-envelope moveTo parameter
    out_of_bounds = host.invoke(req("inv-oob", "moveTo", {"x": 2.5, "y": 0.0}))
    assert out_of_bounds.status == "rejected"

    # 3. Run balance action for one tick, then upright -> succeeds
    bal_accept = host.invoke(req("inv-bal", "balance"))
    assert bal_accept.status in ("accepted", "running")
    host.tick(0.05, 0.05)
    # defaultPosition [0.1, -0.1] + bias [0.5, -0.5] * scale [0.2, 0.2] = [0.2, -0.2]
    assert port.targets[joints[0]] == pytest.approx(0.2, abs=1e-6)
    assert port.targets[joints[1]] == pytest.approx(-0.2, abs=1e-6)

    # Restore upright orientation -> next tick satisfies tiltDeg <= 5.0
    port.poses[base] = (np.array([0.0, 0.0, 0.3]), np.array([0.0, 0.0, 0.0, 1.0]))
    events = host.tick(0.10, 0.05)
    assert any(
        getattr(ev, "invocationId", None) == "inv-bal" and getattr(ev, "status", None) == "succeeded"
        for ev in events
    )

    # 4. Run moveTo action with continuous command -> drives joints [0.2, 0.0], then stop cancels & holds
    move_accept = host.invoke(req("inv-move", "moveTo", {"x": 0.8, "y": 0.0, "speed": 0.2}))
    assert move_accept.status in ("accepted", "running")
    port.q[joints[0]], port.q[joints[1]] = 0.12, -0.08
    host.tick(0.15, 0.05)
    assert port.targets[joints[0]] == pytest.approx(0.2, abs=1e-6)
    assert port.targets[joints[1]] == pytest.approx(0.0, abs=1e-6)

    stop_res = host.invoke(req("inv-stop", "stop"))
    assert stop_res.status in ("accepted", "running", "succeeded")
    host.tick(0.20, 0.05)
    assert port.targets[joints[0]] == pytest.approx(0.12, abs=1e-6)
    assert port.targets[joints[1]] == pytest.approx(-0.08, abs=1e-6)

    # 5. Per-action execution evidence stays strictly isolated via public HostCore API
    bal_ev = host.program_execution("/World/Lab/Bot", action="balance")
    move_ev = host.program_execution("/World/Lab/Bot", action="moveTo")
    assert bal_ev["policy"] == balance_id and bal_ev["actionStarts"] == 1 and bal_ev["inferenceCalls"] == 1
    assert move_ev["policy"] == move_id and move_ev["actionStarts"] == 1 and move_ev["inferenceCalls"] == 1
    assert bal_ev["observationDigest"] != move_ev["observationDigest"]


def test_embodied_policy_per_action_command_params_and_physical_joint_limits(tmp_path, monkeypatch):
    user_lib = tmp_path / "library"
    state_root = tmp_path / "state"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state_root))

    joints = ["/World/Lab/Rig/pivot", "/World/Lab/Rig/slider"]
    base = "/World/Lab/Rig/Base"

    rig_type = {
        "type": "slider-rig/v1",
        "title": "Slider rig",
        "description": "Mixed revolute/prismatic embodied fixture.",
        "properties": {
            "phase": {"schema": {"enum": ["idle", "active", "fault"]}},
            "tiltDeg": {"schema": {"type": "number"}},
        },
        "actions": {
            "stabilize": {"params": {}, "required": [], "longRunning": True},
            "drive": {
                "params": {"vx": {"type": "number"}, "wz": {"type": "number"}},
                "required": ["vx", "wz"],
                "longRunning": True,
            },
            "stop": {"params": {}, "required": [], "concurrent": True},
        },
        "events": {},
    }

    stabilize_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "gravity",
                "source": "projected_gravity",
                "key": base,
                "unit": "1",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            }
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad", "m"],
            "defaultPosition": [0.0, 0.0],
            "scale": [0.1, 0.05],
            "lower": [-0.5, -0.15],
            "upper": [0.5, 0.15],
        },
    }
    stabilize_task = {
        "name": "rig-stabilize",
        "title": "Stabilize",
        "scene": "rig-scene@1",
        "robot": "/World/Lab/Rig",
        "implements": "slider-rig/v1.stabilize",
        "episode": {"params": {}},
        "success": {"property": "tiltDeg", "lte": 1.0},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    stabilize_id = write_policy_package(
        user_lib,
        name="rig-stabilize",
        rev=1,
        task=stabilize_task,
        spec=stabilize_spec,
        onnx_bytes=linear_onnx_bytes(np.zeros((3, 2), dtype=np.float32), bias=np.array([1.0, -1.0])),
    )

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
                "lower": [-0.4, -0.8],
                "upper": [0.4, 0.8],
            }
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad", "m"],
            "defaultPosition": [0.0, 0.0],
            "scale": [1.0, 0.5],
            "lower": [-0.5, -0.15],
            "upper": [0.5, 0.15],
        },
    }
    drive_task = {
        "name": "rig-drive",
        "title": "Drive",
        "scene": "rig-scene@1",
        "robot": "/World/Lab/Rig",
        "implements": "slider-rig/v1.drive",
        "episode": {
            "params": {},
            "paramBounds": {"vx": [-0.3, 0.3], "wz": [-0.6, 0.6]},
            "commandEnvelope": [[-0.3, 0.3], [-0.6, 0.6]],
        },
        "success": {"property": "tiltDeg", "lte": 1.0},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    drive_id = write_policy_package(
        user_lib,
        name="rig-drive",
        rev=1,
        task=drive_task,
        spec=drive_spec,
        onnx_bytes=linear_onnx_bytes(np.eye(2, dtype=np.float32)),
    )

    tilt_rad = math.radians(10.0)
    # Physical joint_metadata declares slider as prismatic with tight [-0.15, 0.15] m limits
    port = FakeEmbodiedPort(
        joints,
        {
            base: {
                "p": [0.0, 0.0, 0.2],
                "q": [math.sin(tilt_rad / 2), 0.0, 0.0, math.cos(tilt_rad / 2)],
                "w": [0.0, 0.0, 0.0],
            }
        },
        kinds={joints[0]: "revolute", joints[1]: "prismatic"},
        limits={joints[0]: (-0.6, 0.6), joints[1]: (-0.15, 0.15)},
    )

    # Notice: no manual prismaticJointKeys needed — joint_metadata derives ["rad", "m"] directly!
    cfg = {
        "jointKeys": joints,
        "baseBody": base,
        "policies": {"stabilize": stabilize_id, "drive": drive_id},
        "commandParams": {"drive": ["vx", "wz"]},
    }
    registry = {EmbodiedPolicyProgram.id: EmbodiedPolicyProgram}
    catalog = compile_catalog(
        "rev-rig",
        {"slider-rig/v1": rig_type},
        {
            "/World/Lab/Rig": {
                "key": "/World/Lab/Rig",
                "type": "slider-rig/v1",
                "program": EmbodiedPolicyProgram.id,
                "config": cfg,
            }
        },
        {"/World/Lab/Rig": EmbodiedPolicyProgram.Config.model_validate(cfg)},
    )
    host = HostCore(catalog, registry, physics=port, source="sim", fact_hz=20.0)
    actor = Actor(kind="user", name="tester")

    def req(inv_id, action, params=None):
        return InvokeRequest(
            invocationId=inv_id,
            requestId="req-" + inv_id,
            entityKey="/World/Lab/Rig",
            action=action,
            params=params or {},
            actor=actor,
            epoch=0,
            createdSimTime=host.sim_time,
            createdAtMs=1000.0,
        )

    # Invoking zero-param stabilize succeeds without colliding with drive's commandParams
    assert host.invoke(req("s1", "stabilize")).status in ("accepted", "running")
    host.tick(0.05, 0.05)
    assert port.targets[joints[0]] == pytest.approx(0.1)
    assert port.targets[joints[1]] == pytest.approx(-0.05)
    host.invoke(req("stop1", "stop"))
    host.tick(0.10, 0.05)

    # Invoking drive maps vx, wz cleanly via commandParams
    assert host.invoke(req("d1", "drive", {"vx": 0.2, "wz": -0.2})).status in ("accepted", "running")
    host.tick(0.15, 0.05)
    assert port.targets[joints[0]] == pytest.approx(0.2)
    assert port.targets[joints[1]] == pytest.approx(-0.10)

    # Tighter physical joint limits than spec.action.upper are rejected at init
    tight_port = FakeEmbodiedPort(
        joints,
        {base: {"p": [0.0, 0.0, 0.2], "q": [0.0, 0.0, 0.0, 1.0]}},
        kinds={joints[0]: "revolute", joints[1]: "prismatic"},
        limits={joints[0]: (-0.6, 0.6), joints[1]: (-0.05, 0.05)},
    )
    bad_host = HostCore(catalog, registry, physics=tight_port, source="sim", fact_hz=20.0)
    assert bad_host.invoke(req("s-bad", "stabilize")).status == "rejected"

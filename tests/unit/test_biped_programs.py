"""Unit tests for biped/v1 EntityType, biped.posture, PolicySlot entity remapping, and biped.policy."""

import math
from pathlib import Path

import numpy as np
import pytest
import yaml

from packages.info.catalog import DEVICE_ROOT, compile_catalog
from packages.info.program import HostCore
from packages.policies.observations import ObservationSpec, remap_observation_spec_entity
from packages.protocol.math import rotate_vector
from packages.protocol.schema import Actor, InvokeRequest
from packages.sim_runtime.info_catalog import REGISTRY
from tests.unit.test_multi_policy_program import FakeEmbodiedPort, linear_onnx_bytes, write_policy_package


class KinematicBipedPort(FakeEmbodiedPort):
    """Fake PhysicsPort that integrates commanded joint targets and optional planar motion."""

    def __init__(self, joints, base, *, pose_p=(0.0, 0.0, 0.25), pose_q=(0.0, 0.0, 0.0, 1.0), limits=None):
        super().__init__(
            joints,
            {base: {"p": list(pose_p), "q": list(pose_q), "w": [0.0, 0.0, 0.0]}},
            limits=limits or {j: (-1.2, 1.2) for j in joints},
        )
        self.base = base
        self.lin_vel = {base: np.zeros(3, dtype=float)}

    def body_linear_velocity(self, key, *, frame="world"):
        v_world = self.lin_vel[key]
        if frame == "world":
            return v_world.copy()
        _, q = self.poses[key]
        q_inv = np.array([-q[0], -q[1], -q[2], q[3]], dtype=float)
        return np.asarray(rotate_vector(q_inv.tolist(), v_world.tolist()), dtype=float)

    def body_angular_velocity(self, key, *, frame="world"):
        w_world = self.ang_vel[key]
        if frame == "world":
            return w_world.copy()
        _, q = self.poses[key]
        q_inv = np.array([-q[0], -q[1], -q[2], q[3]], dtype=float)
        return np.asarray(rotate_vector(q_inv.tolist(), w_world.tolist()), dtype=float)

    def set_euler(self, *, roll_deg=0.0, pitch_deg=0.0, yaw_rad=0.0):
        r = math.radians(roll_deg) * 0.5
        p = math.radians(pitch_deg) * 0.5
        y = yaw_rad * 0.5
        cr, sr = math.cos(r), math.sin(r)
        cp, sp = math.cos(p), math.sin(p)
        cy, sy = math.cos(y), math.sin(y)
        qx = sr * cp * cy - cr * sp * sy
        qy = cr * sp * cy + sr * cp * sy
        qz = cr * cp * sy - sr * sp * cy
        qw = cr * cp * cy + sr * sp * sy
        pos, _ = self.poses[self.base]
        self.poses[self.base] = (pos, np.array([qx, qy, qz, qw], dtype=float))


def _load_biped_type() -> dict:
    path = DEVICE_ROOT / "types" / "biped.v1.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _make_biped_policies(user_lib: Path, *, robot: str = "/World/Lab/ArenaBiped", scene: str = "arena@1"):
    joints = [f"{robot}/LeftHip", f"{robot}/RightHip", f"{robot}/LeftKnee", f"{robot}/RightKnee"]
    trunk = f"{robot}/Trunk"

    # walkTo: 4 joint_pos + 3 ang_vel + 3 proj_grav + 3 command = 13 inputs -> 4 joint_position_offset outputs
    # Map command [vx, vy, wz] directly into the first 3 joints so tests inspect the exact injected command.
    walk_weights = np.zeros((13, 4), dtype=np.float32)
    walk_weights[10, 0] = 1.0  # vx -> LeftHip offset
    walk_weights[11, 1] = 1.0  # vy -> RightHip offset
    walk_weights[12, 2] = 1.0  # wz -> LeftKnee offset
    walk_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "joint_pos",
                "source": "joint_position",
                "keys": joints,
                "frame": "world",
                "unit": "rad",
                "shape": [4],
                "offset": [0.0] * 4,
                "scale": [1.0] * 4,
            },
            {
                "name": "ang_vel",
                "source": "body_angular_velocity",
                "key": trunk,
                "frame": "body",
                "unit": "rad/s",
                "shape": [3],
                "offset": [0.0] * 3,
                "scale": [1.0] * 3,
            },
            {
                "name": "proj_grav",
                "source": "projected_gravity",
                "key": trunk,
                "unit": "1",
                "shape": [3],
                "offset": [0.0] * 3,
                "scale": [1.0] * 3,
            },
            {
                "name": "vel_cmd",
                "source": "command",
                "frame": "body",
                "unit": "1",
                "shape": [3],
                "offset": [0.0] * 3,
                "scale": [1.0] * 3,
                "lower": [-0.20, -0.12, -0.60],
                "upper": [0.25, 0.12, 0.60],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad"] * 4,
            "defaultPosition": [0.0, 0.0, 0.0, 0.0],
            "scale": [1.0] * 4,
            "lower": [-1.0] * 4,
            "upper": [1.0] * 4,
        },
    }
    walk_task = {
        "name": "biped-walk",
        "title": "Walk to waypoint",
        "scene": scene,
        "robot": robot,
        "implements": "biped/v1.walkTo",
        "episode": {
            "params": {"waypoint": "training_target"},
            "commandEnvelope": [[-0.20, 0.25], [-0.12, 0.12], [-0.60, 0.60]],
        },
        "success": {"property": "phase", "equals": "idle"},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    walk_id = write_policy_package(
        user_lib,
        name="biped-walk",
        rev=1,
        task=walk_task,
        spec=walk_spec,
        onnx_bytes=linear_onnx_bytes(walk_weights),
    )

    # standUp: 4 joint_pos + 3 proj_grav = 7 inputs -> 4 outputs
    stand_weights = np.zeros((7, 4), dtype=np.float32)
    stand_bias = np.array([0.05, 0.05, -0.05, -0.05], dtype=np.float32)
    stand_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "joint_pos",
                "source": "joint_position",
                "keys": joints,
                "frame": "world",
                "unit": "rad",
                "shape": [4],
                "offset": [0.0] * 4,
                "scale": [1.0] * 4,
            },
            {
                "name": "proj_grav",
                "source": "projected_gravity",
                "key": trunk,
                "unit": "1",
                "shape": [3],
                "offset": [0.0] * 3,
                "scale": [1.0] * 3,
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": joints,
            "units": ["rad"] * 4,
            "defaultPosition": [0.0] * 4,
            "scale": [1.0] * 4,
            "lower": [-1.0] * 4,
            "upper": [1.0] * 4,
        },
    }
    stand_task = {
        "name": "biped-stand",
        "title": "Stand up",
        "scene": scene,
        "robot": robot,
        "implements": "biped/v1.standUp",
        "episode": {"params": {"fromFall": "front"}},
        "success": {"property": "fallen", "equals": False},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    stand_id = write_policy_package(
        user_lib,
        name="biped-stand",
        rev=1,
        task=stand_task,
        spec=stand_spec,
        onnx_bytes=linear_onnx_bytes(stand_weights, bias=stand_bias),
    )

    # bow: 4 joint_pos + 3 proj_grav = 7 inputs -> 4 outputs
    bow_weights = np.zeros((7, 4), dtype=np.float32)
    bow_bias = np.array([0.35, 0.35, 0.0, 0.0], dtype=np.float32)
    bow_task = {
        "name": "biped-bow",
        "title": "Bow",
        "scene": scene,
        "robot": robot,
        "implements": "biped/v1.bow",
        "episode": {"params": {}},
        "success": {"property": "phase", "equals": "idle"},
        "control": {"hz": 20.0, "action": "joint_position_offset"},
    }
    bow_id = write_policy_package(
        user_lib,
        name="biped-bow",
        rev=1,
        task=bow_task,
        spec=stand_spec,
        onnx_bytes=linear_onnx_bytes(bow_weights, bias=bow_bias),
    )

    return {"walkTo": walk_id, "standUp": stand_id, "bow": bow_id}


def test_biped_type_and_programs_have_no_robot_specific_hardcoding():
    biped_yaml = (DEVICE_ROOT / "types" / "biped.v1.yaml").read_text(encoding="utf-8")
    assert "microduck" not in biped_yaml.lower()

    definition = _load_biped_type()
    assert definition["type"] == "biped/v1"
    expected_props = {
        "phase",
        "activeAction",
        "lastCompletedAction",
        "fallen",
        "jointPositions",
        "currentWaypoint",
        "targetWaypoint",
        "basePosition",
        "yawRad",
        "tiltDeg",
        "pitchDeg",
        "bodyVelocity",
    }
    assert expected_props <= set(definition["properties"])
    assert set(definition["actions"]) == {"walkTo", "standUp", "bow", "stop"}
    assert definition["actions"]["stop"]["concurrent"] is True

    biped_dir = Path(__file__).resolve().parents[2] / "packages" / "programs" / "biped"
    py_files = list(biped_dir.glob("*.py"))
    assert py_files, "Expected packages/programs/biped/*.py modules"
    for path in py_files:
        text = path.read_text(encoding="utf-8").lower()
        assert "microduck" not in text

    assert "biped.posture" in REGISTRY
    assert "biped.policy" in REGISTRY


def test_remap_observation_spec_entity_remaps_joints_bodies_and_frames():
    raw = ObservationSpec.model_validate(
        {
            "schema": "lw.obs-spec/1",
            "observations": [
                {
                    "name": "q",
                    "source": "joint_position",
                    "keys": ["/World/Lab/RobotA/J1", "/World/Lab/RobotA/J2"],
                    "frame": "world",
                    "unit": "rad",
                    "shape": [2],
                    "offset": [0.0, 0.0],
                    "scale": [1.0, 1.0],
                },
                {
                    "name": "rel_pos",
                    "source": "body_position",
                    "key": "/World/Lab/RobotA/Foot",
                    "frame": "/World/Lab/RobotA/Trunk",
                    "unit": "m",
                    "shape": [3],
                    "offset": [0.0, 0.0, 0.0],
                    "scale": [1.0, 1.0, 1.0],
                },
            ],
            "action": {
                "space": "joint_position_offset",
                "jointKeys": ["/World/Lab/RobotA/J1", "/World/Lab/RobotA/J2"],
                "units": ["rad", "rad"],
                "defaultPosition": [0.0, 0.0],
                "scale": [1.0, 1.0],
                "lower": [-1.0, -1.0],
                "upper": [1.0, 1.0],
            },
        }
    )
    remapped = remap_observation_spec_entity(raw, "/World/Lab/RobotA", "/World/Lab/Patrol/RobotB")
    assert remapped.action.jointKeys == ["/World/Lab/Patrol/RobotB/J1", "/World/Lab/Patrol/RobotB/J2"]
    assert remapped.observations[0].keys == ["/World/Lab/Patrol/RobotB/J1", "/World/Lab/Patrol/RobotB/J2"]
    assert remapped.observations[1].key == "/World/Lab/Patrol/RobotB/Foot"
    assert remapped.observations[1].frame == "/World/Lab/Patrol/RobotB/Trunk"


def test_biped_posture_program_publishes_telemetry_and_validates_waypoints():
    biped_type = _load_biped_type()
    posture_cls = REGISTRY["biped.posture"]
    entity = "/World/Lab/Biped"
    joints = [f"{entity}/J1", f"{entity}/J2"]
    trunk = f"{entity}/Trunk"
    port = KinematicBipedPort(joints, trunk)
    cfg = {
        "jointKeys": joints,
        "baseBody": trunk,
        "defaultPosition": [0.1, -0.1],
        "locations": {"waypoints": {"home": {"p": [0.0, 0.0, 0.0], "yaw": 0.0}}},
        "actionDuration": 0.1,
    }
    catalog = compile_catalog(
        "rev-posture",
        {"biped/v1": biped_type},
        {entity: {"key": entity, "type": "biped/v1", "program": posture_cls.id, "config": cfg}},
        {entity: posture_cls.Config.model_validate(cfg)},
    )
    host = HostCore(catalog, REGISTRY, physics=port, source="sim", fact_hz=20.0)
    vals = host.values()[entity]
    assert vals["phase"] == "idle"
    assert vals["fallen"] is False
    assert vals["activeAction"] is None
    assert port.targets[joints[0]] == pytest.approx(0.1)

    actor = Actor(kind="user", name="op")
    bad = host.invoke(
        InvokeRequest(
            invocationId="w-bad",
            requestId="r-bad",
            entityKey=entity,
            action="walkTo",
            params={"waypoint": "missing"},
            actor=actor,
            epoch=0,
            createdSimTime=0.0,
            createdAtMs=1.0,
        )
    )
    assert bad.status == "rejected"
    assert bad.error.code == "unknown_waypoint"


def test_biped_policy_cross_entity_waypoint_guidance_fall_standup_bow_and_stop(tmp_path, monkeypatch):
    user_lib = tmp_path / "user-lib"
    monkeypatch.setenv("LW_LIBRARY", str(user_lib))

    # Author policies for /World/Lab/ArenaBiped on arena@1, then bind them to /World/Lab/PatrolBiped!
    policies = _make_biped_policies(user_lib, robot="/World/Lab/ArenaBiped", scene="arena@1")
    biped_type = _load_biped_type()
    policy_cls = REGISTRY["biped.policy"]

    entity = "/World/Lab/PatrolBiped"
    joints = [f"{entity}/LeftHip", f"{entity}/RightHip", f"{entity}/LeftKnee", f"{entity}/RightKnee"]
    trunk = f"{entity}/Trunk"
    port = KinematicBipedPort(joints, trunk)

    cfg = {
        "jointKeys": joints,
        "baseBody": trunk,
        "defaultPosition": [0.0, 0.0, 0.0, 0.0],
        "policies": policies,
        "locations": {
            "waypoints": {
                "checkpoint_east": {
                    "p": [0.8, 0.1, 0.0],
                    "yaw": 0.3,
                    "xyTolerance": 0.08,
                    "yawTolerance": 0.15,
                },
                "checkpoint_far": {"p": [5.0, 0.0, 0.0], "yaw": 0.0},
            }
        },
        "dwellSeconds": 0.15,
        "commandSmoothing": 1.0,
        "bowMinPitchDeg": 8.0,
        "bowDurationSeconds": 0.25,
        "maxTiltDeg": 50.0,
        "uprightTiltDeg": 15.0,
    }
    catalog = compile_catalog(
        "rev-patrol",
        {"biped/v1": biped_type},
        {entity: {"key": entity, "type": "biped/v1", "program": policy_cls.id, "config": cfg}},
        {entity: policy_cls.Config.model_validate(cfg)},
    )
    sim_t = 0.0
    host = HostCore(
        catalog, REGISTRY, physics=port, source="sim", fact_hz=20.0, publication_clock=lambda: sim_t
    )
    actor = Actor(kind="user", name="tester")
    invocations = {}

    def step(n=1, dt=0.05):
        nonlocal sim_t
        for _ in range(n):
            sim_t = round(sim_t + dt, 6)
            for ev in host.tick(sim_t, dt):
                if hasattr(ev, "invocationId") and hasattr(ev, "status"):
                    invocations[ev.invocationId] = ev

    def req(inv_id, action, params=None):
        return InvokeRequest(
            invocationId=inv_id,
            requestId="req-" + inv_id,
            entityKey=entity,
            action=action,
            params=params or {},
            actor=actor,
            epoch=0,
            createdSimTime=sim_t,
            createdAtMs=1000.0,
        )

    # 1. Unknown waypoint rejected with unknown_waypoint
    rej_unknown = host.invoke(req("unk", "walkTo", {"waypoint": "no_such_wp"}))
    assert rej_unknown.status == "rejected"
    assert rej_unknown.error.code == "unknown_waypoint"

    # 2. walkTo("checkpoint_east") accepted despite cross-scene waypoint name & remapped entity path!
    w1 = host.invoke(req("w1", "walkTo", {"waypoint": "checkpoint_east"}))
    assert w1.status in ("accepted", "running")
    step(1)
    vals = host.values()[entity]
    assert vals["phase"] == "walking"
    assert vals["activeAction"] == "walkTo"
    assert vals["targetWaypoint"] == "checkpoint_east"
    # Verify outer velocity command was clamped to walkTo's command bounds [0.25, 0.12, 0.60]!
    assert port.targets[joints[0]] == pytest.approx(0.25)  # vx clamped at 0.25
    assert port.targets[joints[1]] == pytest.approx(0.12)  # vy = 1.5 * 0.1 = 0.15 -> clamped at 0.12
    assert port.targets[joints[2]] == pytest.approx(0.60)  # wz = 2.0 * 0.3 = 0.60

    # Move robot inside checkpoint_east tolerance -> commands [0, 0, 0] and dwells for 0.15s (3 ticks)
    port.poses[trunk] = (np.array([0.78, 0.11, 0.25]), port.poses[trunk][1])
    port.set_euler(yaw_rad=0.28)
    step(1)
    assert port.targets[joints[0]] == pytest.approx(0.0)
    assert port.targets[joints[1]] == pytest.approx(0.0)
    assert port.targets[joints[2]] == pytest.approx(0.0)
    assert invocations.get("w1") is None or invocations["w1"].status == "running"  # Still dwelling!

    # Drift outside tolerance resets dwell timer
    port.poses[trunk] = (np.array([0.60, 0.11, 0.25]), port.poses[trunk][1])
    step(1)
    assert invocations["w1"].status == "running"

    # Return inside tolerance and dwell for 4 ticks (0.20s >= 0.15s)
    port.poses[trunk] = (np.array([0.79, 0.10, 0.25]), port.poses[trunk][1])
    port.set_euler(yaw_rad=0.30)
    step(4)
    assert invocations["w1"].status == "succeeded"
    vals = host.values()[entity]
    assert vals["currentWaypoint"] == "checkpoint_east"
    assert vals["targetWaypoint"] is None
    assert vals["activeAction"] is None
    assert vals["phase"] == "idle"

    # 3. Concurrent stop cancels active walkTo
    w2 = host.invoke(req("w2", "walkTo", {"waypoint": "checkpoint_far"}))
    assert w2.status in ("accepted", "running")
    step(1)
    assert host.values()[entity]["phase"] == "walking"
    stop_res = host.invoke(req("stop-w2", "stop"))
    assert stop_res.status in ("accepted", "running")
    step(1)
    assert invocations["stop-w2"].status == "succeeded"
    assert invocations["w2"].status == "cancelled"
    assert host.values()[entity]["phase"] == "idle"
    assert host.values()[entity]["targetWaypoint"] is None

    # 4. Tip-over during walkTo -> robot_fallen, fallen=True, walkTo & bow preconditions reject
    w3 = host.invoke(req("w3", "walkTo", {"waypoint": "checkpoint_far"}))
    assert w3.status in ("accepted", "running")
    step(1)
    port.set_euler(roll_deg=62.0)  # Exceeds maxTiltDeg=50.0
    step(1)
    assert invocations["w3"].status == "failed"
    assert invocations["w3"].error.code == "robot_fallen"
    vals = host.values()[entity]
    assert vals["fallen"] is True
    assert vals["phase"] == "fallen"
    assert vals["activeAction"] is None
    assert vals["targetWaypoint"] is None

    assert host.invoke(req("w-fallen", "walkTo", {"waypoint": "checkpoint_east"})).status == "rejected"
    assert host.invoke(req("b-fallen", "bow")).status == "rejected"

    # 5. standUp({}) from fallen=True recovers upright without triggering maxTiltDeg abort!
    su1 = host.invoke(req("su1", "standUp"))
    assert su1.status in ("accepted", "running")
    step(2)  # Still tilted at 62 deg -> must NOT fail with robot_fallen during standUp!
    assert host.values()[entity]["phase"] == "standing_up"
    port.set_euler(roll_deg=5.0, pitch_deg=0.0, yaw_rad=0.0)
    step(4)
    assert invocations["su1"].status == "succeeded"
    assert host.program_execution(entity, action="standUp")["inferenceCalls"] >= 2
    vals = host.values()[entity]
    assert vals["fallen"] is False
    assert vals["phase"] == "idle"

    # 6. standUp({}) when upright without fromFall is rejected; with fromFall="front" induces fall & recovers!
    su_bad = host.invoke(req("su-bad", "standUp"))
    assert su_bad.status == "rejected"
    assert su_bad.error.code == "precondition_failed"

    su_fall = host.invoke(req("su-fall", "standUp", {"fromFall": "front"}))
    assert su_fall.status in ("accepted", "running")
    step(1)
    # Standard joint_target_q lean posture was commanded (no state teleportation!)
    assert any(abs(port.targets[j]) > 0.05 for j in joints)
    # Simulate physics tipping forward past maxTiltDeg
    port.set_euler(pitch_deg=55.0)
    step(1)
    assert host.values()[entity]["fallen"] is True
    # Now ONNX standUp slot takes over and recovers upright
    port.set_euler(pitch_deg=3.0)
    step(4)
    assert invocations["su-fall"].status == "succeeded"
    assert host.values()[entity]["fallen"] is False
    assert host.values()[entity]["phase"] == "idle"

    # 7. bow() requires real pitch milestone + inferenceCalls > 0 + return to upright dwell
    b1 = host.invoke(req("b1", "bow"))
    assert b1.status in ("accepted", "running")
    step(1)  # Robot still at pitch=3.0 < bowMinPitchDeg=8.0 -> MUST NOT succeed on tick 0!
    assert invocations["b1"].status == "running"
    assert host.values()[entity]["phase"] == "bowing"
    port.set_euler(pitch_deg=18.0)  # Reaches bow pitch milestone
    step(5)
    port.set_euler(pitch_deg=2.0)  # Returns upright and dwells
    step(4)
    assert invocations["b1"].status == "succeeded"
    assert host.program_execution(entity, action="bow")["inferenceCalls"] >= 2
    assert host.values()[entity]["phase"] == "idle"

    # 8. motion_timeout resets activeAction/targetWaypoint and transitions back to idle
    w_timeout = host.invoke(req("w-timeout", "walkTo", {"waypoint": "checkpoint_far"}))
    assert w_timeout.status in ("accepted", "running")
    step(601, dt=0.05)  # 30.05s >= maxSeconds=30.0
    assert invocations["w-timeout"].status == "failed"
    assert invocations["w-timeout"].error.code == "motion_timeout"
    vals = host.values()[entity]
    assert vals["phase"] == "idle"
    assert vals["activeAction"] is None
    assert vals["targetWaypoint"] is None

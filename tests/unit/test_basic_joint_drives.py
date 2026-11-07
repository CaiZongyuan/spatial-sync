"""Real Catalog/HostCore admission with deliberately independent motor feedback."""

import itertools
import os
import struct
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from apps.api.app.library_validation import program_schemas
from packages.info.catalog import load_catalog
from packages.info.pipeline import ActionPipeline
from packages.info.program import HostCore
from packages.info.store import StateStore
from packages.library.structural import validate
from packages.programs.joint_drive import assert_disjoint_joint_owners
from packages.programs.linear_axis import LinearAxisProgram
from packages.programs.motorized_incubator import MotorizedIncubatorProgram
from packages.programs.panda_wide import PandaWideProgram
from packages.programs.plate_imager import PlateImagerProgram
from packages.protocol.schema import ActionRequest, Actor, InvokeRequest, SceneManifest, SessionOptions

ROOT = Path(__file__).resolve().parents[2]
ARM, RAIL, DOORS, IMAGER = ("/World/Lab/" + name for name in ("Panda", "Rail", "Incubator", "Imager"))
ARM_JOINTS = [f"/World/Lab/panda_rail/panda__joint{i}" for i in range(1, 8)]
FINGERS = [f"/World/Lab/panda_rail/panda__finger_joint{i}" for i in (1, 2)]
RAIL_JOINT = "/World/Lab/panda_rail/rail__slide"
OUTER, INNER = (f"/World/Lab/incubator/incubator__{name}_door_hinge" for name in ("outer", "inner"))
DRAWER = "/World/Lab/imager/imager__drawer_slide"
PROGRAMS = (PandaWideProgram, LinearAxisProgram, MotorizedIncubatorProgram, PlateImagerProgram)
REGISTRY = {program.id: program for program in PROGRAMS}
TOKENS = itertools.count()


def f32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


class FakePort:
    def __init__(self):
        self.kinds = {key: "revolute" for key in [*ARM_JOINTS, OUTER, INNER]}
        self.kinds |= {key: "prismatic" for key in [*FINGERS, RAIL_JOINT, DRAWER]}
        self.bounds = {key: (-3., 3.) for key in ARM_JOINTS}
        self.bounds |= {key: (0., f32(.04)) for key in FINGERS}
        self.bounds |= {RAIL_JOINT: (0., f32(2.95)), OUTER: (0., 2.), INNER: (0., 2.), DRAWER: (0., f32(.19))}
        self.q, self.qd = dict.fromkeys(self.kinds, 0.), dict.fromkeys(self.kinds, 0.)
        self.q[RAIL_JOINT] = 1.2
        self.targets, self.velocity_targets = {}, {}

    def bind_joint(self, key):
        if key not in self.kinds:
            raise KeyError(key)
        return key

    def joint_metadata(self, key):
        return MappingProxyType({"kind": self.kinds[key], "qWidth": 1, "qdWidth": 1, "targetWidth": 1,
                                 "lower": self.bounds[key][0], "upper": self.bounds[key][1],
                                 "targetDtype": "float32"})

    def joint_q(self, key):
        return [self.q[key]]

    def joint_qd(self, key):
        return [self.qd[key]]

    def set_joint_target(self, key, value):
        self.targets[key] = [f32(value[0])]

    def set_joint_target_velocity(self, key, value):
        self.velocity_targets[key] = list(value)


def configs():
    return {ARM: {"jointKeys": ARM_JOINTS, "fingerKeys": FINGERS, "homePositions": [0.] * 7},
            RAIL: {"jointKey": RAIL_JOINT},
            DOORS: {"outerJoint": OUTER, "innerJoint": INNER, "outerOpenAngle": 1.5, "innerOpenAngle": 1.3},
            IMAGER: {"drawerJoint": DRAWER}}


def make_host(tmp_path, *, port=None, config_changes=None):
    values = configs()
    for key, change in (config_changes or {}).items():
        values[key] |= change
    bindings = [{"key": key, "type": program.type, "program": program.id, "config": values[key]}
                for key, program in zip((ARM, RAIL, DOORS, IMAGER), PROGRAMS, strict=True)]
    assert_disjoint_joint_owners({binding["key"]: REGISTRY[binding["program"]].owned_joint_keys(
        REGISTRY[binding["program"]].Config.model_validate(binding["config"])) for binding in bindings})
    scene = tmp_path / "devices.yaml"
    scene.write_text(yaml.safe_dump({"scene": "basic-drives", "entities": bindings}), encoding="utf-8")
    port = port or FakePort()
    manifest = SceneManifest.model_validate({
        "sessionId": "basic", "sceneRevision": "basic-rev", "revisionNumericId": 1, "sceneName": "basic",
        "source": {"kind": "newton", "physicsDt": .0005},
        "nodes": [{"kind": "body", "entityKey": key, "name": key, "streamId": index + 1}
                  for index, key in enumerate((ARM, RAIL, DOORS, IMAGER))],
        "joints": [{"entityKey": key, "type": kind, "childBodyKey": ARM, "dofCount": 1,
                    "axis": [1, 0, 0], "parentLocalPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]},
                    "anchorSiteKey": key + "/Anchor"} for key, kind in FakePort().kinds.items()],
        "sites": [{"entityKey": key + "/Anchor", "role": "joint-anchor", "parentBodyKey": ARM,
                   "jointKey": key, "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}}
                  for key in FakePort().kinds],
    })
    catalog = load_catalog(manifest, "basic-drives", REGISTRY, scene_file=scene)
    clock = [0.]
    host = HostCore(catalog, REGISTRY, physics=port, publication_clock=lambda: clock[0])

    def tick(dt=.05):
        clock[0] = host.sim_time + dt
        return host.tick(clock[0], dt)

    return host, port, tick


def invoke(host, key, action, params=None, *, epoch=None):
    token = "basic-" + str(next(TOKENS))
    return host.invoke(InvokeRequest(invocationId=token, requestId=token, entityKey=key, action=action,
                                    params=params or {}, epoch=host.epoch if epoch is None else epoch,
                                    createdSimTime=host.sim_time, createdAtMs=0))


def current(host, invocation):
    return next(value for value in host.snapshot().invocations
                if value.invocationId == invocation.invocationId)


def test_arm_commands_only_seven_angles_and_waits_for_actual_settling(tmp_path):
    host, port, tick = make_host(tmp_path)
    port.targets.clear()
    inv = invoke(host, ARM, "moveJoints", {"positions": [1.] * 7, "durationSeconds": .1})
    assert inv.status == "accepted" and not port.targets
    tick(.2)
    assert set(port.targets) == set(ARM_JOINTS)
    assert current(host, inv).status == "running"
    assert host.values()[ARM]["jointPositions"] == [0.] * 7
    for key in ARM_JOINTS:
        port.q[key], port.qd[key] = 1., .2
    tick(.1)
    assert current(host, inv).status == "running"
    for key in ARM_JOINTS:
        port.qd[key] = 0.
    tick(.05)
    assert current(host, inv).status == "running"
    tick(.05)
    assert current(host, inv).status == "succeeded"
    assert host.values()[RAIL]["positionM"] == 1.2


def test_wide_opening_uses_offset_actual_feedback_and_float32_endpoint(tmp_path):
    host, port, tick = make_host(tmp_path)
    port.targets.clear()
    inv = invoke(host, ARM, "setGripper", {"openingM": .19, "durationSeconds": .05})
    assert inv.status == "accepted"
    tick(.1)
    assert set(port.targets) == set(FINGERS)
    assert all(port.targets[key] == [f32(.04)] for key in FINGERS)
    assert host.values()[ARM]["openingM"] == .11
    assert current(host, inv).status == "running"
    port.q[FINGERS[0]], port.q[FINGERS[1]] = .01, .03
    tick(.1)
    assert host.values()[ARM]["openingM"] == pytest.approx(.15)
    assert current(host, inv).status == "running"
    for key in FINGERS:
        port.q[key] = f32(.04)
    tick(.1)
    assert current(host, inv).status == "succeeded"


@pytest.mark.parametrize("params", [{"positions": [0.] * 8, "durationSeconds": 1},
                                    {"positions": [4.] * 7, "durationSeconds": 1},
                                    {"positions": [0.] * 7, "durationSeconds": -1}])
def test_invalid_arm_dimensions_limits_or_duration_never_write_targets(tmp_path, params):
    host, port, _ = make_host(tmp_path)
    port.targets.clear()
    inv = invoke(host, ARM, "moveJoints", params)
    assert inv.status == "rejected" and inv.error.code == "invalid_params"
    assert not port.targets


def test_rail_is_a_distinct_meter_drive_and_busy_is_real_host_admission(tmp_path):
    host, port, tick = make_host(tmp_path)
    port.targets.clear()
    inv = invoke(host, RAIL, "moveTo", {"positionM": 2., "durationSeconds": .1})
    assert inv.status == "accepted"
    busy = invoke(host, RAIL, "home")
    assert busy.status == "rejected" and busy.error.code == "busy"
    tick(.2)
    assert set(port.targets) == {RAIL_JOINT} and current(host, inv).status == "running"
    port.q[RAIL_JOINT] = 2.
    tick(.1)
    assert current(host, inv).status == "succeeded"
    bad = invoke(host, RAIL, "moveTo", {"positionM": 3., "durationSeconds": 1})
    assert bad.error.code == "invalid_params"


def test_stop_and_cancel_wait_for_measured_rest_without_replaying_motion(tmp_path):
    host, port, tick = make_host(tmp_path)
    inv = invoke(host, ARM, "moveJoints", {"positions": [1.] * 7, "durationSeconds": 1})
    tick(.1)
    for key in ARM_JOINTS:
        port.q[key], port.qd[key] = .2, .4
    stop = invoke(host, ARM, "stop")
    assert stop.status == "accepted"
    tick(.1)
    assert current(host, stop).status == current(host, inv).status == "running"
    assert all(port.targets[key] == [f32(.2)] for key in ARM_JOINTS)
    for key in ARM_JOINTS:
        port.qd[key] = 0.
    tick(.1)
    assert current(host, stop).status == "succeeded" and current(host, inv).status == "cancelled"
    next_inv = invoke(host, ARM, "home", {"durationSeconds": 1})
    tick(.1)
    host.cancel(next_inv.invocationId)
    tick(.1)
    assert current(host, next_inv).status == "cancelled"
    assert all(port.targets[key] == [f32(.2)] for key in ARM_JOINTS)


def test_reset_holds_new_measured_state_and_old_epoch_cannot_resume(tmp_path):
    host, port, tick = make_host(tmp_path)
    inv = invoke(host, RAIL, "moveTo", {"positionM": 2., "durationSeconds": 1})
    tick(.1)
    port.q[RAIL_JOINT] = .7
    host.reset(1)
    assert current(host, inv).error.code == "session_reset"
    assert port.targets[RAIL_JOINT] == [f32(.7)]
    tick(.1)
    assert port.targets[RAIL_JOINT] == [f32(.7)]
    assert invoke(host, RAIL, "home", epoch=0).error.code == "session_reset"


def test_repeated_cancel_keeps_the_original_hold_and_settling_window(tmp_path):
    host, port, tick = make_host(tmp_path)
    inv = invoke(host, RAIL, "moveTo", {"positionM": 2., "durationSeconds": 1})
    tick(.1)
    host.cancel(inv.invocationId)
    tick(.05)
    assert current(host, inv).status == "running"
    host.cancel(inv.invocationId)
    tick(.05)
    assert current(host, inv).status == "cancelled"
    assert port.targets[RAIL_JOINT] == [f32(1.2)]


def test_timeout_fault_holds_measured_pose_and_stop_recovers(tmp_path):
    host, port, tick = make_host(tmp_path, config_changes={RAIL: {"settleTimeoutSeconds": .2}})
    inv = invoke(host, RAIL, "moveTo", {"positionM": 2., "durationSeconds": .1})
    tick(.4)
    assert current(host, inv).error.code == "motion_timeout"
    assert host.values()[RAIL]["phase"] == "fault"
    assert port.targets[RAIL_JOINT] == [f32(1.2)]
    stop = invoke(host, RAIL, "stop")
    tick(.1)
    assert current(host, stop).status == "succeeded" and host.values()[RAIL]["phase"] == "idle"


def test_door_interlocks_use_actual_feedback_again_at_start_and_during_motion(tmp_path):
    host, port, tick = make_host(tmp_path)
    assert invoke(host, DOORS, "openInner").error.code == "precondition_failed"
    outer = invoke(host, DOORS, "openOuter", {"durationSeconds": .1})
    tick(.2)
    assert not host.values()[DOORS]["outerOpen"] and current(host, outer).status == "running"
    port.q[OUTER] = 1.5
    tick(.1)
    assert current(host, outer).status == "succeeded"
    inner = invoke(host, DOORS, "openInner", {"durationSeconds": .1})
    port.q[OUTER] = 1.
    port.targets.clear()
    tick(.05)
    assert current(host, inner).error.code == "precondition_failed" and not port.targets
    port.q[OUTER] = 1.5
    tick(.1)
    inner = invoke(host, DOORS, "openInner", {"durationSeconds": .1})
    tick(.05)
    port.q[OUTER], port.q[INNER] = 1., .2
    tick(.05)
    assert current(host, inner).error.code == "precondition_failed"
    assert host.values()[DOORS]["phase"] == "fault"
    assert port.targets[OUTER] == [1.] and port.targets[INNER] == [f32(.2)]


def test_drawer_and_catalog_do_not_invent_occupancy_or_observation(tmp_path):
    host, port, tick = make_host(tmp_path)
    inv = invoke(host, IMAGER, "extend", {"durationSeconds": .1})
    tick(.2)
    assert not host.values()[IMAGER]["extended"]
    port.q[DRAWER] = f32(.19)
    tick(.1)
    assert current(host, inv).status == "succeeded" and host.values()[IMAGER]["extended"]
    assert invoke(host, IMAGER, "observe").error.code == "unknown_action"
    assert invoke(host, ARM, "pick").error.code == "unknown_action"
    assert not {"holding", "occupancy", "cellCount"} & host.values()[ARM].keys()
    assert not {"holding", "occupancy", "cellCount"} & host.values()[IMAGER].keys()


def test_actual_joint_kind_is_checked_without_forward_kinematics(tmp_path):
    port = FakePort()
    port.kinds[ARM_JOINTS[0]] = "prismatic"
    host, port, _ = make_host(tmp_path, port=port)
    assert host.values()[ARM]["phase"] == "fault"
    assert not set(ARM_JOINTS) & port.targets.keys()
    rejected = invoke(host, ARM, "stop")
    assert rejected.error.code == "source_unavailable"
    assert "revolute" in rejected.error.details["reason"]


def test_config_duplicate_ownership_and_missing_spatial_reference_are_rejected(tmp_path):
    with pytest.raises(ValidationError, match="distinct"):
        PandaWideProgram.Config.model_validate(configs()[ARM] | {"fingerKeys": [FINGERS[0]] * 2})
    with pytest.raises(ValueError, match="both"):
        assert_disjoint_joint_owners({ARM: [DRAWER], IMAGER: [DRAWER]})
    host, port, _ = make_host(tmp_path, config_changes={RAIL: {"jointKey": "/World/Lab/missing"}})
    assert host.values()[RAIL]["phase"] == "fault"
    rejected = invoke(host, RAIL, "stop")
    assert rejected.error.code == "source_unavailable" and "missing" in rejected.error.details["reason"]
    assert "/World/Lab/missing" not in port.targets


def test_new_registry_definitions_preserve_legacy_width_and_remain_engine_free():
    schemas = program_schemas()
    assert all(program.id in schemas and schemas[program.id]["type"] == program.type for program in PROGRAMS)
    old = yaml.safe_load((ROOT / "devices/types/robot-arm.v1.yaml").read_text(encoding="utf-8"))
    assert old["properties"]["gripperWidth"]["schema"]["maximum"] == .08
    assert old["actions"]["setGripper"]["params"]["width"]["maximum"] == .08
    result = subprocess.run([sys.executable, "-B", "-c", (
        "from apps.api.app.library_validation import program_schemas; import sys; "
        "program_schemas(); assert not {'newton','warp'} & set(sys.modules)"
    )], cwd=ROOT, env=os.environ | {"PYTHONPATH": str(ROOT)}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_public_structural_validator_rejects_missing_new_program_joint_reference(tmp_path):
    from pxr import Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateNew(str(tmp_path / "stage.usda"))
    root = UsdGeom.Xform.Define(stage, "/World/Lab")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.Xform.Define(stage, RAIL)
    UsdPhysics.PrismaticJoint.Define(stage, RAIL_JOINT)
    stage.GetRootLayer().Save()
    definition = yaml.safe_load((ROOT / "devices/types/linear-axis.v1.yaml").read_text(encoding="utf-8"))
    report = validate({
        "kind": "scene", "subject": "scene:drive-reference@1", "metadata": {
            "stage": "stage.usda", "sources": ["newton"]},
        "package": {"path": str(tmp_path), "files": {"stage.usda": str(tmp_path / "stage.usda")}},
        "assets": [], "programs": program_schemas(), "types": {definition["type"]: definition},
        "bindings": {"entities": [{"key": RAIL, "type": LinearAxisProgram.type,
                                    "program": LinearAxisProgram.id, "config": {
                                        "jointKey": "/World/Lab/missing"}}]},
    })
    check = next(value for value in report["checks"] if value["id"] == "S5")
    assert check["status"] == "fail" and "missing joint" in check["message"]


async def test_generic_action_pipeline_retains_actual_feedback_and_actor(tmp_path):
    host, port, tick = make_host(tmp_path)
    store = StateStore(host.catalog.sceneRevision)
    store.apply(host.snapshot())

    class LocalSource:
        availability = host.availability

        async def invoke(self, request):
            return host.invoke(request)

        async def cancel(self, invocation_id):
            return host.cancel(invocation_id)

    target = SimpleNamespace(
        state="RUNNING", session_id="basic-pipeline", source=LocalSource(), sim_time=0., epoch=0,
        supports_plans=False, options=SessionOptions(),
        approvals=SimpleNamespace(for_invocation=lambda _: None),
        plans=SimpleNamespace(invocation=lambda *_: None),
    )
    pipeline = ActionPipeline(target, host.catalog, store, lambda _: None)
    actor = Actor(kind="agent", name="basic-drive-test")
    command = ActionRequest(requestId="opening", entityKey=ARM, action="setGripper",
                            params={"openingM": .19, "durationSeconds": .05})

    def forward(events):
        target.sim_time = host.sim_time
        for event in events:
            assert event.type != "server.error", event
            if event.type == "invocation.update":
                pipeline.update(event)
            elif event.type in ("entity.snapshot", "entity.observations"):
                store.apply(event)

    try:
        accepted = await pipeline.invoke(command, "operator", actor)
        assert accepted.status == "accepted" and accepted.actor == actor
        forward(tick(.1))
        assert pipeline.table.get(accepted.invocationId).status == "running"
        assert store.values()[ARM]["openingM"] == .11
        for key in FINGERS:
            port.q[key] = f32(.04)
        forward(tick(.1))
        assert pipeline.table.get(accepted.invocationId).status == "succeeded"
        assert store.values()[ARM]["openingM"] == pytest.approx(.19)
    finally:
        pipeline.close()

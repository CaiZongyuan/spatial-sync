"""Actual Newton FK distinguishes a physical TCP from a conditional target frame."""

import copy
import math
from types import SimpleNamespace

import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")

from packages.library.physics import taught_checks

pytestmark = pytest.mark.newton
RAIL, ARM = "/World/Lab/RailJoint", "/World/Lab/ArmJoint"
TCP, STATION = "/World/Lab/Hand/Tcp", "/World/Lab/Station"


def fixture(*, yaw=0.0, tcp_x=0.07, free_context=None, sites=True):
    builder = newton.ModelBuilder(up_axis=newton.Axis.Z)
    rail, hand = builder.add_link(label="/World/Lab/Rail"), builder.add_link(label="/World/Lab/Hand")
    first = builder.add_joint_prismatic(-1, rail, axis=(1, 0, 0), limit_lower=-0.5,
                                       limit_upper=0.5, label=RAIL)
    second = builder.add_joint_prismatic(
        rail, hand, axis=(0, 0, 1), limit_lower=-0.1, limit_upper=0.1, label=ARM,
        parent_xform=wp.transform((0, 0, 0), wp.quat_from_axis_angle(wp.vec3(0, 0, 1), yaw)),
    )
    builder.add_articulation([first, second], label="/World/Lab/Chain")
    if free_context:
        body = builder.add_link(label="/World/Lab/Free")
        joint = builder.add_joint_free(body, label="/World/Lab/FreeJoint")
        if free_context == "articulated":
            builder.add_articulation([joint], label="/World/Lab/FreeChain")
    if sites:
        builder.add_site(hand, xform=wp.transform((tcp_x, 0, 0.075), wp.quat_identity()), label=TCP)
        builder.add_site(-1, xform=wp.transform(
            (-0.45, 0, 0), wp.quat_from_axis_angle(wp.vec3(0, 0, 1), yaw)), label=STATION)
    model = builder.finalize(device="cpu")
    config = {
        "jointKeys": [ARM], "tcpSite": TCP,
        "locations": {"locations": {"pick": {"site": STATION, "grasp": [0]}}},
        "contexts": {"pick": {"site": STATION, "offsetM": [0.07, 0, 0.075],
                              "joints": [{"joint": RAIL, "position": -0.45}]}},
    }
    fields = {"jointKeys": {"posture": True}, "tcpSite": {"spatialReference": "site"},
              "locations": {"documentSchema": "locations"}, "contexts": {"postureContext": True}}
    binding = {"key": "/World/Lab/Arm", "program": "fixture", "config": config}
    payload = {"structure": {"effectiveBindings": {"newton": [binding]}},
               "programs": {"fixture": {"fields": fields, "configSchema": {}}}}
    return SimpleNamespace(model=model, builder=builder), payload


def test_real_rail_fk_and_local_offset_repair_the_525mm_false_negative():
    sim, payload = fixture()
    legacy = copy.deepcopy(payload)
    del legacy["programs"]["fixture"]["fields"]["contexts"]
    before = taught_checks(sim, legacy)[0]
    assert before["status"] == "fail"
    assert before["measurements"]["errorM"] == pytest.approx(math.hypot(0.52, 0.075), abs=1e-6)
    after = taught_checks(sim, payload)[0]
    assert after["status"] == "pass"
    assert after["measurements"]["physicalTcpWorld"] == pytest.approx([-0.38, 0, 0.075], abs=1e-6)
    assert after["measurements"]["postureContext"]["joints"] == [{"joint": RAIL, "position": -0.45}]


def test_context_offset_uses_the_actual_site_quaternion():
    sim, payload = fixture(yaw=math.pi / 2)
    row = taught_checks(sim, payload)[0]
    assert row["status"] == "pass"
    assert row["measurements"]["graspTargetWorld"] == pytest.approx([-0.45, 0.07, 0.075], abs=1e-6)


@pytest.mark.parametrize("change", ["missing", "unknown_role", "missing_site", "missing_joint",
                                   "ambiguous_role", "ambiguous_site", "ambiguous_joint",
                                   "duplicate_joint", "overlap", "limit", "nonfinite", "moved_tcp",
                                   "nonzero_height"])
def test_context_never_masks_invalid_frames_or_external_joint_stances(change):
    sim, payload = fixture(tcp_x=0.10 if change == "moved_tcp" else 0.07)
    fields = payload["programs"]["fixture"]["fields"]
    config = payload["structure"]["effectiveBindings"]["newton"][0]["config"]
    context = config["contexts"]["pick"]
    if change == "missing":
        config["contexts"] = {}
    elif change == "unknown_role":
        fields["contexts"]["postureContext"] = "maybe"
    elif change == "missing_site":
        context["site"] += "Missing"
    elif change == "missing_joint":
        context["joints"][0]["joint"] += "Missing"
    elif change == "ambiguous_role":
        fields["anotherContext"] = {"postureContext": True}
        config["anotherContext"] = copy.deepcopy(config["contexts"])
    elif change == "ambiguous_site":
        sim.model.shape_label[0] = STATION
    elif change == "ambiguous_joint":
        sim.model.joint_label[1] = RAIL
    elif change == "duplicate_joint":
        context["joints"] *= 2
    elif change == "overlap":
        context["joints"][0]["joint"] = ARM
    elif change == "limit":
        context["joints"][0]["position"] = 0.6
    elif change == "nonfinite":
        context["offsetM"][0] = float("nan")
    elif change == "nonzero_height":
        fields["tcpSite"]["postureEndpoint"] = {"offsetField": "graspHeightM", "offsetAxis": "worldZ"}
        payload["programs"]["fixture"]["configSchema"] = {
            "properties": {"graspHeightM": {"type": "number", "minimum": 0}}
        }
        config["graspHeightM"] = 0.08
    row = taught_checks(sim, payload)[0]
    assert row["status"] == "fail"
    if change in ("ambiguous_site", "ambiguous_joint"):
        assert "ambiguous" in row["message"]


@pytest.mark.parametrize("free_context", ["articulated", "outside_fk"])
def test_extra_joint_must_be_an_actual_scalar_fk_coordinate(free_context):
    sim, payload = fixture(free_context=free_context)
    config = payload["structure"]["effectiveBindings"]["newton"][0]["config"]
    config["contexts"]["pick"]["joints"] = [{"joint": "/World/Lab/FreeJoint", "position": 0}]
    row = taught_checks(sim, payload)[0]
    assert row["status"] == "fail"
    assert ("scalar" if free_context == "articulated" else "outside Newton") in row["message"]


def test_context_station_is_materialized_from_its_usd_frame_separately_from_the_runtime_site():
    from pxr import Sdf, Usd, UsdGeom

    from packages.sim_runtime.library_scene import add_taught_sites

    sim, payload = fixture(sites=False)
    config = payload["structure"]["effectiveBindings"]["newton"][0]["config"]
    runtime = "/World/Lab/RuntimeGoal"
    config["locations"]["locations"]["pick"]["site"] = runtime
    stage = Usd.Stage.CreateInMemory()
    for key, position in ((TCP, (0.07, 0, 0.075)), (STATION, (-0.45, 0, 0)), (runtime, (0, 2, 0))):
        UsdGeom.Xform.Define(stage, key).AddTranslateOp().Set(position)
    hand = sim.builder.body_label.index("/World/Lab/Hand")
    paths = {"path_shape_map": {}, "path_body_map": {Sdf.Path("/World/Lab/Hand"): hand}}
    add_taught_sites(sim.builder, stage, paths, payload)
    sim.model = sim.builder.finalize(device="cpu")
    assert {TCP, STATION, runtime}.issubset(sim.model.shape_label)
    assert taught_checks(sim, payload)[0]["status"] == "pass"


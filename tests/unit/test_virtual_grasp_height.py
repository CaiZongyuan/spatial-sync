"""Exercise nonzero Virtual facts through pure Program contexts, no Source."""

import math
from types import SimpleNamespace

import pytest

from packages.programs.robot_arm.virtual import HOME, RobotArmProgram
from packages.sim_runtime.synthetic_geometry import world_site_pose, world_site_poses

# Independently calculated from the documented two-link HOME geometry.
HAND = [0.02955202066613396, 0, 1.9508701380381665]
BOTTOM = [HAND[0], 0, HAND[2] - 0.020]
SITE = "/World/Lab/Rack/A1"
TUBE = "/World/Lab/Tubes/T1"


class Context:
    def __init__(self, height, bottom=BOTTOM):
        location = {"site": SITE, "zone": "rack", "p": list(bottom), "grasp": HOME, "approach": HOME}
        self.config = RobotArmProgram.Config.model_validate(
            {"graspHeightM": height, "tubes": [TUBE], "locations": {"locations": {"rack.A1": location}}}
        )
        self.physics = SimpleNamespace(site_pose=lambda key: (bottom, [0, 0, 0, 1]),
                                       tube_height=lambda key: .08)
        self.values = {}

    def set(self, **values):
        self.values.update(values)

    def get(self, name):
        return self.values.get(name)

    def read(self, entity, name):
        return SITE if (entity, name) == (TUBE, "location") else None


def finish_segment(program, ctx, kind):
    failures = []
    program.handle = SimpleNamespace(
        fail=lambda code, message, **details: failures.append(code), progress=lambda value: None
    )
    program.segment = kind, None, "rack.A1"
    program.elapsed, program.duration = 0, 0.2
    program.completed, program.total_segments = 0, 1
    program.tick(ctx, 0.2)
    return failures


def test_nonzero_pick_uses_actual_kinematic_tcp_and_preserves_its_pose():
    ctx, program = Context(0.020), RobotArmProgram()
    program.init(ctx)
    before = list(ctx.values["tcpPose"])
    assert before[:3] == pytest.approx(HAND)
    assert not finish_segment(program, ctx, "pick")
    assert ctx.values["holding"] == TUBE
    assert ctx.values["tcpPose"] == before


def test_wrong_grasp_does_not_write_holding_to_make_geometry_match():
    ctx, program = Context(0.020, [HAND[0], 0, BOTTOM[2] - 0.080]), RobotArmProgram()
    program.init(ctx)
    assert finish_segment(program, ctx, "pick") == ["grasp_failed"]
    assert ctx.values["holding"] is None
    assert ctx.values["tcpPose"][:3] == pytest.approx(HAND)


def test_wrong_release_keeps_the_held_tube_instead_of_trusting_location_name():
    ctx, program = Context(0.020, [BOTTOM[0] + 0.040, 0, BOTTOM[2]]), RobotArmProgram()
    program.init(ctx)
    ctx.set(holding=TUBE, atLocation="rack.A1")
    assert finish_segment(program, ctx, "place") == ["place_failed"]
    assert ctx.values["holding"] == TUBE


def test_zero_retains_legacy_virtual_behavior_without_a_geometry_provider():
    ctx, program = Context(0, [3, 4, 5]), RobotArmProgram()
    ctx.physics = None
    program.init(ctx)
    assert not finish_segment(program, ctx, "pick")
    assert ctx.values["holding"] == TUBE


def test_nonzero_without_actual_site_geometry_cannot_initialize_as_supported():
    ctx = Context(0.020)
    ctx.physics = None
    with pytest.raises(ValueError, match="actual Synthetic"):
        RobotArmProgram().init(ctx)


def test_nonzero_height_outside_actual_synthetic_tube_cannot_initialize():
    ctx = Context(.081)
    with pytest.raises(ValueError, match="exceeds actual Synthetic tube height"):
        RobotArmProgram().init(ctx)


def test_site_world_pose_uses_actual_parent_and_missing_parent_is_not_identity():
    local = {"p": [0.1, 0, 0.1], "q": [0, 0, 0, 1]}
    site = SimpleNamespace(
        entityKey=SITE, parentBodyKey="rotor", localPose=SimpleNamespace(model_dump=lambda: local)
    )
    rotor = {"p": [1, 2, 3], "q": [0, 0, math.sqrt(0.5), math.sqrt(0.5)]}
    assert world_site_pose(site, {"rotor": rotor})["p"] == pytest.approx([1, 2.1, 3.1])
    with pytest.raises(ValueError, match="parent pose"):
        world_site_pose(site, {})
    with pytest.raises(ValueError, match="ambiguous"):
        world_site_poses([site, site], {"rotor": rotor})

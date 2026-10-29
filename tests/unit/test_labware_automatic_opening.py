"""A bounded automatic opening also applies to release and failed-pick recovery."""

from types import SimpleNamespace

import pytest

from packages.programs.labware.motion import LabwareMotionProgram
from packages.programs.robot_arm.newton import RobotArmProgram
from tests.unit.test_info_newton_programs import ArmContext, stalled_gripper
from tests.unit.test_labware_occupancy import configured_context


def test_scene_calibration_sets_automatic_opening_and_legacy_default_stays_80mm():
    ctx, _ = configured_context()
    assert ctx.config.automaticOpenWidthM == .08
    ctx.config = LabwareMotionProgram.Config.model_validate(
        ctx.config.model_dump() | {"automaticOpenWidthM": .05, "gripperSpeedMPerS": .02})
    program = LabwareMotionProgram()
    program.init(ctx)
    assert program.gripper_open_width_m == .05
    assert program.gripper_speed_mps == .02
    assert RobotArmProgram.gripper_open_width_m == .08


@pytest.mark.parametrize("width", [0, .081, float("nan"), float("inf")])
def test_automatic_opening_must_fit_the_physical_hand(width):
    ctx, _ = configured_context()
    with pytest.raises(ValueError):
        LabwareMotionProgram.Config.model_validate(ctx.config.model_dump() | {"automaticOpenWidthM": width})


@pytest.mark.parametrize("diameter", [.05, .06])
def test_configured_automatic_opening_must_clear_every_grip(diameter):
    ctx, _ = configured_context()
    with pytest.raises(ValueError, match="configured automatic opening"):
        LabwareMotionProgram.Config.model_validate(ctx.config.model_dump() | {
            "automaticOpenWidthM": .05, "gripDiameters": {"held": .017, "occupant": diameter}})


def test_pick_approach_uses_calibrated_opening_without_changing_explicit_set_gripper():
    ctx, program = ArmContext(), RobotArmProgram()
    program.gripper_open_width_m = .05
    ctx.config.locations = {"locations": {"rack": {
        "site": "site", "zone": "rack", "approach": [0.] * 7, "grasp": [0.] * 7}}}
    ctx.read = lambda key, prop: "site"
    program.init(ctx)
    program.start(SimpleNamespace(action="pick", params={"location": "rack"}), ctx)
    assert next(target for kind, target, _ in program.plan if kind == "gripper") == .05
    program.start(SimpleNamespace(action="setGripper", params={"width": .08}, progress=lambda _: None), ctx)
    program.tick(ctx, 1 / 60)
    assert program.gripper_goal == .08


def test_place_caps_opening_and_waits_for_actual_release_before_unwelding():
    program, ctx, _ = stalled_gripper("place")
    program.elapsed = 0
    program.gripper_open_width_m = .05
    program.pending_release = {1}
    ctx.physics.width = .016
    program.tick(ctx, 1 / 60)
    assert program.gripper_goal == .05
    assert ctx.physics.enabled and program.pending_release == {1}
    ctx.physics.width = .05
    program.tick(ctx, 1 / 60)
    assert not ctx.physics.enabled and not program.pending_release


def test_failed_pick_recovery_uses_same_calibrated_opening_and_keeps_weld_until_feedback():
    program, ctx, _ = stalled_gripper("pick")
    program.gripper_open_width_m = .05
    program._recover_pick(ctx, "rack.A1")
    assert program.gripper_goal == .05
    assert ctx.physics.enabled and program.pending_release == {1}
    ctx.physics.width = .05
    program._drive_gripper(ctx, 1 / 60)
    assert not ctx.physics.enabled and not program.pending_release


def test_slow_automatic_motion_is_not_stationary_and_pick_requires_an_actual_weld():
    ctx, _ = configured_context()
    program = LabwareMotionProgram()
    program.init(ctx)
    program.handle = SimpleNamespace(action="pick")
    program.pick_tube = "held"
    program.last_width = .05
    program.gripper_goal = 0
    assert not program._gripper_settled(ctx, "pick", .05 - .02 / 60, 1 / 60)
    program.last_width = .017
    ctx.physics.welds.active = lambda hand, body: False
    assert not program._gripper_settled(ctx, "pick", .017, 1 / 60)
    ctx.physics.welds.active = lambda hand, body: True
    assert program._gripper_settled(ctx, "pick", .017, 1 / 60)


def test_automatic_release_waits_for_its_opening_but_explicit_gripper_keeps_legacy_completion():
    ctx, _ = configured_context()
    program = LabwareMotionProgram()
    program.init(ctx)
    program.handle = SimpleNamespace(action="place")
    program.last_width = .03
    program.gripper_goal = .05
    assert not program._gripper_settled(ctx, "place", .03, 1 / 60)
    program.last_width = .05
    assert program._gripper_settled(ctx, "place", .05, 1 / 60)
    program.handle = SimpleNamespace(action="setGripper")
    program.last_width = .03
    assert program._gripper_settled(ctx, "gripper", .03, 1 / 60)

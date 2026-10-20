"""Independent FK geometry and the actual virtual execution share planned motion."""

import math
from copy import deepcopy
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pytest

from packages.info.preview import PreviewContext
from packages.programs.robot_arm.preview import joint_targets, path_preview, sample_joint_path
from packages.programs.robot_arm.virtual import HOME, VELOCITY_LIMITS, RobotArmProgram, kinematics


class Context:
    def __init__(self, locations=None):
        self.config = RobotArmProgram.Config(locations={"locations": locations or {}}, tubes=["/tube"])
        self.values = {}

    def set(self, **values):
        self.values.update(deepcopy(values))

    def get(self, name):
        return deepcopy(self.values[name])

    def read(self, key, prop):
        return "/rack" if (key, prop) == ("/tube", "location") else None

    def check(self, _conditions):
        return []

    def readonly(self):
        return PreviewContext(self.config.model_copy(deep=True), MappingProxyType(deepcopy(self.values)),
                              None, lambda _key, build: build())


def invocation(action, **params):
    result = SimpleNamespace(action=action, params=params, succeeded=False, was_cancelled=False)
    result.progress = lambda _fraction: None
    result.succeed = lambda: setattr(result, "succeeded", True)
    result.cancelled = lambda: setattr(result, "was_cancelled", True)
    return result


def test_virtual_forward_kinematics_matches_independent_axis_rotations():
    first, second, hand = kinematics([0] * 7)
    assert first["p"] == pytest.approx([0, 0, .9])
    assert second["p"] == pytest.approx([0, 0, 1.5])
    assert hand == {"p": [0, 0, 2.0], "q": [0, 0, 0, 1]}
    _, _, rotated = kinematics([math.pi / 2, math.pi / 2, 0, 0, 0, 0, 0])
    assert rotated["p"] == pytest.approx([0, 1.1, .9], abs=1e-12)
    assert rotated["q"] == pytest.approx([-.5, .5, .5, .5])
    wrist = kinematics([0, 0, 0, math.pi, 0, 0, 0])[2]
    assert wrist["p"] == [0, 0, 2.0] and wrist["q"] == pytest.approx([1, 0, 0, 0], abs=1e-12)


def test_mixed_triangle_trapezoid_samples_match_actual_virtual_tick_and_fk():
    program, ctx = RobotArmProgram(), Context()
    program.init(ctx)
    target = list(HOME)
    target[0], target[1] = 1.0, HOME[1] + .25
    preview = path_preview(HOME, [target], VELOCITY_LIMITS, lambda q: kinematics(q)[2], max_points=7)
    inv = invocation("moveJoints", positions=target)
    program.start(inv, ctx)
    for index in range(1, 7):
        program.tick(ctx, .25)
        assert ctx.get("tcpPose")[:3] == pytest.approx(preview.points[index])
        if index == 1:
            # The short axis is triangular, the long axis trapezoidal. Their
            # normalized progress differs; ordinary linear q interpolation fails.
            assert ctx.get("jointPositions")[:2] == pytest.approx([.0625, HOME[1] + 1 / 72])
    assert ctx.get("jointPositions") == pytest.approx(target)
    assert not inv.succeeded
    program.tick(ctx, .05)
    assert inv.succeeded and ctx.get("phase") == "idle"


def test_taught_multiple_segments_preserve_every_boundary_and_bounded_samples():
    start = [0, 0]
    targets = [[1, .25], [.2, 2], [1, .25], [3, 2], [3, 3], [3, 2]]
    samples = sample_joint_path(start, targets, [1, 1])
    assert len(samples) == 64
    assert samples[0].joints.tolist() == start
    for segment, target in enumerate(targets):
        final = [sample for sample in samples if sample.segment == segment][-1]
        assert final.joints.tolist() == target
    value = path_preview(start, targets, [1, 1], lambda q: {"p": [q[0], q[1], 4], "q": [0, 0, 0, 1]})
    assert value.points[-1] == (3, 2, 4)
    assert value.targetJointPositions == targets[-1]
    with pytest.raises(ValueError, match="boundary"):
        sample_joint_path(start, targets, [1, 1], max_points=6)
    with pytest.raises(ValueError):
        sample_joint_path(start, targets, [1, 1], max_points=65)


def test_zero_distance_holds_existing_minimum_and_does_not_create_a_fake_path():
    program, ctx = RobotArmProgram(), Context()
    program.init(ctx)
    value = program.preview("home", {}, ctx.readonly())
    assert len(value.points) == 1
    assert value.targetJointPositions == HOME
    inv = invocation("home")
    program.start(inv, ctx)
    for _ in range(2):
        program.tick(ctx, .1)
        assert ctx.get("jointPositions") == HOME and not inv.succeeded
    program.tick(ctx, .1)
    assert inv.succeeded and ctx.get("phase") == "idle"


@pytest.mark.parametrize("speed", [0, 1.1, float("inf"), float("nan")])
def test_invalid_speed_does_not_change_the_preview_source_values(speed):
    program, ctx = RobotArmProgram(), Context()
    program.init(ctx)
    before = deepcopy(ctx.values)
    with pytest.raises(ValueError):
        program.preview("moveJoints", {"positions": [0] * 7, "speed": speed}, ctx.readonly())
    assert ctx.values == before


def test_virtual_cancel_holds_actual_current_coordinates_under_new_time_profile():
    program, ctx = RobotArmProgram(), Context()
    program.init(ctx)
    inv = invocation("moveJoints", positions=[1] * 7)
    program.start(inv, ctx)
    program.tick(ctx, .1)
    current = ctx.get("jointPositions")
    assert current != HOME
    program.cancel(inv, ctx)
    program.tick(ctx, .1)
    assert inv.was_cancelled and not inv.succeeded
    assert ctx.get("jointPositions") == current and ctx.get("phase") == "idle"


def test_actual_pick_place_move_order_and_terminal_facts_match_taught_path():
    def location(value, site):
        return {"approach": [value] * 7, "grasp": [value + .1] * 7, "site": site, "zone": site}

    locations = {"from": location(.2, "/rack"), "to": location(.5, "/destination")}
    program, ctx = RobotArmProgram(), Context(locations)
    program.init(ctx)
    targets = joint_targets("pickPlace", {"from": "from", "to": "to"}, locations, HOME)
    inv = invocation("pickPlace", **{"from": "from", "to": "to"})
    program.start(inv, ctx)
    observed = []
    for _ in range(300):
        starting = program.segment is None
        program.tick(ctx, .1)
        if starting and program.segment is not None and program.segment[0] == "move":
            observed.append(program.segment[1])
        if inv.succeeded:
            break
    assert inv.succeeded and len(observed) == 6
    np.testing.assert_allclose(observed, targets)
    assert ctx.get("jointPositions") == targets[-1]
    assert ctx.get("holding") is None and ctx.get("gripperWidth") == .08
    assert ctx.get("atLocation") == "to" and ctx.get("phase") == "idle"

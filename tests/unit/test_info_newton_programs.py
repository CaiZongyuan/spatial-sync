"""Public Program/PhysicsPort contract checks without importing Newton."""

import math
from types import SimpleNamespace

import numpy as np
import pytest

from packages.programs.robot_arm.newton import RobotArmProgram


class ArmPort:
    def __init__(self):
        self.enabled = True
        self.width = 0.016
        self.body = np.array([[0, 0, 0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0, 1]], dtype=float)
        self.welds = SimpleNamespace(active=lambda hand, tube: self.enabled)
        self.releasing = set()
        self.targets = {}
        self.positions = {}

    def bind_joint(self, key):
        return key

    def bind_body(self, key):
        return 0 if key == "hand" else 1

    def bind_site(self, key):
        return key

    def joint_q(self, joint):
        return np.array([self.width / 2 if joint.startswith("finger") else self.positions.get(joint, 0.0)])

    def joint_qd(self, joint):
        return np.zeros(1)

    def site_pose(self, site):
        return np.zeros(3), np.array([0, 0, 0, 1])

    def set_joint_target(self, joint, value):
        self.targets[joint] = list(value)

    def unweld(self, hand, tube):
        self.enabled = False

    def weld(self, hand, tube):
        self.enabled = True


class ArmContext:
    def __init__(self):
        self.physics = ArmPort()
        self.config = SimpleNamespace(
            jointKeys=[f"joint{i}" for i in range(7)],
            fingerKeys=["finger1", "finger2"],
            handBody="hand",
            tcpSite="tcp",
            tubes=["tube"],
            locations={"locations": {}},
            graspMode="weld",
            speedScale=0.5,
            graspHeightM=0.0,
        )
        self.values, self.fact_due = {}, True

    def set(self, **values):
        self.values.update(values)

    def get(self, name):
        return self.values.get(name)


def test_real_finger_region_checks_rotated_upper_point_without_moving_tcp():
    program, ctx = RobotArmProgram(), ArmContext()
    ctx.config.graspHeightM = 0.1
    ctx.config.graspMode = "contact"
    ctx.physics.body[1, :3] = [0, 0, -0.1]
    program.init(ctx)
    assert program._in_fingers(ctx.physics, 1)
    ctx.physics.body[1, 3:] = [0, math.sin(math.radians(9.5)), 0, math.cos(math.radians(9.5))]
    program._facts(ctx)
    assert program._finger_position(ctx.physics, 1) == pytest.approx(
        [0.03255681544571567, 0, -0.00544814244006832])
    assert not program._in_fingers(ctx.physics, 1)
    assert ctx.values["holding"] is None
    assert ctx.values["tcpPose"] == [0, 0, 0, 0, 0, 0, 1]


def test_release_does_not_ignore_the_rotated_grasp_points_y_offset():
    program, ctx = RobotArmProgram(), ArmContext()
    ctx.config.graspHeightM = 0.1
    ctx.physics.body[1, :3] = [0, 0, -0.1]
    ctx.physics.body[1, 3:] = [math.sin(math.radians(9.5)), 0, 0, math.cos(math.radians(9.5))]
    program.init(ctx)
    program._open(ctx)
    ctx.physics.width = 0.03  # This releases h=0 in the existing test below.
    program._drive_gripper(ctx, 0)
    assert ctx.physics.enabled
    assert program.pending_release == {1}


@pytest.mark.parametrize(
    "requested_width,released,first_finger_target",
    [(0.03, True, 0.011333333333333334), (0.018, True, 0.009), (0.01, False, 0.005)],
)
def test_opening_releases_weld_and_respects_finger_velocity(requested_width, released, first_finger_target):
    program, ctx = RobotArmProgram(), ArmContext()
    program.init(ctx)
    invocation = SimpleNamespace(
        action="setGripper", params={"width": requested_width}, progress=lambda _: None
    )
    program.start(invocation, ctx)
    assert ctx.physics.enabled  # Intake while paused does not execute the opening.
    program.tick(ctx, 1 / 60)
    assert ctx.physics.enabled  # A target alone cannot release physical contact.
    assert ctx.physics.targets["finger1"] == pytest.approx([first_finger_target])
    ctx.physics.width = requested_width
    program.tick(ctx, 1 / 60)
    assert ctx.physics.enabled is not released
    assert not ctx.physics.releasing  # Only a place release suppresses dropped.


def test_stop_holds_measured_joints_even_without_an_active_invocation():
    program, ctx = RobotArmProgram(), ArmContext()
    program.init(ctx)
    ctx.physics.set_joint_target("joint0", [1])
    completed = []
    stop = SimpleNamespace(action="stop", params={}, succeed=lambda: completed.append(True))
    program.start(stop, ctx)
    assert completed == [True]
    assert ctx.physics.targets["joint0"] == [0]
    assert ctx.physics.enabled  # Stop keeps the physically held tube.


def stalled_gripper(kind):
    program, ctx = RobotArmProgram(), ArmContext()
    ctx.config.locations = {"locations": {"rack.A1": {"approach": [0.15] + [0] * 6, "zone": "rack"}}}
    program.init(ctx)
    failures = []
    program.handle = SimpleNamespace(
        action=kind,
        params={},
        progress=lambda _: None,
        fail=lambda code, message, **details: failures.append((code, details)),
    )
    program.segment = kind, 0.08, "rack.A1"
    program.pick_tube = "tube"
    program.elapsed, program.stable, program.last_width = 2.0, 0.0, 0.03
    program.completed, program.total = 0, 1
    return program, ctx, failures


def test_pick_clamp_timeout_opens_and_retreats_to_approach_before_reporting_failure():
    program, ctx, failures = stalled_gripper("pick")
    program.tick(ctx, 1 / 60)
    assert not failures  # Retreat is part of recovery, before terminal failure.
    for _ in range(120):
        program.tick(ctx, 1 / 60)
        ctx.physics.positions.update({k: v[0] for k, v in ctx.physics.targets.items()})
        ctx.physics.width = sum(ctx.physics.targets.get(k, [0])[0] for k in ("finger1", "finger2"))
        if failures:
            break
    assert failures == [("grasp_failed", {})]
    assert ctx.physics.positions["joint0"] == pytest.approx(0.15, abs=0.01)
    assert ctx.physics.width == pytest.approx(0.08)
    assert not ctx.physics.enabled
    assert ctx.values["phase"] == "idle"


@pytest.mark.parametrize("kind,code", [("place", "place_failed"), ("gripper", "motion_timeout")])
def test_gripper_timeout_holds_measured_targets_with_action_specific_error(kind, code):
    program, ctx, failures = stalled_gripper(kind)
    program.tick(ctx, 1 / 60)
    assert failures == [(code, {})]
    program.tick(ctx, 1 / 60)
    assert sum(ctx.physics.targets[k][0] for k in ("finger1", "finger2")) == pytest.approx(0.016)
    assert ctx.physics.enabled  # Holding does not drop a tube on failed opening.


def test_location_requires_failure_preserves_each_failed_condition_before_descent():
    program, ctx, failures = stalled_gripper("check")
    expected = [{"condition": {"property": "lidOpen", "equals": True}, "actual": False}]
    ctx.check = lambda _: expected
    program.tick(ctx, 1 / 60)
    assert failures == [("precondition_failed", {"failed": expected, "location": "rack.A1"})]
    assert "joint0" not in ctx.physics.targets


@pytest.mark.parametrize("parameter", ["from", "to"])
def test_location_intake_refusal_identifies_the_parameter_and_taught_location(parameter):
    program, ctx = RobotArmProgram(), ArmContext()
    program.init(ctx)
    program.locations = {name: {"site": name, "requires": [name]}
                         for name in ("rack.A2", "centrifuge.slot2")}
    params = {"from": "rack.A2", "to": "centrifuge.slot2"}
    expected = [{"actual": "blocked"}]
    ctx.check = lambda conditions: expected if conditions == [params[parameter]] else []
    result = program.accept(SimpleNamespace(action="pickPlace", params=params), ctx)
    assert result.code == "precondition_failed"
    assert result.details == {"parameter": parameter, "location": params[parameter], "failed": expected}


@pytest.mark.parametrize("drifts", [True, False])
def test_place_needs_one_continuous_second_of_physical_seating_not_stale_location(drifts):
    program, ctx = RobotArmProgram(), ArmContext()
    program.init(ctx)
    ctx.fact_due = False
    ctx.read = lambda key, prop: "site"  # Deliberately stale successful location.
    program.locations = {"rack.A1": {"site": "site"}}
    ctx.physics.bind_sites = lambda keys: tuple(keys)
    ctx.physics.site_poses = lambda sites: np.array([[0, 0, 0, 0, 0, 0, 1]])
    ctx.physics.relative_site_velocity = lambda body, sites: np.zeros((1, 3))
    program.segment = "verifyplace", None, "rack.A1"
    program.pick_tube = "tube"
    program.elapsed = program.stable = 0
    program.completed, program.total = 0, 1
    failures = []
    program.handle = SimpleNamespace(action="place", progress=lambda _: None,
                                     succeed=lambda: failures.append("unexpected_success"),
                                     fail=lambda code, message, **details: failures.append(code))
    for _ in range(5):
        ctx.physics.last_time = getattr(ctx.physics, "last_time", 0)+.1
        program.tick(ctx, .1)
    assert program.segment is not None  # One good frame cannot complete a place.
    if drifts:
        angle = math.radians(24)/2
        ctx.physics.body[1, 3:] = [0, math.sin(angle), 0, math.cos(angle)]
        for _ in range(26):
            ctx.physics.last_time += .1
            program.tick(ctx, .1)
        assert failures == ["place_failed"]
    else:
        for _ in range(6):
            ctx.physics.last_time += .1
            program.tick(ctx, .1)
        assert program.segment is None
        assert not failures


def test_place_one_hz_needs_two_distinct_physical_samples_spanning_one_second():
    program, ctx = RobotArmProgram(), ArmContext()
    program.init(ctx)
    ctx.fact_due = False
    program.locations = {"rack.A1": {"site": "site"}}
    ctx.physics.bind_sites = lambda keys: tuple(keys)
    ctx.physics.site_poses = lambda sites: np.array([[0, 0, 0, 0, 0, 0, 1]])
    ctx.physics.relative_site_velocity = lambda body, sites: np.zeros((1, 3))
    ctx.read = lambda key, prop: "site"
    program.pick_tube, program.segment = "tube", ("verifyplace", None, "rack.A1")
    program.elapsed = program.stable = 0
    program.completed, program.total = 0, 1
    program.handle = SimpleNamespace(action="place", progress=lambda _: None)
    ctx.physics.last_time = 1
    program.tick(ctx, 1)
    assert program.segment is not None
    program.tick(ctx, 1)  # Reusing the sample cannot accumulate stability.
    assert program.segment is not None
    ctx.physics.last_time = 2
    program.tick(ctx, 1)
    assert program.segment is None

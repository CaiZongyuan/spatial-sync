"""Public Program/PhysicsPort contract checks without importing Newton."""

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
        )
        self.values, self.fact_due = {}, True

    def set(self, **values):
        self.values.update(values)

    def get(self, name):
        return self.values.get(name)


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
    assert failures == [("precondition_failed", {"failed": expected})]
    assert "joint0" not in ctx.physics.targets

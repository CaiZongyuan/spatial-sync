"""Feedback-checked buffered motion shared by the basic laboratory drives."""

import math
import struct
from dataclasses import dataclass
from numbers import Real

from pydantic import ConfigDict, Field, model_validator

from packages.info.program import ProgramConfig, Reject


class FeedbackUnavailable(ValueError):
    """A complete finite scalar feedback sample could not be obtained."""


class DriveConfig(ProgramConfig):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)
    settleSeconds: float = Field(default=.1, gt=0, le=1, allow_inf_nan=False)
    settleTimeoutSeconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)

    @model_validator(mode="after")
    def settling_window(self):
        if self.settleTimeoutSeconds < self.settleSeconds:
            raise ValueError("Settling deadline must include the stable feedback window")
        return self


def distinct_joints(keys):
    if len(keys) != len(set(keys)) or any(
        not key or not key.isascii() or not key.startswith("/")
        or any(part in ("", ".", "..") for part in key.split("/")[1:])
        or any(character in key for character in "*?[]\\") for key in keys
    ):
        raise ValueError("Owned joints must be distinct exact spatial paths")


def assert_disjoint_joint_owners(owners):
    """A qualified scene assembler calls this; HostCore makes no joint claim."""
    assigned = {}
    for entity, keys in owners.items():
        distinct_joints(keys)
        for key in keys:
            if key in assigned:
                raise ValueError(f"Joint {key} is assigned to both {assigned[key]} and {entity}")
            assigned[key] = entity


@dataclass(frozen=True)
class Axis:
    key: str
    reference: object
    lower: float
    upper: float
    position_tolerance: float
    velocity_tolerance: float
    target_dtype: str

    def target(self, value):
        # Compare the value the actual actuator buffer will store, including
        # float32 limit endpoints such as the source finger's 0.04m travel.
        if self.target_dtype == "float32":
            return struct.unpack("<f", struct.pack("<f", value))[0]
        return float(value)

    def position(self, port):
        return self._read(port, "joint_q")

    def velocity(self, port):
        return self._read(port, "joint_qd")

    def _read(self, port, method):
        try:
            values = getattr(port, method)(self.reference)
            if (len(values) != 1 or isinstance(values[0], bool) or not isinstance(values[0], Real)
                    or not math.isfinite(float(values[0]))):
                raise ValueError("Expected one finite measured coordinate")
            return float(values[0])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise FeedbackUnavailable(f"Actual {method} feedback is unavailable: {self.key}") from error


def bind_axes(port, keys, kind, position_tolerance, velocity_tolerance):
    distinct_joints(keys)
    axes = {}
    for key in keys:
        reference = port.bind_joint(key)
        metadata = port.joint_metadata(reference)
        if metadata["kind"] != kind or any(
            type(metadata[name]) is not int or metadata[name] != 1
            for name in ("qWidth", "qdWidth", "targetWidth")
        ):
            raise ValueError(f"Joint {key} must be a scalar {kind} drive")
        lower, upper = metadata["lower"], metadata["upper"]
        if (type(lower) not in (int, float) or type(upper) not in (int, float)
                or not math.isfinite(lower) or not math.isfinite(upper) or lower > upper):
            raise ValueError("Actual finite joint limits are unavailable: " + key)
        dtype = metadata["targetDtype"]
        if dtype not in ("float32", "float64"):
            raise ValueError("Actual actuator target precision is unsupported: " + key)
        axes[key] = Axis(key, reference, float(lower), float(upper),
                         position_tolerance, velocity_tolerance, dtype)
    return axes


def target_error(axes, targets):
    for key, target in targets.items():
        axis = axes[key]
        try:
            valid = type(target) in (int, float) and math.isfinite(target) and (
                axis.lower <= axis.target(target) <= axis.upper)
        except (OverflowError, struct.error):
            valid = False
        if not valid:
            return Reject(code="invalid_params", message="Target exceeds actual joint limits",
                          details={"joint": key, "lower": axis.lower, "upper": axis.upper})


class JointMotion:
    def __init__(self, ctx, axes):
        self.axes, self.config = axes, ctx.config
        self.handle, self.stop_handle = None, None
        self.phase, self.elapsed, self.stable = "idle", 0., 0.
        self.goals, self.origins = {}, {}
        self.duration = 0.
        self.cancelling, self.stop_cancelled = False, False
        self.feedback, self.known_positions, self.fault_targets = None, {}, None

    def sample(self, ctx):
        positions, velocities, problem = {}, {}, None
        for key, axis in self.axes.items():
            try:
                value = axis.position(ctx.physics)
                if not math.isfinite(axis.target(value)):
                    raise FeedbackUnavailable("Measured position cannot be stored by its actuator: " + key)
                positions[key] = value
                self.known_positions[key] = value
            except FeedbackUnavailable as error:
                problem = problem or error
            except (OverflowError, struct.error):
                problem = problem or FeedbackUnavailable("Measured position cannot be stored: " + key)
            try:
                velocities[key] = axis.velocity(ctx.physics)
            except FeedbackUnavailable as error:
                problem = problem or error
        if problem is not None:
            self.feedback = None
            raise problem
        self.feedback = positions, velocities

    def position(self, key):
        if self.feedback is None:
            raise FeedbackUnavailable("No complete current position/velocity sample")
        return self.feedback[0][key]

    def velocity(self, key):
        if self.feedback is None:
            raise FeedbackUnavailable("No complete current position/velocity sample")
        return self.feedback[1][key]

    def check_targets(self, targets):
        return target_error(self.axes, targets)

    def accept(self, targets):
        if self.phase != "idle":
            return Reject(code="busy", message="Drive is not idle; stop and wait for settled feedback",
                          details={"phase": self.phase})
        return self.check_targets(targets)

    def hold(self, ctx):
        self.goals = {key: self.position(key) for key in self.axes}
        self._write_hold(ctx, self.goals)

    def _write_hold(self, ctx, targets):
        # These are protective commands, never substituted observations. If no
        # finite position has ever been seen for an axis, invent no position.
        for axis in self.axes.values():
            ctx.physics.set_joint_target_velocity(axis.reference, [0.])
        for key, value in targets.items():
            ctx.physics.set_joint_target(self.axes[key].reference, [self.axes[key].target(value)])

    def _write(self, ctx, targets):
        for key, value in targets.items():
            reference = self.axes[key].reference
            ctx.physics.set_joint_target(reference, [float(value)])
            ctx.physics.set_joint_target_velocity(reference, [0.])

    def start(self, inv, ctx, targets, duration):
        error = self.accept(targets)
        if error:
            inv.fail(error.code, error.message, **error.details)
            return
        self.handle = inv
        self.goals = {key: self.axes[key].target(value) for key, value in targets.items()}
        self.origins = {key: self.position(key) for key in targets}
        self.duration, self.elapsed, self.stable = float(duration), 0., 0.
        self.cancelling, self.phase = False, "moving"
        self.fault_targets = None

    def stop(self, inv, ctx):
        if self.stop_handle is not None:
            inv.fail("busy", "A stop is already waiting for settled feedback",
                     activeInvocationId=self.stop_handle.id)
            return
        self.stop_handle, self.stop_cancelled = inv, False
        self._stop(ctx)

    def _stop(self, ctx):
        if self.phase == "stopping":
            return
        self.hold(ctx)
        self.fault_targets = None
        self.elapsed, self.stable, self.duration = 0., 0., 0.
        self.cancelling, self.phase = True, "stopping"

    def cancel(self, inv, ctx):
        if self.stop_handle is not None and self.stop_handle.id == inv.id:
            self.stop_cancelled = True
        self._stop(ctx)

    def fault(self, ctx, code, message):
        if self.phase != "fault":
            self.fault_targets = dict(self.known_positions)
        handles = self.handle, self.stop_handle
        self.handle, self.stop_handle, self.phase = None, None, "fault"
        self.goals, self.origins, self.stable = {}, {}, 0.
        ctx.set(phase="fault")
        for handle in handles:
            if handle is not None:
                handle.fail(code, message)
        self._write_hold(ctx, self.fault_targets or {})

    def tick(self, ctx, dt):
        if self.phase not in ("moving", "stopping"):
            return
        self.elapsed += dt
        progress = min(1., self.elapsed / self.duration) if self.duration else 1.
        if self.phase == "moving":
            blend = progress * progress * (3 - 2 * progress)
            self._write(ctx, {key: self.origins[key] + (goal - self.origins[key]) * blend
                              for key, goal in self.goals.items()})
        else:
            self._write(ctx, self.goals)
        settled = progress >= 1 and all(
            abs(self.position(key) - goal) <= self.axes[key].position_tolerance
            and abs(self.velocity(key)) <= self.axes[key].velocity_tolerance
            for key, goal in self.goals.items())
        self.stable = self.stable + dt if settled else 0.
        if self.stable + 1e-10 >= self.config.settleSeconds:
            if self.handle is not None:
                self.handle.cancelled() if self.cancelling else self.handle.succeed()
            if self.stop_handle is not None:
                self.stop_handle.cancelled() if self.stop_cancelled else self.stop_handle.succeed()
            self.handle, self.stop_handle, self.phase = None, None, "idle"
        elif self.elapsed > self.duration + self.config.settleTimeoutSeconds:
            self.fault(ctx, "motion_timeout", "Actual joint feedback did not settle before the deadline")
        elif self.handle is not None:
            self.handle.progress(min(.999, progress))


class JointDriveProgram:
    tick_priority = 20

    def init(self, ctx):
        self.motion, self.bind_error, self.action = None, None, None
        try:
            axes = self.bind(ctx)
            for targets in self.configured_targets(ctx.config):
                error = target_error(axes, targets)
                if error:
                    raise ValueError(error.message + ": " + error.details["joint"])
            self.motion = JointMotion(ctx, axes)
        except (KeyError, ValueError) as error:
            self.bind_error = str(error)
        if self.motion is None:
            self.publish(ctx)
        else:
            self._with_feedback(ctx, lambda: (self.motion.hold(ctx), self.publish(ctx)))

    def interlock(self, ctx, action):
        return None

    def _accept_current(self, inv, ctx):
        if self.motion is None:
            return Reject(code="source_unavailable", message="Configured scalar drives are unavailable",
                          details={"reason": self.bind_error})
        if inv.action != "stop":
            return self.motion.accept(self.targets(inv.action, inv.params, ctx.config)) or self.interlock(
                ctx, inv.action)

    def _with_feedback(self, ctx, work, inv=None):
        try:
            self.motion.sample(ctx)
            return work()
        except FeedbackUnavailable as error:
            self.motion.feedback, self.action = None, None
            fail = getattr(inv, "fail", None)
            if fail is not None:
                fail("feedback_unavailable", str(error))
            self.motion.fault(ctx, "feedback_unavailable", str(error))
            return Reject(code="feedback_unavailable", message=str(error))

    def accept(self, inv, ctx):
        if self.motion is None:
            return self._accept_current(inv, ctx)
        return self._with_feedback(ctx, lambda: self._accept_current(inv, ctx))

    def start(self, inv, ctx):
        if self.motion is None:
            error = self._accept_current(inv, ctx)
            inv.fail(error.code, error.message, **error.details)
            return
        self._with_feedback(ctx, lambda: self._start_current(inv, ctx), inv)

    def _start_current(self, inv, ctx):
        error = self._accept_current(inv, ctx)
        if error:
            inv.fail(error.code, error.message, **error.details)
        elif inv.action == "stop":
            self.motion.stop(inv, ctx)
        else:
            self.action = inv.action
            self.motion.start(inv, ctx, self.targets(inv.action, inv.params, ctx.config),
                              inv.params.get("durationSeconds", 1.))
        self.publish(ctx)

    def cancel(self, inv, ctx):
        if self.motion is not None:
            self._with_feedback(ctx, lambda: (self.motion.cancel(inv, ctx), self.publish(ctx)), inv)

    def tick(self, ctx, dt):
        if self.motion is None:
            self.publish(ctx)
        else:
            self._with_feedback(ctx, lambda: self._tick_current(ctx, dt))

    def _tick_current(self, ctx, dt):
        error = self.interlock(ctx, self.action) if self.motion.phase == "moving" else None
        if error:
            self.motion.fault(ctx, error.code, error.message)
        else:
            self.motion.tick(ctx, dt)
        self.publish(ctx)

    def phase(self):
        return self.motion.phase if self.motion is not None else "fault"

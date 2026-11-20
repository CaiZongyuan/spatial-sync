"""Generic posture Reference and multi-policy CPU ONNX Programs for custom embodied robots."""

from __future__ import annotations

import math
from copy import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.info.program import Reject
from packages.library.metadata import Locations
from packages.policies.motion import motion_observations, reference_tracking
from packages.programs.policy_base import (
    ActionName,
    MultiPolicyConfig,
    MultiPolicyProgram,
    SpatialPath,
    inspect_port_joints,
)
from packages.programs.waypoint_guidance import slot_command_bounds, waypoint_command, wrap_pi
from packages.protocol.math import compose_pose, normalize_quaternion, rotate_vector
from packages.training.declarative import Completion, clip_seconds, trajectory_completion


def _orientation_angles(q):
    qx, qy, qz, qw = (float(v) for v in q)
    cos_tilt = max(-1.0, min(1.0, 1.0 - 2.0 * (qx * qx + qy * qy)))
    tilt_deg = math.degrees(math.acos(cos_tilt))
    roll = math.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy))
    sin_pitch = max(-1.0, min(1.0, 2.0 * (qw * qy - qz * qx)))
    pitch = math.asin(sin_pitch)
    yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))
    return tilt_deg, math.degrees(pitch), math.degrees(roll), yaw


def _publish_embodied_facts(ctx, joints, base_body):
    declared = set(ctx.property_names)
    updates = {}
    if "jointPositions" in declared:
        updates["jointPositions"] = [round(float(ctx.physics.joint_q(ref)[0]), 5) for ref in joints.values()]
    if "jointVelocities" in declared:
        updates["jointVelocities"] = [
            round(float(ctx.physics.joint_qd(ref)[0]), 5) for ref in joints.values()
        ]
    if base_body is not None:
        p, q = ctx.physics.body_pose(base_body)
        tilt_deg, pitch_deg, roll_deg, yaw_rad = _orientation_angles(q)
        if "basePosition" in declared:
            updates["basePosition"] = [round(float(val), 5) for val in p]
        if "baseX" in declared:
            updates["baseX"] = round(float(p[0]), 5)
        if "baseY" in declared:
            updates["baseY"] = round(float(p[1]), 5)
        if "baseHeight" in declared:
            updates["baseHeight"] = round(float(p[2]), 5)
        if "tiltDeg" in declared:
            updates["tiltDeg"] = round(tilt_deg, 4)
        if "pitchDeg" in declared:
            updates["pitchDeg"] = round(pitch_deg, 4)
        if "rollDeg" in declared:
            updates["rollDeg"] = round(roll_deg, 4)
        if "yawRad" in declared:
            updates["yawRad"] = round(yaw_rad, 5)
        if "planarSpeed" in declared or "bodyVelocity" in declared:
            v_world = ctx.physics.body_linear_velocity(base_body, frame="world")
            if "planarSpeed" in declared:
                updates["planarSpeed"] = round(float(math.hypot(v_world[0], v_world[1])), 5)
            if "bodyVelocity" in declared:
                v_body = ctx.physics.body_linear_velocity(base_body, frame="body")
                updates["bodyVelocity"] = [round(float(val), 5) for val in v_body]
        if "angularVelocity" in declared:
            w_body = ctx.physics.body_angular_velocity(base_body, frame="body")
            updates["angularVelocity"] = [round(float(val), 5) for val in w_body]
    if updates:
        ctx.set(**updates)


def _detect_state_property(ctx, preferred: str | None) -> str | None:
    declared = set(ctx.property_names)
    if preferred and preferred in declared:
        return preferred
    for candidate in ("phase", "state"):
        if candidate in declared:
            return candidate
    return None


def _clamp_posture(keys, values, limits):
    clamped = []
    for key, raw in zip(keys, values, strict=True):
        val = float(raw)
        phys_lo, phys_hi = limits.get(key, (None, None))
        if phys_lo is not None:
            val = max(phys_lo, val)
        if phys_hi is not None:
            val = min(phys_hi, val)
        clamped.append(val)
    return clamped


class EmbodiedPostureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jointKeys: list[SpatialPath] = Field(min_length=1, json_schema_extra={"spatialReference": "joint"})
    prismaticJointKeys: list[SpatialPath] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        json_schema_extra={"spatialReference": "joint"},
    )
    baseBody: SpatialPath | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        json_schema_extra={"spatialReference": "body"},
    )
    defaultPosition: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)
    actionTargets: dict[ActionName, list[float]] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    actionDuration: float = Field(default=0.5, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_postures(self):
        size = len(self.jointKeys)
        if len(set(self.jointKeys)) != size:
            raise ValueError("jointKeys must be distinct")
        if len(set(self.prismaticJointKeys)) != len(self.prismaticJointKeys) or not (
            set(self.prismaticJointKeys) <= set(self.jointKeys)
        ):
            raise ValueError("prismaticJointKeys must be distinct members of jointKeys")
        if self.defaultPosition is not None and len(self.defaultPosition) != size:
            raise ValueError("defaultPosition must match jointKeys length")
        for target in self.actionTargets.values():
            if len(target) != size:
                raise ValueError("actionTargets entries must match jointKeys length")
        return self


class EmbodiedPostureProgram:
    """Open-loop posture Reference Program compatible with any custom multi-joint EntityType."""

    id, type, tick_priority = "embodied.posture", None, 10
    Config = EmbodiedPostureConfig

    @classmethod
    def supports_type(cls, entity_type: str) -> bool:
        return cls.type is None or cls.type == entity_type

    def init(self, ctx):
        if ctx.physics is None:
            raise ValueError("embodied.posture requires the Newton PhysicsPort")
        prismatic = set(ctx.config.prismaticJointKeys)
        fallback_units = {key: ("m" if key in prismatic else "rad") for key in ctx.config.jointKeys}
        self.joints = {key: ctx.physics.bind_joint(key) for key in ctx.config.jointKeys}
        _, self.joint_limits = inspect_port_joints(
            ctx.physics,
            self.joints,
            fallback_units=fallback_units,
            prismatic_keys=ctx.config.prismaticJointKeys,
        )
        self.base_body = ctx.physics.bind_body(ctx.config.baseBody) if ctx.config.baseBody else None
        self._state_prop = _detect_state_property(ctx, "phase")
        self.handle = None
        self.started = 0.0
        if ctx.config.defaultPosition is not None:
            targets = _clamp_posture(ctx.config.jointKeys, ctx.config.defaultPosition, self.joint_limits)
            for key, value in zip(ctx.config.jointKeys, targets, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])
        _publish_embodied_facts(ctx, self.joints, self.base_body)
        self._set_state(ctx, "idle")

    def _set_state(self, ctx, value: str):
        if self._state_prop is not None:
            ctx.set(**{self._state_prop: value})

    def _hold(self, ctx):
        for ref in self.joints.values():
            ctx.physics.set_joint_target(ref, ctx.physics.joint_q(ref))

    def accept(self, inv, ctx):
        return None

    def start(self, inv, ctx):
        if inv.action == "stop":
            self._hold(ctx)
            if self.handle is not None:
                self.handle.cancelled()
                self.handle = None
            self._set_state(ctx, "idle")
            inv.succeed()
            return
        self.handle = inv
        self.started = ctx.sim_time
        posture = ctx.config.actionTargets.get(inv.action) or ctx.config.defaultPosition
        if posture is not None:
            targets = _clamp_posture(ctx.config.jointKeys, posture, self.joint_limits)
            for key, value in zip(ctx.config.jointKeys, targets, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])
        else:
            self._hold(ctx)
        self._set_state(ctx, "active")

    def cancel(self, inv, ctx):
        self._hold(ctx)
        inv.cancelled()
        self.handle = None
        self._set_state(ctx, "idle")

    def tick(self, ctx, dt):
        if ctx.fact_due:
            _publish_embodied_facts(ctx, self.joints, self.base_body)
        if self.handle is None:
            return
        if ctx.sim_time - self.started >= ctx.config.actionDuration:
            _publish_embodied_facts(ctx, self.joints, self.base_body)
            self.handle.succeed()
            self.handle = None
            self._set_state(ctx, "idle")


class ReferenceSite(BaseModel):
    body: SpatialPath
    offset: tuple[float, float, float]

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ReferencePlacement(BaseModel):
    p: tuple[float, float, float] = (0., 0., 0.)
    q: tuple[float, float, float, float] = (0., 0., 0., 1.)
    sites: dict[str, ReferenceSite] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="after")
    def unit_rotation(self):
        self.q = normalize_quaternion(self.q)
        return self


class TrajectoryCheck:
    """Measure physical state against the same phase used for policy observations."""

    def __init__(self, ctx, slot, joints, units):
        self.motion = slot.task["referenceMotion"]
        placement = ctx.config.referencePlacement or ReferencePlacement()
        self.placement = {"p": placement.p, "q": placement.q}
        def scene_key(key):
            return ctx.key + key.removeprefix("/Asset")

        keys = [scene_key(key) for key in self.motion["jointKeys"]]
        self.joints = [(joints[key], units[key]) for key in keys]
        self.points = {}
        self.reset()
        for key in (self.motion["frames"][0].get("bodyLocal") or {}):
            site = placement.sites.get(key)
            if site:
                self.points[key] = (ctx.physics.body_pose, ctx.physics.bind_body(site.body), site.offset)
                continue
            try:
                index = ctx.physics.bind_body(scene_key(key))
            except KeyError:
                self.points[key] = (ctx.physics.site_pose, ctx.physics.bind_site(scene_key(key)), None)
            else:
                self.points[key] = (ctx.physics.body_pose, index, None)

    def reset(self):
        self.first_steady_sample = None

    def sample(self, ctx, root, elapsed, completion):
        position, orientation = ctx.physics.body_pose(root)
        tilt = _orientation_angles(orientation)[0]
        self.fallen = tilt > completion.maxTiltDeg or float(position[2]) < completion.minHeight
        criteria = completion.trajectory
        error = self.error(ctx.physics, position, orientation, elapsed, criteria)
        terminal = elapsed >= clip_seconds(self.motion, completion)
        self.failed = not terminal and error > 1.
        steady = False
        if terminal and error <= 1. and not self.fallen and tilt <= completion.uprightTiltDeg:
            linear = ctx.physics.body_linear_velocity(root, frame="body")
            angular = ctx.physics.body_angular_velocity(root, frame="body")
            steady = (
                math.hypot(*linear) <= completion.maxSpeed
                and math.hypot(*angular) <= completion.maxAngularSpeed
                and self.joints_stationary(ctx.physics, criteria)
            )
        if not steady:
            self.first_steady_sample = None
            return 0.
        if self.first_steady_sample is None:
            self.first_steady_sample = ctx.sim_time
        return ctx.sim_time - self.first_steady_sample

    def error(self, physics, position, orientation, elapsed, criteria):
        pose = motion_observations(self.motion, elapsed)["reference_motion_frame"]
        target = compose_pose(self.placement, {"p": pose[:3], "q": pose[3:7]})
        actual = normalize_quaternion(orientation)
        cosine = abs(sum(a * b for a, b in zip(actual, target["q"], strict=True)))
        error = max(
            math.dist(position, target["p"]) / criteria.rootPositionM,
            2 * math.acos(min(1., cosine)) / criteria.rootOrientationRad,
        )
        for (joint, unit), expected in zip(self.joints, pose[7:], strict=True):
            limit = criteria.slideM if unit == "m" else criteria.hingeRad
            error = max(error, abs(float(physics.joint_q(joint)[0]) - expected) / limit)
        if self.points:
            x, y, z, w = actual
            targets = reference_tracking(self.motion, elapsed)["bodyLocal"]
            for key, (read_pose, index, site_offset) in self.points.items():
                point, rotation = read_pose(index)
                if site_offset is not None:
                    point = [a + b for a, b in zip(point, rotate_vector(rotation, site_offset), strict=True)]
                offset = [a - b for a, b in zip(point, position, strict=True)]
                local = rotate_vector((-x, -y, -z, w), offset)
                error = max(error, math.dist(local, targets[key]) / criteria.bodyLocalM)
        return error

    def joints_stationary(self, physics, criteria):
        return all(
            abs(float(physics.joint_qd(joint)[0])) <= (
                criteria.terminalSlideMPerSec if unit == "m" else criteria.terminalHingeRadPerSec
            )
            for joint, unit in self.joints
        )


class PostureFallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program: Literal["biped.posture", "embodied.posture"]
    config: dict

    def program_class(self):
        if self.program == "biped.posture":
            from packages.programs.biped.posture import BipedPostureProgram

            return BipedPostureProgram
        return EmbodiedPostureProgram

    @model_validator(mode="after")
    def validate_binding(self):
        self.config = self.program_class().Config.model_validate(self.config).model_dump(mode="json")
        return self


class EmbodiedPolicyConfig(MultiPolicyConfig):
    postureFallback: PostureFallback | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    referencePlacement: ReferencePlacement | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    locations: dict = Field(default_factory=dict, exclude_if=lambda value: not value,
                            json_schema_extra={"documentSchema": "locations"})
    xyTolerance: float = Field(default=.08, gt=0, exclude_if=lambda value: value == .08)
    yawTolerance: float = Field(default=.2, gt=0, exclude_if=lambda value: value == .2)
    linearGain: float = Field(default=1.5, gt=0, exclude_if=lambda value: value == 1.5)
    yawGain: float = Field(default=2., gt=0, exclude_if=lambda value: value == 2.)
    maxLinearSpeed: float = Field(default=.35, gt=0, exclude_if=lambda value: value == .35)
    maxLateralSpeed: float = Field(default=.2, ge=0, exclude_if=lambda value: value == .2)
    maxYawRate: float = Field(default=1., gt=0, exclude_if=lambda value: value == 1.)
    commandSmoothing: float = Field(default=.35, gt=0, le=1, exclude_if=lambda value: value == .35)
    headingAlignRad: float = Field(default=.9, gt=0, exclude_if=lambda value: value == .9)
    defaultPosition: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_embodied_fields(self):
        if self.defaultPosition is not None and len(self.defaultPosition) != len(self.jointKeys):
            raise ValueError("defaultPosition must match jointKeys length")
        return self


class EmbodiedPolicyProgram(MultiPolicyProgram):
    """Polymorphic multi-policy CPU ONNX runtime Program for custom embodied EntityTypes."""

    id, type, tick_priority = "embodied.policy", None, 10
    Config = EmbodiedPolicyConfig

    def init(self, ctx):
        self.fallback = None
        self.fallback_selected = False
        super().init(ctx)
        if ctx.config.postureFallback is not None:
            binding = ctx.config.postureFallback
            program = binding.program_class()
            self.fallback_ctx = copy(ctx)
            self.fallback_ctx.config = program.Config.model_validate(binding.config)
            self.fallback = program()
            self.fallback.init(self.fallback_ctx)
            self.fallback_selected = True

    def _uses_fallback(self, action):
        return self.fallback is not None and action not in self.slots and (
            action not in self.stop_actions or self.fallback_selected
        )

    def accept(self, inv, ctx):
        if self._uses_fallback(inv.action):
            if not self.fallback_selected and hasattr(self.fallback, "fallen"):
                self.fallback.fallen = self.fallen
            return self.fallback.accept(inv, self.fallback_ctx)
        if self.fallback_selected:
            self.fallen = getattr(self.fallback, "fallen", self.fallen)
        return super().accept(inv, ctx)

    def start(self, inv, ctx):
        if self._uses_fallback(inv.action):
            self.fallback_selected = True
            self.fallback.start(inv, self.fallback_ctx)
            return
        if self.fallback_selected:
            self.fallen = getattr(self.fallback, "fallen", self.fallen)
        self.fallback_selected = False
        super().start(inv, ctx)

    def cancel(self, inv, ctx):
        if self._uses_fallback(inv.action):
            self.fallback.cancel(inv, self.fallback_ctx)
            return
        super().cancel(inv, ctx)

    def tick(self, ctx, dt):
        if self.fallback_selected:
            self.fallback.tick(self.fallback_ctx, dt)
        else:
            super().tick(ctx, dt)

    def _init_robot(self, ctx):
        self.state_property = _detect_state_property(ctx, self.state_property)
        self.completions = {}
        self.trajectories = {}
        for action, slot in self.slots.items():
            completion = trajectory_completion(slot.task)
            if completion:
                self.trajectories[action] = TrajectoryCheck(ctx, slot, self.joints, self.joint_units)
                self.completions[action] = completion
            elif slot.task.get("completion") is not None:
                self.completions[action] = Completion.model_validate(slot.task["completion"])
        self.motion_error = None
        self.waypoints = {
            name: value.model_dump(mode="json")
            for name, value in Locations.model_validate(ctx.config.locations).waypoints.items()
        }
        self.command_keys = {}
        for action, slot in self.slots.items():
            completion = self.completions.get(action)
            bounds = slot_command_bounds(slot)
            if completion is not None and self.base_body is None:
                raise ValueError("Embodied completion modes require a bound baseBody")
            if completion is not None and completion.mode in ("command_track", "waypoint_nav"):
                if (not bounds or len(bounds) not in (2, 3) or any(lo > hi for lo, hi in bounds)
                        or any(term.frame != "body" for term in slot.spec.observations
                               if term.source == "command")):
                    raise ValueError("Velocity guidance requires bounded body-frame command channels")
                if completion.mode == "waypoint_nav":
                    if (not self.waypoints or any(not lo <= 0 <= hi for lo, hi in bounds)
                            or "waypoint" not in ctx.action_params(action)):
                        raise ValueError("Navigation requires waypoints, zero command and waypoint parameter")
                    continue
            if bounds:
                keys = tuple(ctx.config.commandParams.get(action, ctx.action_params(action)))
                if len(keys) != len(bounds) or not set(keys) <= set(ctx.action_params(action)):
                    raise ValueError("Command parameters must match the Policy command layout")
                self.command_keys[action] = keys
        self.smoothed_command = [0., 0., 0.]
        self.target_waypoint = None
        self.current_waypoint = None
        self.last_completed = None
        self.dwell = 0.
        self.tracking_error = None
        self.fallen = False
        if ctx.config.defaultPosition is not None:
            targets = _clamp_posture(ctx.config.jointKeys, ctx.config.defaultPosition, self.joint_limits)
            for key, value in zip(ctx.config.jointKeys, targets, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])

    def _telemetry(self, ctx, **values):
        values = {name: value for name, value in values.items() if name in ctx.property_names}
        if values:
            ctx.set(**values)

    def _facts(self, ctx):
        _publish_embodied_facts(ctx, self.joints, self.base_body)
        if self.completions:
            self._telemetry(
                ctx, activeAction=self.handle.action if self.handle else None,
                lastCompletedAction=self.last_completed, fallen=self.fallen,
                currentWaypoint=self.current_waypoint, targetWaypoint=self.target_waypoint,
            )
            if self.tracking_error is not None:
                self._telemetry(ctx, trackingError=self.tracking_error)

    def _parameter_coverage_failure(self, inv, ctx, slot):
        completion = self.completions.get(inv.action)
        if completion is None or completion.mode != "waypoint_nav":
            return super()._parameter_coverage_failure(inv, ctx, slot)
        if inv.params.get("waypoint") not in self.waypoints:
            return {"code": "unknown_waypoint", "message": "Waypoint is not declared by this Scene"}
        speed = inv.params.get("speed")
        if speed is not None and (
            type(speed) not in (int, float) or not math.isfinite(speed) or speed <= 0
        ):
            return {"code": "precondition_failed", "message": "Waypoint speed must be positive"}
        task = slot.task
        expected = task["episode"].get("params", {})
        filtered = {key: value for key, value in inv.params.items() if key not in ("waypoint", "speed")}
        if filtered != {key: value for key, value in expected.items() if key not in ("waypoint", "speed")}:
            return {"code": "precondition_failed", "message": "Waypoint parameters exceed training coverage"}
        return None

    def _accept_action(self, inv, ctx, slot):
        mode = self.completions.get(inv.action)
        if mode is None:
            return None
        if mode.mode != "posture_recovery" and self.fallen:
            return Reject(code="robot_fallen", message="Recover posture before starting another action")
        if mode.mode == "waypoint_nav":
            failed = ctx.check(self.waypoints[inv.params["waypoint"]].get("requires", []))
            if failed:
                return Reject(code="precondition_failed", message=failed[0]["message"])
        return None

    def _command(self, inv, ctx, slot):
        completion = self.completions.get(inv.action)
        if completion is None or completion.mode != "waypoint_nav":
            keys = self.command_keys.get(inv.action)
            return [float(inv.params[key]) for key in keys] if keys else None
        p, q = ctx.physics.body_pose(self.base_body)
        command, history = waypoint_command(
            self.waypoints[inv.params["waypoint"]], p, _orientation_angles(q)[3],
            slot_command_bounds(slot), ctx.config, inv.params.get("speed"),
            self.smoothed_command if self.handle is inv else [0., 0., 0.],
        )
        if self.handle is inv:
            self.smoothed_command = history
        return command

    def _on_action_start(self, inv, ctx, slot):
        self.last_completed = None
        self.motion_error = None
        self.dwell = 0.
        self.smoothed_command = [0., 0., 0.]
        self.target_waypoint = inv.params.get("waypoint")
        if self.target_waypoint is not None:
            self.current_waypoint = None
        self._facts(ctx)
        tracker = self.trajectories.get(inv.action)
        if tracker is None:
            super()._on_action_start(inv, ctx, slot)
        else:
            tracker.reset()

    def _tick_robot(self, ctx, dt):
        if not self.completions:
            return
        completion = self.completions.get(self.handle.action) if self.handle else None
        p, q = ctx.physics.body_pose(self.base_body)
        tilt, _, _, yaw = _orientation_angles(q)
        limits = completion or next(iter(self.completions.values()))
        self.fallen = tilt > limits.maxTiltDeg or float(p[2]) < limits.minHeight
        if completion is None:
            return
        tracker = self.trajectories.get(self.handle.action)
        if tracker:
            self.dwell = tracker.sample(ctx, self.base_body, ctx.sim_time - self.started, completion)
            self.fallen = tracker.fallen
            if tracker.failed:
                self.motion_error = True
            return
        speed = ctx.physics.body_linear_velocity(self.base_body, frame="body")
        angular = ctx.physics.body_angular_velocity(self.base_body, frame="body")
        calm = (
            math.hypot(*speed) <= completion.maxSpeed and math.hypot(*angular) <= completion.maxAngularSpeed
        )
        if completion.mode == "phase_clip":
            duration = clip_seconds(self.active_slot.task["referenceMotion"], completion)
            reached = ctx.sim_time - self.started + 1e-9 >= duration
        elif completion.mode == "posture_recovery":
            reached = not self.fallen and tilt <= completion.uprightTiltDeg and calm
        elif completion.mode == "waypoint_nav":
            wp = self.waypoints[self.target_waypoint]
            self.tracking_error = math.hypot(float(p[0]) - wp["p"][0], float(p[1]) - wp["p"][1])
            reached = (
                self.tracking_error <= (wp.get("xyTolerance") or ctx.config.xyTolerance)
                and abs(wrap_pi(wp["yaw"] - yaw)) <= (wp.get("yawTolerance") or ctx.config.yawTolerance)
                and calm
            )
        else:
            desired = self._command(self.handle, ctx, self.active_slot)
            measured = [speed[0], angular[2]] if len(desired) == 2 else [speed[0], speed[1], angular[2]]
            self.tracking_error = math.dist(measured, desired)
            reached = self.tracking_error <= completion.trackingTolerance
        self.dwell = self.dwell + dt if reached else 0.

    def _fault_failure(self, inv, ctx, slot):
        completion = self.completions.get(inv.action)
        if completion is not None and completion.mode != "posture_recovery" and self.fallen:
            return "robot_fallen", "Base height or tilt left the commissioned range"
        if self.motion_error is not None:
            return "motion_tracking_failed", "Measured pose left the pinned trajectory tolerance"
        return None

    def _action_completed(self, inv, ctx, slot):
        completion = self.completions.get(inv.action)
        if completion is None:
            return super()._action_completed(inv, ctx, slot)
        dwell = self.dwell if inv.action in self.trajectories else self.dwell + 1e-9
        return (
            slot.model.inference_calls > self.start_inferences
            and dwell >= completion.dwellSeconds
            and not self.fallen
            and self.motion_error is None
        )

    def _clear_action(self, ctx):
        self.target_waypoint = None
        self.tracking_error = None
        if self.completions:
            self._telemetry(ctx, activeAction=None, targetWaypoint=None, fallen=self.fallen)

    def _set_idle_state(self, ctx):
        super()._set_idle_state(ctx)
        self._clear_action(ctx)

    def _set_fault_state(self, ctx, code, message):
        pinned_biped = (
            ctx.entity_type == "biped/v1" and self._last_slot is not None
            and self._last_slot.action in self.trajectories
        )
        if pinned_biped:
            self._telemetry(ctx, phase="fallen" if self.fallen else "idle")
        else:
            super()._set_fault_state(ctx, code, message)
        self._clear_action(ctx)

    def _on_action_complete(self, inv, ctx, slot):
        self.last_completed = inv.action
        if self.target_waypoint is not None:
            self.current_waypoint = self.target_waypoint
        self._facts(ctx)
        super()._on_action_complete(inv, ctx, slot)

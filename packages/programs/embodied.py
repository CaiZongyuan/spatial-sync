"""Generic posture Reference and multi-policy CPU ONNX Programs for custom embodied robots."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.info.program import Reject
from packages.programs.policy_base import ActionName, MultiPolicyConfig, MultiPolicyProgram, SpatialPath


def _orientation_angles(q):
    qx, qy, qz, qw = (float(v) for v in q)
    cos_tilt = max(-1.0, min(1.0, 1.0 - 2.0 * (qx * qx + qy * qy)))
    tilt_deg = math.degrees(math.acos(cos_tilt))
    roll = math.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy))
    sin_pitch = max(-1.0, min(1.0, 2.0 * (qw * qy - qz * qx)))
    pitch = math.asin(sin_pitch)
    yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))
    return tilt_deg, math.degrees(pitch), math.degrees(roll), yaw


def _publish_embodied_facts(ctx, joints, base_body, goal_xy):
    declared = set(ctx._properties)
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
        if "distanceToGoal" in declared:
            dist = float(math.hypot(goal_xy[0] - p[0], goal_xy[1] - p[1])) if goal_xy is not None else 0.0
            updates["distanceToGoal"] = round(dist, 5)
    if updates:
        ctx.set(**updates)


def _detect_state_property(ctx, preferred: str | None) -> str | None:
    declared = set(ctx._properties)
    if preferred and preferred in declared:
        return preferred
    for candidate in ("phase", "state"):
        if candidate in declared:
            return candidate
    return None


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
    maxTiltDeg: float | None = Field(default=None, gt=0, le=180, exclude_if=lambda value: value is None)
    commandMode: Literal["none", "planar_target", "direct_params"] = Field(
        default="none", exclude_if=lambda value: value == "none"
    )
    commandKeys: list[str] = Field(default_factory=list, exclude_if=lambda value: not value)
    defaultSpeed: float = Field(default=0.15, gt=0, allow_inf_nan=False, exclude_if=lambda v: v == 0.15)
    maxYawRate: float = Field(default=1.0, gt=0, allow_inf_nan=False, exclude_if=lambda v: v == 1.0)
    arrivalTolerance: float = Field(default=0.05, gt=0, allow_inf_nan=False, exclude_if=lambda v: v == 0.05)
    maxSeconds: float = Field(default=20.0, gt=0, allow_inf_nan=False)

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

    def init(self, ctx):
        if ctx.physics is None:
            raise ValueError("embodied.posture requires the Newton PhysicsPort")
        self.joints = {key: ctx.physics.bind_joint(key) for key in ctx.config.jointKeys}
        self.base_body = ctx.physics.bind_body(ctx.config.baseBody) if ctx.config.baseBody else None
        self._state_prop = _detect_state_property(ctx, "phase")
        self.handle = None
        self.started = 0.0
        self._goal_xy: tuple[float, float] | None = None
        if ctx.config.defaultPosition is not None:
            for key, value in zip(ctx.config.jointKeys, ctx.config.defaultPosition, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])
        _publish_embodied_facts(ctx, self.joints, self.base_body, self._goal_xy)
        self._set_state(ctx, "idle")

    def _set_state(self, ctx, value: str):
        if self._state_prop is not None:
            ctx.set(**{self._state_prop: value})

    def _hold(self, ctx):
        for ref in self.joints.values():
            ctx.physics.set_joint_target(ref, ctx.physics.joint_q(ref))

    def accept(self, inv, ctx):
        if inv.action == "stop":
            return None
        if ctx.config.maxTiltDeg is not None and self.base_body is not None:
            _, q = ctx.physics.body_pose(self.base_body)
            tilt_deg, _, _, _ = _orientation_angles(q)
            if tilt_deg > ctx.config.maxTiltDeg:
                return Reject(code="precondition_failed", message="Robot exceeds maxTiltDeg safety threshold")
        return None

    def start(self, inv, ctx):
        if inv.action == "stop":
            self._hold(ctx)
            if self.handle is not None:
                self.handle.cancelled()
                self.handle = None
            self._goal_xy = None
            self._set_state(ctx, "idle")
            inv.succeed()
            return
        self.handle = inv
        self.started = ctx.sim_time
        self._goal_xy = (
            (float(inv.params["x"]), float(inv.params["y"]))
            if "x" in inv.params and "y" in inv.params
            else None
        )
        posture = ctx.config.actionTargets.get(inv.action) or ctx.config.defaultPosition
        if posture is not None:
            for key, value in zip(ctx.config.jointKeys, posture, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])
        else:
            self._hold(ctx)
        self._set_state(ctx, "active")

    def cancel(self, inv, ctx):
        self._hold(ctx)
        inv.cancelled()
        self.handle = None
        self._goal_xy = None
        self._set_state(ctx, "idle")

    def tick(self, ctx, dt):
        if ctx.fact_due:
            _publish_embodied_facts(ctx, self.joints, self.base_body, self._goal_xy)
        if self.handle is None:
            return
        if ctx.config.maxTiltDeg is not None and self.base_body is not None:
            _, q = ctx.physics.body_pose(self.base_body)
            tilt_deg, _, _, _ = _orientation_angles(q)
            if tilt_deg > ctx.config.maxTiltDeg:
                self._hold(ctx)
                self.handle.fail("stability_fault", "Robot exceeded maxTiltDeg during posture hold")
                self.handle = None
                self._set_state(ctx, "fault")
                return
        if ctx.sim_time - self.started >= ctx.config.actionDuration:
            _publish_embodied_facts(ctx, self.joints, self.base_body, self._goal_xy)
            self.handle.succeed()
            self.handle = None
            self._set_state(ctx, "idle")


class EmbodiedPolicyConfig(MultiPolicyConfig):
    defaultPosition: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)
    maxTiltDeg: float | None = Field(default=None, gt=0, le=180, exclude_if=lambda value: value is None)
    commandMode: Literal["none", "planar_target", "direct_params"] = Field(
        default="none", exclude_if=lambda value: value == "none"
    )
    commandKeys: list[str] = Field(default_factory=list, exclude_if=lambda value: not value)
    defaultSpeed: float = Field(default=0.15, gt=0, allow_inf_nan=False, exclude_if=lambda v: v == 0.15)
    maxYawRate: float = Field(default=1.0, gt=0, allow_inf_nan=False, exclude_if=lambda v: v == 1.0)
    arrivalTolerance: float = Field(default=0.05, gt=0, allow_inf_nan=False, exclude_if=lambda v: v == 0.05)

    @model_validator(mode="after")
    def validate_embodied_fields(self):
        if self.defaultPosition is not None and len(self.defaultPosition) != len(self.jointKeys):
            raise ValueError("defaultPosition must match jointKeys length")
        if self.commandMode == "direct_params" and not self.commandKeys:
            raise ValueError("commandKeys is required when commandMode is 'direct_params'")
        return self


class EmbodiedPolicyProgram(MultiPolicyProgram):
    """Polymorphic multi-policy CPU ONNX runtime Program for custom embodied EntityTypes."""

    id, type, tick_priority = "embodied.policy", None, 10
    Config = EmbodiedPolicyConfig

    def _init_robot(self, ctx):
        self.state_property = _detect_state_property(ctx, self.state_property)
        self._goal_xy: tuple[float, float] | None = None
        if ctx.config.defaultPosition is not None:
            for key, value in zip(ctx.config.jointKeys, ctx.config.defaultPosition, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])

    def _facts(self, ctx):
        _publish_embodied_facts(ctx, self.joints, self.base_body, self._goal_xy)

    def _accept_action(self, inv, ctx, slot):
        if ctx.config.maxTiltDeg is not None and self.base_body is not None:
            _, q = ctx.physics.body_pose(self.base_body)
            tilt_deg, _, _, _ = _orientation_angles(q)
            if tilt_deg > ctx.config.maxTiltDeg:
                return Reject(code="precondition_failed", message="Robot exceeds maxTiltDeg safety threshold")
        return None

    def _on_action_start(self, inv, ctx, slot):
        self._goal_xy = (
            (float(inv.params["x"]), float(inv.params["y"]))
            if "x" in inv.params and "y" in inv.params
            else None
        )
        _publish_embodied_facts(ctx, self.joints, self.base_body, self._goal_xy)
        super()._on_action_start(inv, ctx, slot)

    def _on_stop(self, inv, ctx):
        self._goal_xy = None
        super()._on_stop(inv, ctx)

    def _fault_failure(self, inv, ctx, slot):
        if ctx.config.maxTiltDeg is not None and self.base_body is not None:
            _, q = ctx.physics.body_pose(self.base_body)
            tilt_deg, _, _, _ = _orientation_angles(q)
            if tilt_deg > ctx.config.maxTiltDeg:
                return "stability_fault", "Robot exceeded maxTiltDeg safety threshold"
        return None

    def _command(self, inv, ctx, slot):
        cmd_obs = [item for item in slot.spec.observations if item.source == "command"]
        has_envelope = slot.task.get("episode", {}).get("commandEnvelope") is not None
        if not cmd_obs and not has_envelope:
            return None
        width = (
            cmd_obs[0].shape[0]
            if cmd_obs
            else len(slot.task["episode"]["commandEnvelope"])
        )
        mode = ctx.config.commandMode
        if mode == "direct_params" or (mode == "none" and ctx.config.commandKeys):
            return [float(inv.params[key]) for key in ctx.config.commandKeys[:width]]
        if mode == "planar_target" or ("x" in inv.params and "y" in inv.params):
            if self.base_body is None:
                raise ValueError("planar_target commandMode requires baseBody")
            p, q = ctx.physics.body_pose(self.base_body)
            _, _, _, yaw = _orientation_angles(q)
            dx = float(inv.params["x"]) - float(p[0])
            dy = float(inv.params["y"]) - float(p[1])
            dist = math.hypot(dx, dy)
            if dist <= ctx.config.arrivalTolerance:
                return [0.0] * width
            speed = float(inv.params.get("speed", ctx.config.defaultSpeed))
            cos_y, sin_y = math.cos(yaw), math.sin(yaw)
            dx_b = cos_y * dx + sin_y * dy
            dy_b = -sin_y * dx + cos_y * dy
            vx = (dx_b / dist) * speed
            vy = (dy_b / dist) * speed
            wz = float(np.clip(math.atan2(dy_b, dx_b), -ctx.config.maxYawRate, ctx.config.maxYawRate))
            full = [vx, vy, wz]
            return full[:width] if width <= len(full) else full + [0.0] * (width - len(full))
        return [0.0] * width

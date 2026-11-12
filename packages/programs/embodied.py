"""Generic posture Reference and multi-policy CPU ONNX Programs for custom embodied robots."""

from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.programs.policy_base import (
    ActionName,
    MultiPolicyConfig,
    MultiPolicyProgram,
    SpatialPath,
    inspect_port_joints,
)


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


class EmbodiedPolicyConfig(MultiPolicyConfig):
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

    def _init_robot(self, ctx):
        self.state_property = _detect_state_property(ctx, self.state_property)
        if ctx.config.defaultPosition is not None:
            targets = _clamp_posture(ctx.config.jointKeys, ctx.config.defaultPosition, self.joint_limits)
            for key, value in zip(ctx.config.jointKeys, targets, strict=True):
                ctx.physics.set_joint_target(self.joints[key], [value])

    def _facts(self, ctx):
        _publish_embodied_facts(ctx, self.joints, self.base_body)

    def _on_action_start(self, inv, ctx, slot):
        _publish_embodied_facts(ctx, self.joints, self.base_body)
        super()._on_action_start(inv, ctx, slot)

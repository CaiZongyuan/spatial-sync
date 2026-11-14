"""Open-loop standing/posture Reference Program for biped/v1."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.info.program import Reject
from packages.library.metadata import Locations
from packages.programs.embodied import _clamp_posture, _orientation_angles
from packages.programs.policy_base import ActionName, SpatialPath, inspect_port_joints


def wrap_pi(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def read_biped_kinematics(ctx, base_body):
    p, q = ctx.physics.body_pose(base_body)
    tilt_deg, pitch_deg, roll_deg, yaw_rad = _orientation_angles(q)
    v_body = ctx.physics.body_linear_velocity(base_body, frame="body")
    return p, tilt_deg, pitch_deg, roll_deg, yaw_rad, v_body


def publish_biped_telemetry(
    ctx,
    joints,
    base_body,
    *,
    phase: str,
    active_action: str | None,
    last_completed_action: str | None = None,
    fallen: bool,
    current_waypoint: str | None,
    target_waypoint: str | None,
):
    p, tilt_deg, pitch_deg, _, yaw_rad, v_body = read_biped_kinematics(ctx, base_body)
    ctx.set(
        phase=phase,
        activeAction=active_action,
        lastCompletedAction=last_completed_action,
        fallen=fallen,
        jointPositions=[round(float(ctx.physics.joint_q(ref)[0]), 5) for ref in joints.values()],
        currentWaypoint=current_waypoint,
        targetWaypoint=target_waypoint,
        basePosition=[round(float(val), 5) for val in p],
        yawRad=round(float(yaw_rad), 5),
        tiltDeg=round(float(tilt_deg), 4),
        pitchDeg=round(float(pitch_deg), 4),
        bodyVelocity=[round(float(val), 5) for val in v_body],
    )
    return p, tilt_deg, pitch_deg, yaw_rad


class BipedPostureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jointKeys: list[SpatialPath] = Field(min_length=1, json_schema_extra={"spatialReference": "joint"})
    prismaticJointKeys: list[SpatialPath] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
        json_schema_extra={"spatialReference": "joint"},
    )
    baseBody: SpatialPath = Field(json_schema_extra={"spatialReference": "body"})
    locations: dict = Field(default_factory=dict, json_schema_extra={"documentSchema": "locations"})
    defaultPosition: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)
    actionTargets: dict[ActionName, list[float]] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    fallPostures: dict[Literal["front", "back", "left", "right"], list[float]] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    actionDuration: float = Field(default=0.8, gt=0, allow_inf_nan=False)
    maxTiltDeg: float = Field(default=55.0, gt=0, lt=180.0, allow_inf_nan=False)
    uprightTiltDeg: float = Field(default=20.0, gt=0, lt=90.0, allow_inf_nan=False)
    bowMinPitchDeg: float = Field(default=8.0, ge=0, lt=90.0, allow_inf_nan=False)
    xyTolerance: float = Field(default=0.08, gt=0, allow_inf_nan=False)
    yawTolerance: float = Field(default=0.20, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_postures_and_locations(self):
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
        for posture in self.fallPostures.values():
            if len(posture) != size:
                raise ValueError("fallPostures entries must match jointKeys length")
        if self.uprightTiltDeg >= self.maxTiltDeg:
            raise ValueError("uprightTiltDeg must be strictly less than maxTiltDeg")
        Locations.model_validate(self.locations)
        return self


class BipedPostureProgram:
    """Open-loop standing/posture Reference Program for biped/v1."""

    id, type, tick_priority = "biped.posture", "biped/v1", 10
    Config = BipedPostureConfig

    def init(self, ctx):
        if ctx.physics is None:
            raise ValueError("biped.posture requires the Newton PhysicsPort")
        prismatic = set(ctx.config.prismaticJointKeys)
        fallback_units = {key: ("m" if key in prismatic else "rad") for key in ctx.config.jointKeys}
        self.joints = {key: ctx.physics.bind_joint(key) for key in ctx.config.jointKeys}
        _, self.joint_limits = inspect_port_joints(
            ctx.physics,
            self.joints,
            fallback_units=fallback_units,
            prismatic_keys=ctx.config.prismaticJointKeys,
        )
        self.base_body = ctx.physics.bind_body(ctx.config.baseBody)
        parsed_locations = Locations.model_validate(ctx.config.locations)
        self.waypoints = {
            name: wp.model_dump(mode="json") for name, wp in parsed_locations.waypoints.items()
        }
        self.handle = None
        self.started = 0.0
        self.phase = "idle"
        self.active_action: str | None = None
        self.last_completed_action: str | None = None
        self.current_waypoint: str | None = None
        self.target_waypoint: str | None = None
        _, tilt_deg, _, _, _, _ = read_biped_kinematics(ctx, self.base_body)
        self.fallen = tilt_deg > ctx.config.maxTiltDeg
        if self.fallen:
            self.phase = "fallen"
        if ctx.config.defaultPosition is not None:
            self._apply_posture(ctx, ctx.config.defaultPosition)
        self._publish(ctx)

    def _apply_posture(self, ctx, posture: list[float]):
        targets = _clamp_posture(ctx.config.jointKeys, posture, self.joint_limits)
        for key, value in zip(ctx.config.jointKeys, targets, strict=True):
            ctx.physics.set_joint_target(self.joints[key], [value])

    def _hold(self, ctx):
        for ref in self.joints.values():
            ctx.physics.set_joint_target(ref, ctx.physics.joint_q(ref))

    def _publish(self, ctx):
        return publish_biped_telemetry(
            ctx,
            self.joints,
            self.base_body,
            phase=self.phase,
            active_action=self.active_action,
            last_completed_action=self.last_completed_action,
            fallen=self.fallen,
            current_waypoint=self.current_waypoint,
            target_waypoint=self.target_waypoint,
        )

    def accept(self, inv, ctx):
        if inv.action == "walkTo":
            name = inv.params["waypoint"]
            if name not in self.waypoints:
                return Reject(
                    code="unknown_waypoint",
                    message=f"Unknown waypoint '{name}'",
                    details={"availableWaypoints": sorted(self.waypoints)},
                )
            failed = ctx.check(self.waypoints[name].get("requires", []))
            if failed:
                return Reject(
                    code="precondition_failed",
                    message=failed[0]["message"],
                    details={"failedConditions": failed},
                )
        elif inv.action == "standUp":
            if not self.fallen and inv.params.get("fromFall") is None:
                return Reject(
                    code="precondition_failed",
                    message="Robot is already upright; pass fromFall to test fall recovery.",
                )
        return None

    def start(self, inv, ctx):
        if inv.action == "stop":
            self._hold(ctx)
            if self.handle is not None:
                self.handle.cancelled()
                self.handle = None
            _, tilt_deg, _, _, _, _ = read_biped_kinematics(ctx, self.base_body)
            self.fallen = tilt_deg > ctx.config.uprightTiltDeg
            if self.fallen:
                self.last_completed_action = None
            self.active_action = None
            self.target_waypoint = None
            self.phase = "fallen" if self.fallen else "idle"
            self._publish(ctx)
            inv.succeed()
            return
        self.handle = inv
        self.started = ctx.sim_time
        self.active_action = inv.action
        self.last_completed_action = None
        self._bow_peaked = False
        self._saw_fall = self.fallen
        if inv.action == "walkTo":
            self.target_waypoint = inv.params["waypoint"]
            self.current_waypoint = None
            self.phase = "walking"
        elif inv.action == "standUp":
            self.target_waypoint = None
            self.phase = "standing_up"
        elif inv.action == "bow":
            self.target_waypoint = None
            self.phase = "bowing"
        else:
            self.phase = "walking"
        posture = ctx.config.actionTargets.get(inv.action) or ctx.config.defaultPosition
        if posture is not None:
            self._apply_posture(ctx, posture)
        else:
            self._hold(ctx)
        self._publish(ctx)

    def cancel(self, inv, ctx):
        self._hold(ctx)
        inv.cancelled()
        self.handle = None
        _, tilt_deg, _, _, _, _ = read_biped_kinematics(ctx, self.base_body)
        self.fallen = tilt_deg > ctx.config.uprightTiltDeg
        if self.fallen:
            self.last_completed_action = None
        self.active_action = None
        self.target_waypoint = None
        self.phase = "fallen" if self.fallen else "idle"
        self._publish(ctx)

    def tick(self, ctx, dt):
        p, tilt_deg, pitch_deg, _, yaw_rad, _ = read_biped_kinematics(ctx, self.base_body)
        if self.handle is None:
            if not self.fallen and tilt_deg > ctx.config.maxTiltDeg:
                self.fallen = True
                self.last_completed_action = None
                self.phase = "fallen"
                self._hold(ctx)
                self._publish(ctx)
            elif ctx.fact_due:
                self._publish(ctx)
            return

        inv = self.handle
        if inv.action != "standUp" and tilt_deg > ctx.config.maxTiltDeg:
            self.fallen = True
            self.last_completed_action = None
            self.phase = "fallen"
            self.active_action = None
            self.target_waypoint = None
            self._hold(ctx)
            self._publish(ctx)
            self.handle = None
            inv.fail("robot_fallen", f"Torso tilt {tilt_deg:.1f} deg exceeded maxTiltDeg")
            return

        if inv.action == "standUp" and tilt_deg >= ctx.config.maxTiltDeg:
            self._saw_fall = True
            self.fallen = True
            self.last_completed_action = None
        if inv.action == "bow" and (
            ctx.config.bowMinPitchDeg == 0.0 or pitch_deg >= ctx.config.bowMinPitchDeg
        ):
            self._bow_peaked = True

        if ctx.fact_due:
            self._publish(ctx)

        duration = float(inv.params.get("duration", ctx.config.actionDuration))
        if ctx.sim_time - self.started < duration:
            return

        if inv.action == "walkTo":
            wp = self.waypoints[self.target_waypoint]
            xy_tol = float(wp.get("xyTolerance") or ctx.config.xyTolerance)
            yaw_tol = float(wp.get("yawTolerance") or ctx.config.yawTolerance)
            dist = math.hypot(float(wp["p"][0]) - float(p[0]), float(wp["p"][1]) - float(p[1]))
            dyaw = abs(wrap_pi(float(wp["yaw"]) - float(yaw_rad)))
            self._hold(ctx)
            self.handle = None
            reached_name = self.target_waypoint
            self.target_waypoint = None
            self.active_action = None
            self.phase = "idle"
            if dist <= xy_tol and dyaw <= yaw_tol:
                self.current_waypoint = reached_name
                self.last_completed_action = "walkTo"
                self._publish(ctx)
                inv.succeed()
            else:
                self._publish(ctx)
                inv.fail("motion_timeout", "Open-loop posture did not reach target Waypoint")
            return

        if inv.action == "standUp":
            self._hold(ctx)
            self.handle = None
            self.active_action = None
            fell_ok = inv.params.get("fromFall") is None or self._saw_fall
            if fell_ok and tilt_deg <= ctx.config.uprightTiltDeg:
                self.fallen = False
                self.last_completed_action = "standUp"
                self.phase = "idle"
                self._publish(ctx)
                inv.succeed()
            else:
                self.fallen = tilt_deg > ctx.config.uprightTiltDeg
                self.phase = "fallen" if self.fallen else "idle"
                self._publish(ctx)
                inv.fail("motion_timeout", "Open-loop posture did not complete fall recovery")
            return

        if ctx.config.defaultPosition is not None:
            self._apply_posture(ctx, ctx.config.defaultPosition)
        self.handle = None
        self.active_action = None
        self.phase = "idle"
        if inv.action == "bow" and not self._bow_peaked:
            self._publish(ctx)
            inv.fail("motion_timeout", "Open-loop posture did not reach bow pitch threshold")
            return
        self.last_completed_action = inv.action
        self._publish(ctx)
        inv.succeed()

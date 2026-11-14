"""Hierarchical Waypoint velocity-guided multi-policy Program for biped/v1."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from packages.info.program import Reject
from packages.library.metadata import Locations
from packages.policies.observations import command_envelope_failure
from packages.programs.biped.posture import (
    publish_biped_telemetry,
    read_biped_kinematics,
    wrap_pi,
)
from packages.programs.embodied import _clamp_posture
from packages.programs.policy_base import (
    MultiPolicyConfig,
    MultiPolicyProgram,
    PolicySlot,
    SpatialPath,
)

FallDirection = Literal["front", "back", "left", "right"]


class BipedPolicyConfig(MultiPolicyConfig):
    baseBody: SpatialPath = Field(json_schema_extra={"spatialReference": "body"})
    locations: dict = Field(default_factory=dict, json_schema_extra={"documentSchema": "locations"})
    defaultPosition: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)
    fallPostures: dict[FallDirection, list[float]] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    maxTiltDeg: float = Field(default=55.0, gt=0.0, lt=180.0, allow_inf_nan=False)
    uprightTiltDeg: float = Field(default=20.0, gt=0.0, lt=90.0, allow_inf_nan=False)
    minStandHeight: float | None = Field(
        default=None, gt=0.0, allow_inf_nan=False, exclude_if=lambda value: value is None
    )
    xyTolerance: float = Field(default=0.08, gt=0.0, allow_inf_nan=False)
    yawTolerance: float = Field(default=0.20, gt=0.0, allow_inf_nan=False)
    dwellSeconds: float = Field(default=0.25, gt=0.0, allow_inf_nan=False)
    linearGain: float = Field(default=1.5, gt=0.0, allow_inf_nan=False)
    yawGain: float = Field(default=2.0, gt=0.0, allow_inf_nan=False)
    maxLinearSpeed: float = Field(default=0.35, gt=0.0, allow_inf_nan=False)
    maxLateralSpeed: float = Field(default=0.20, ge=0.0, allow_inf_nan=False)
    maxYawRate: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)
    commandSmoothing: float = Field(default=0.35, gt=0.0, le=1.0, allow_inf_nan=False)
    headingAlignRad: float = Field(default=0.90, gt=0.0, allow_inf_nan=False)
    bowMinPitchDeg: float = Field(default=8.0, ge=0.0, lt=90.0, allow_inf_nan=False)
    bowDurationSeconds: float = Field(default=1.0, gt=0.0, allow_inf_nan=False)
    fallTimeoutSeconds: float = Field(default=4.0, gt=0.0, allow_inf_nan=False)
    maxSeconds: float = Field(default=30.0, gt=0.0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_biped_config(self):
        size = len(self.jointKeys)
        if self.defaultPosition is not None and len(self.defaultPosition) != size:
            raise ValueError("defaultPosition must match jointKeys length")
        for direction, posture in self.fallPostures.items():
            if len(posture) != size:
                raise ValueError(f"fallPostures[{direction}] must match jointKeys length")
        if self.uprightTiltDeg >= self.maxTiltDeg:
            raise ValueError("uprightTiltDeg must be strictly less than maxTiltDeg")
        Locations.model_validate(self.locations)
        return self


def _slot_command_bounds(slot: PolicySlot) -> list[tuple[float, float]] | None:
    cmd_terms = [item for item in slot.spec.observations if item.source == "command"]
    if not cmd_terms:
        return None
    width = cmd_terms[0].shape[0]
    lower = [-math.inf] * width
    upper = [math.inf] * width
    for term in cmd_terms:
        for idx, (lo, hi) in enumerate(zip(term.lower, term.upper, strict=True)):
            lower[idx] = max(lower[idx], float(lo))
            upper[idx] = min(upper[idx], float(hi))
    envelope = slot.task.get("episode", {}).get("commandEnvelope")
    if envelope is not None and len(envelope) == width:
        for idx, (lo, hi) in enumerate(envelope):
            lower[idx] = max(lower[idx], float(lo))
            upper[idx] = min(upper[idx], float(hi))
    return list(zip(lower, upper, strict=True))


class BipedPolicyProgram(MultiPolicyProgram):
    """Outer Waypoint velocity guidance + inner CPU ONNX policy dispatch for biped/v1."""

    id, type, tick_priority = "biped.policy", "biped/v1", 10
    Config = BipedPolicyConfig
    state_property = "phase"
    idle_state = "idle"
    active_state = "walking"
    fault_state = "fallen"

    def _init_robot(self, ctx):
        parsed_locations = Locations.model_validate(ctx.config.locations)
        self.waypoints = {
            name: wp.model_dump(mode="json") for name, wp in parsed_locations.waypoints.items()
        }
        p, tilt_deg, pitch_deg, _, yaw_rad, _ = read_biped_kinematics(ctx, self.base_body)
        self._latest_pos = p
        self._latest_tilt_deg = tilt_deg
        self._latest_pitch_deg = pitch_deg
        self._latest_yaw_rad = yaw_rad
        self.fallen = tilt_deg > ctx.config.maxTiltDeg
        self.phase = "fallen" if self.fallen else "idle"
        self.active_action_name: str | None = None
        self.last_completed_action: str | None = None
        self.current_waypoint: str | None = None
        self.target_waypoint: str | None = None
        self._dwell_elapsed = 0.0
        self._bow_peaked = False
        self._pre_fall_direction: str | None = None
        self._smoothed_cmd = [0.0, 0.0, 0.0]
        self._action_start_inferences = 0
        if ctx.config.defaultPosition is not None and not self.fallen:
            self._apply_posture(ctx, ctx.config.defaultPosition)

    def _apply_posture(self, ctx, posture: list[float]):
        targets = _clamp_posture(ctx.config.jointKeys, posture, self.joint_limits)
        for key, value in zip(ctx.config.jointKeys, targets, strict=True):
            ctx.physics.set_joint_target(self.joints[key], [value])

    def _pre_fall_posture(self, ctx, direction: str) -> list[float]:
        configured = ctx.config.fallPostures.get(direction)
        if configured is not None:
            return configured
        base = (
            list(ctx.config.defaultPosition)
            if ctx.config.defaultPosition is not None
            else [float(ctx.physics.joint_q(ref)[0]) for ref in self.joints.values()]
        )
        prismatic = set(ctx.config.prismaticJointKeys)
        targets = []
        for idx, (key, q0) in enumerate(zip(ctx.config.jointKeys, base, strict=True)):
            if key in prismatic:
                targets.append(q0)
                continue
            lo, hi = self.joint_limits.get(key, (None, None))
            span_hi = (hi - q0) if hi is not None else 0.7
            span_lo = (q0 - lo) if lo is not None else 0.7
            if direction == "front":
                offset = 0.85 * span_hi
            elif direction == "back":
                offset = -0.85 * span_lo
            elif direction == "left":
                offset = 0.85 * span_hi if idx % 2 == 0 else -0.85 * span_lo
            else:
                offset = -0.85 * span_lo if idx % 2 == 0 else 0.85 * span_hi
            targets.append(q0 + offset)
        return targets

    def _facts(self, ctx):
        p, tilt_deg, pitch_deg, yaw_rad = publish_biped_telemetry(
            ctx,
            self.joints,
            self.base_body,
            phase=self.phase,
            active_action=self.active_action_name,
            last_completed_action=self.last_completed_action,
            fallen=self.fallen,
            current_waypoint=self.current_waypoint,
            target_waypoint=self.target_waypoint,
        )
        self._latest_pos = p
        self._latest_tilt_deg = tilt_deg
        self._latest_pitch_deg = pitch_deg
        self._latest_yaw_rad = yaw_rad

    def _set_idle_state(self, ctx):
        self.phase = "fallen" if self.fallen else "idle"
        self.active_action_name = None
        self.target_waypoint = None
        self._pre_fall_direction = None
        self._facts(ctx)

    def _set_fault_state(self, ctx, code: str, message: str):
        if code == "robot_fallen":
            self.fallen = True
            self.last_completed_action = None
        self._set_idle_state(ctx)

    def _parameter_coverage_failure(self, inv, ctx, slot: PolicySlot) -> dict | None:
        bounds = slot.task.get("episode", {}).get("paramBounds") or {}
        if inv.action == "walkTo":
            name = inv.params.get("waypoint")
            if name not in self.waypoints:
                return {
                    "code": "unknown_waypoint",
                    "message": f"Unknown waypoint '{name}'",
                    "details": {"availableWaypoints": sorted(self.waypoints)},
                }
            if "speed" in inv.params and "speed" in bounds:
                lo, hi = bounds["speed"]
                val = float(inv.params["speed"])
                if not (float(lo) <= val <= float(hi)):
                    return {
                        "code": "precondition_failed",
                        "message": "Requested speed is outside training parameter bounds",
                        "details": {"paramBounds": {"speed": [lo, hi]}},
                    }
            envelope = slot.task.get("episode", {}).get("commandEnvelope")
            if envelope is not None:
                zero_check = command_envelope_failure(slot.task, [0.0] * len(envelope))
                if zero_check is not None:
                    return zero_check
            return None
        if inv.action == "standUp":
            if not self.fallen and inv.params.get("fromFall") is None:
                return {
                    "code": "precondition_failed",
                    "message": "Robot is already upright; pass fromFall to test fall recovery.",
                }
            return None
        if inv.action == "bow":
            if "duration" in inv.params and "duration" in bounds:
                lo, hi = bounds["duration"]
                val = float(inv.params["duration"])
                if not (float(lo) <= val <= float(hi)):
                    return {
                        "code": "precondition_failed",
                        "message": "Requested duration is outside training parameter bounds",
                        "details": {"paramBounds": {"duration": [lo, hi]}},
                    }
            return None
        return super()._parameter_coverage_failure(inv, ctx, slot)

    def _accept_action(self, inv, ctx, slot: PolicySlot) -> Reject | None:
        if inv.action == "walkTo":
            wp = self.waypoints[inv.params["waypoint"]]
            failed = ctx.check(wp.get("requires", []))
            if failed:
                return Reject(
                    code="precondition_failed",
                    message=failed[0]["message"],
                    details={"failedConditions": failed},
                )
        return None

    def _compute_walk_command(
        self, inv, ctx, slot: PolicySlot, *, update_state: bool
    ) -> list[float] | None:
        cmd_bounds = _slot_command_bounds(slot)
        if cmd_bounds is None:
            return None
        width = len(cmd_bounds)
        wp = self.waypoints[inv.params["waypoint"]]
        p, _, _, _, yaw_rad, _ = read_biped_kinematics(ctx, self.base_body)
        dx = float(wp["p"][0]) - float(p[0])
        dy = float(wp["p"][1]) - float(p[1])
        dist = math.hypot(dx, dy)
        target_yaw = float(wp["yaw"])
        final_yaw_err = wrap_pi(target_yaw - yaw_rad)

        xy_tol = float(wp.get("xyTolerance") or ctx.config.xyTolerance)
        yaw_tol = float(wp.get("yawTolerance") or ctx.config.yawTolerance)

        if dist <= xy_tol and abs(final_yaw_err) <= yaw_tol:
            if update_state:
                self._smoothed_cmd = [0.0, 0.0, 0.0]
            return [max(lo, min(hi, 0.0)) for lo, hi in cmd_bounds]

        speed_override = inv.params.get("speed")
        lin_cap = float(ctx.config.maxLinearSpeed)
        lat_cap = float(ctx.config.maxLateralSpeed)
        if speed_override is not None:
            lin_cap = min(lin_cap, float(speed_override))
            lat_cap = min(lat_cap, float(speed_override))
        yaw_cap = float(ctx.config.maxYawRate)

        if dist <= xy_tol:
            vx_des, vy_des = 0.0, 0.0
            wz_des = max(-yaw_cap, min(yaw_cap, ctx.config.yawGain * final_yaw_err))
        else:
            dx_body = math.cos(yaw_rad) * dx + math.sin(yaw_rad) * dy
            dy_body = -math.sin(yaw_rad) * dx + math.cos(yaw_rad) * dy
            bearing_err = wrap_pi(math.atan2(dy, dx) - yaw_rad)
            if dist > xy_tol * 2.5 and abs(bearing_err) > ctx.config.headingAlignRad:
                yaw_err = bearing_err
                align_scale = max(0.2, math.cos(bearing_err)) if cmd_bounds[0][0] >= -1e-6 else 1.0
            else:
                yaw_err = final_yaw_err
                align_scale = 1.0
            vx_des = max(-lin_cap, min(lin_cap, ctx.config.linearGain * dx_body * align_scale))
            vy_des = max(-lat_cap, min(lat_cap, ctx.config.linearGain * dy_body * align_scale))
            wz_des = max(-yaw_cap, min(yaw_cap, ctx.config.yawGain * yaw_err))

        alpha = float(ctx.config.commandSmoothing)
        prev = self._smoothed_cmd if update_state else [0.0, 0.0, 0.0]
        smoothed = [
            prev[0] + alpha * (vx_des - prev[0]),
            prev[1] + alpha * (vy_des - prev[1]),
            prev[2] + alpha * (wz_des - prev[2]),
        ]
        if update_state:
            self._smoothed_cmd = smoothed

        raw_vec = [smoothed[0], smoothed[2]] if width == 2 else smoothed[:width]
        if len(raw_vec) < width:
            raw_vec.extend([0.0] * (width - len(raw_vec)))
        return [
            float(max(lo, min(hi, val))) for val, (lo, hi) in zip(raw_vec, cmd_bounds, strict=True)
        ]

    def _command(self, inv, ctx, slot: PolicySlot) -> list[float] | None:
        if inv.action == "walkTo":
            return self._compute_walk_command(inv, ctx, slot, update_state=(self.handle is inv))
        cmd_bounds = _slot_command_bounds(slot)
        if cmd_bounds is None:
            return None
        if inv.action == "bow":
            bow_dur = float(inv.params.get("duration", ctx.config.bowDurationSeconds))
            in_bow = self.handle is inv and (ctx.sim_time - self.started < bow_dur)
            target_rad = math.radians(max(ctx.config.bowMinPitchDeg * 1.5, 15.0)) if in_bow else 0.0
            raw = [target_rad] + [0.0] * (len(cmd_bounds) - 1)
            return [float(max(lo, min(hi, val))) for val, (lo, hi) in zip(raw, cmd_bounds, strict=True)]
        return [float(max(lo, min(hi, 0.0))) for lo, hi in cmd_bounds]

    def _on_action_start(self, inv, ctx, slot: PolicySlot):
        self.active_action_name = inv.action
        self.last_completed_action = None
        self._dwell_elapsed = 0.0
        self._bow_peaked = False
        self._smoothed_cmd = [0.0, 0.0, 0.0]
        self._action_start_inferences = slot.model.inference_calls
        if inv.action == "walkTo":
            self.target_waypoint = inv.params["waypoint"]
            self.current_waypoint = None
            self.phase = "walking"
            self._pre_fall_direction = None
        elif inv.action == "standUp":
            self.target_waypoint = None
            self.phase = "standing_up"
            self._pre_fall_direction = inv.params.get("fromFall") if not self.fallen else None
        elif inv.action == "bow":
            self.target_waypoint = None
            self.phase = "bowing"
            self._pre_fall_direction = None
        else:
            self.target_waypoint = None
            self.phase = "walking"
            self._pre_fall_direction = None
        self._facts(ctx)

    def _tick_robot(self, ctx, dt: float) -> None:
        p, tilt_deg, pitch_deg, _, yaw_rad, _ = read_biped_kinematics(ctx, self.base_body)
        self._latest_pos = p
        self._latest_tilt_deg = tilt_deg
        self._latest_pitch_deg = pitch_deg
        self._latest_yaw_rad = yaw_rad

        inv = self.handle
        if inv is None:
            if not self.fallen and tilt_deg > ctx.config.maxTiltDeg:
                self.fallen = True
                self.last_completed_action = None
                self.phase = "fallen"
                self._hold(ctx)
                self._facts(ctx)
            return

        if inv.action == "walkTo":
            if tilt_deg <= ctx.config.maxTiltDeg and self.target_waypoint in self.waypoints:
                wp = self.waypoints[self.target_waypoint]
                xy_tol = float(wp.get("xyTolerance") or ctx.config.xyTolerance)
                yaw_tol = float(wp.get("yawTolerance") or ctx.config.yawTolerance)
                dist = math.hypot(float(wp["p"][0]) - float(p[0]), float(wp["p"][1]) - float(p[1]))
                dyaw = abs(wrap_pi(float(wp["yaw"]) - float(yaw_rad)))
                if dist <= xy_tol and dyaw <= yaw_tol:
                    self._dwell_elapsed += dt
                else:
                    self._dwell_elapsed = 0.0
            else:
                self._dwell_elapsed = 0.0
            return

        if inv.action == "standUp":
            if self._pre_fall_direction is not None:
                if tilt_deg >= ctx.config.maxTiltDeg:
                    self.fallen = True
                    self._pre_fall_direction = None
                    self._dwell_elapsed = 0.0
                    self.next_inference = ctx.sim_time
                    self._facts(ctx)
                return
            height_ok = (
                ctx.config.minStandHeight is None or float(p[2]) >= float(ctx.config.minStandHeight)
            )
            if tilt_deg <= ctx.config.uprightTiltDeg and height_ok:
                self._dwell_elapsed += dt
            else:
                self._dwell_elapsed = 0.0
            return

        if inv.action == "bow":
            if ctx.config.bowMinPitchDeg == 0.0 or pitch_deg >= ctx.config.bowMinPitchDeg:
                if not self._bow_peaked:
                    self._bow_peaked = True
                    self._facts(ctx)
            returned_upright = tilt_deg <= ctx.config.uprightTiltDeg and (
                ctx.config.bowMinPitchDeg == 0.0 or abs(pitch_deg) < ctx.config.bowMinPitchDeg * 0.75
            )
            if self._bow_peaked and returned_upright:
                self._dwell_elapsed += dt
            else:
                self._dwell_elapsed = 0.0

    def _fault_failure(self, inv, ctx, slot: PolicySlot) -> tuple[str, str] | None:
        if inv.action == "standUp":
            if (
                self._pre_fall_direction is not None
                and ctx.sim_time - self.started >= ctx.config.fallTimeoutSeconds
            ):
                return (
                    "fall_induction_timeout",
                    "Controlled fall posture did not reach maxTiltDeg within fallTimeoutSeconds",
                )
            return None
        if self._latest_tilt_deg > ctx.config.maxTiltDeg:
            self.fallen = True
            msg = (
                f"Torso tilt {self._latest_tilt_deg:.1f} deg exceeded "
                f"safety limit {ctx.config.maxTiltDeg:.1f} deg"
            )
            return ("robot_fallen", msg)
        return None

    def _action_completed(self, inv, ctx, slot: PolicySlot) -> bool:
        if slot.model.inference_calls <= self._action_start_inferences:
            return False
        if inv.action == "walkTo":
            return not self.fallen and self._dwell_elapsed + 1e-9 >= ctx.config.dwellSeconds
        if inv.action == "standUp":
            return self._pre_fall_direction is None and self._dwell_elapsed + 1e-9 >= ctx.config.dwellSeconds
        if inv.action == "bow":
            return (
                not self.fallen
                and self._bow_peaked
                and self._dwell_elapsed + 1e-9 >= ctx.config.dwellSeconds
            )
        return super()._action_completed(inv, ctx, slot)

    def _step_active_action(self, inv, ctx, slot: PolicySlot, dt: float) -> bool:
        if inv.action == "standUp" and self._pre_fall_direction is not None:
            self._apply_posture(ctx, self._pre_fall_posture(ctx, self._pre_fall_direction))
            return True
        if inv.action == "bow":
            bow_dur = float(inv.params.get("duration", ctx.config.bowDurationSeconds))
            if (
                self._bow_peaked
                and ctx.sim_time - self.started >= bow_dur
                and _slot_command_bounds(slot) is None
                and ctx.config.defaultPosition is not None
            ):
                self._apply_posture(ctx, ctx.config.defaultPosition)
                return True
        return False

    def _on_action_complete(self, inv, ctx, slot: PolicySlot):
        if inv.action == "walkTo":
            self.current_waypoint = self.target_waypoint
        elif inv.action == "standUp":
            self.fallen = False
        self.last_completed_action = inv.action
        self._set_idle_state(ctx)

    def _sync_interrupted_fall_state(self, ctx):
        _, tilt_deg, _, _, _, _ = read_biped_kinematics(ctx, self.base_body)
        self._latest_tilt_deg = tilt_deg
        self.fallen = tilt_deg > ctx.config.uprightTiltDeg
        if self.fallen:
            self.last_completed_action = None
        self._set_idle_state(ctx)

    def _on_cancel(self, inv, ctx):
        self._sync_interrupted_fall_state(ctx)

    def _on_stop(self, inv, ctx):
        self._sync_interrupted_fall_state(ctx)

    def _on_timeout(self, inv, ctx, slot: PolicySlot):
        if inv.action == "standUp" and self._latest_tilt_deg > ctx.config.uprightTiltDeg:
            self.fallen = True
            self.last_completed_action = None
        self._set_idle_state(ctx)

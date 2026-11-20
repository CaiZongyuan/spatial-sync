"""Hierarchical Waypoint velocity-guided multi-policy Program for biped/v1."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from packages.info.program import Reject
from packages.library.metadata import Locations
from packages.policies.observations import (
    assemble,
    command_envelope_failure,
    joint_targets,
    observation_canonical_refs,
)
from packages.programs.biped.posture import (
    advance_collapse_elapsed,
    compute_fall_posture,
    is_collapsed_height,
    is_interrupted_fallen,
    publish_biped_telemetry,
    read_biped_calm_speeds,
    read_biped_kinematics,
    wrap_pi,
)
from packages.programs.embodied import ReferencePlacement, TrajectoryCheck, _clamp_posture
from packages.programs.policy_base import (
    MultiPolicyConfig,
    MultiPolicyProgram,
    PolicySlot,
    SpatialPath,
    extract_observation_state,
)
from packages.programs.waypoint_guidance import slot_command_bounds, waypoint_command
from packages.training.declarative import trajectory_completion

FallDirection = Literal["front", "back", "left", "right"]


class BipedPolicyConfig(MultiPolicyConfig):
    referencePlacement: ReferencePlacement | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
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
    standMaxLinearSpeed: float = Field(
        default=0.06, gt=0.0, allow_inf_nan=False, exclude_if=lambda value: value == 0.06
    )
    standMaxAngularSpeed: float = Field(
        default=0.50, gt=0.0, allow_inf_nan=False, exclude_if=lambda value: value == 0.50
    )
    fallMinTiltDeg: float | None = Field(
        default=None, gt=0.0, lt=180.0, allow_inf_nan=False, exclude_if=lambda value: value is None
    )
    fallSettleSeconds: float = Field(
        default=0.0, ge=0.0, allow_inf_nan=False, exclude_if=lambda value: value == 0.0
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
        if self.fallMinTiltDeg is not None and self.fallMinTiltDeg < self.maxTiltDeg:
            raise ValueError("fallMinTiltDeg must be >= maxTiltDeg")
        Locations.model_validate(self.locations)
        return self




class BipedPolicyProgram(MultiPolicyProgram):
    """Outer Waypoint velocity guidance + inner CPU ONNX policy dispatch for biped/v1."""

    id, type, tick_priority = "biped.policy", "biped/v1", 10
    Config = BipedPolicyConfig
    state_property = "phase"
    idle_state = "idle"
    active_state = "walking"
    fault_state = "fallen"

    def _init_robot(self, ctx):
        self.trajectories = {
            action: (completion, TrajectoryCheck(ctx, slot, self.joints, self.joint_units))
            for action, slot in self.slots.items() if (completion := trajectory_completion(slot.task))
        }
        parsed_locations = Locations.model_validate(ctx.config.locations)
        self.waypoints = {
            name: wp.model_dump(mode="json") for name, wp in parsed_locations.waypoints.items()
        }
        p, tilt_deg, pitch_deg, _, yaw_rad, _ = read_biped_kinematics(ctx, self.base_body)
        self._latest_pos = p
        self._latest_tilt_deg = tilt_deg
        self._latest_pitch_deg = pitch_deg
        self._latest_yaw_rad = yaw_rad
        min_h = ctx.config.minStandHeight
        self.fallen = (
            tilt_deg > ctx.config.maxTiltDeg
            or is_collapsed_height(ctx, float(p[2]), tilt_deg)
            or (
                min_h is not None
                and float(p[2]) < float(min_h)
                and tilt_deg > ctx.config.uprightTiltDeg
            )
        )
        self.phase = "fallen" if self.fallen else "idle"
        self.active_action_name: str | None = None
        self.last_completed_action: str | None = None
        self.current_waypoint: str | None = None
        self.target_waypoint: str | None = None
        self._dwell_elapsed = 0.0
        self._collapse_elapsed = 0.0
        self._bow_peaked = False
        self._pre_fall_direction: str | None = None
        self._fall_tilt_reached_at: float | None = None
        self._smoothed_cmd = [0.0, 0.0, 0.0]
        self._action_start_inferences = 0
        self._idle_slot: PolicySlot | None = None
        self._idle_previous: np.ndarray | None = None
        self._next_idle_inference = 0.0
        if not self.fallen:
            if ctx.config.defaultPosition is not None:
                self._apply_posture(ctx, ctx.config.defaultPosition)
            self._arm_idle_balance(ctx)

    def _apply_posture(self, ctx, posture: list[float]):
        targets = _clamp_posture(ctx.config.jointKeys, posture, self.joint_limits)
        for key, value in zip(ctx.config.jointKeys, targets, strict=True):
            ctx.physics.set_joint_target(self.joints[key], [value])

    def _pre_fall_posture(self, ctx, direction: str) -> list[float]:
        return compute_fall_posture(ctx, self.joints, self.joint_limits, direction)

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
        self._fall_tilt_reached_at = None
        self._facts(ctx)

    def _set_fault_state(self, ctx, code: str, message: str):
        if code == "robot_fallen":
            self.fallen = True
            self.last_completed_action = None
        self._set_idle_state(ctx)

    def _parameter_coverage_failure(self, inv, ctx, slot: PolicySlot) -> dict | None:
        if inv.action in self.trajectories:
            return super()._parameter_coverage_failure(inv, ctx, slot)
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
        cmd_bounds = slot_command_bounds(slot)
        if cmd_bounds is None:
            return None
        wp = self.waypoints[inv.params["waypoint"]]
        p, _, _, _, yaw_rad, _ = read_biped_kinematics(ctx, self.base_body)
        command, smoothed = waypoint_command(
            wp, p, yaw_rad, cmd_bounds, ctx.config, inv.params.get("speed"),
            self._smoothed_cmd if update_state else [0.0, 0.0, 0.0],
        )
        if update_state:
            self._smoothed_cmd = smoothed
        return command

    def _command(self, inv, ctx, slot: PolicySlot) -> list[float] | None:
        if inv.action in self.trajectories:
            keys = ctx.config.commandParams.get(inv.action)
            return [float(inv.params[key]) for key in keys] if keys else None
        if inv.action == "walkTo":
            return self._compute_walk_command(inv, ctx, slot, update_state=(self.handle is inv))
        cmd_bounds = slot_command_bounds(slot)
        if cmd_bounds is None:
            return None
        if inv.action == "bow":
            bow_dur = float(inv.params.get("duration", ctx.config.bowDurationSeconds))
            in_bow = self.handle is inv and (ctx.sim_time - self.started < bow_dur)
            target_rad = math.radians(max(ctx.config.bowMinPitchDeg * 1.5, 15.0)) if in_bow else 0.0
            raw = [target_rad] + [0.0] * (len(cmd_bounds) - 1)
            return [float(max(lo, min(hi, val))) for val, (lo, hi) in zip(raw, cmd_bounds, strict=True)]
        return [float(max(lo, min(hi, 0.0))) for lo, hi in cmd_bounds]

    def _select_idle_balance_slot(self) -> PolicySlot | None:
        ordered: list[PolicySlot] = []
        for preferred in ("walkTo", "standUp", "bow"):
            cand = self.slots.get(preferred)
            if cand is not None:
                ordered.append(cand)
        for cand in self.slots.values():
            if cand not in ordered:
                ordered.append(cand)
        for cand in ordered:
            if cand.action in self.trajectories:
                continue
            needs_ref = False
            for obs_term in cand.spec.observations:
                _, keys, frame_body = observation_canonical_refs(obs_term)
                if any(k.startswith("$") for k in keys) or (
                    frame_body is not None and frame_body.startswith("$")
                ):
                    needs_ref = True
                    break
            if needs_ref:
                continue
            bounds = slot_command_bounds(cand)
            if bounds is not None:
                if any(not (lo <= 0.0 <= hi) for lo, hi in bounds):
                    continue
                if command_envelope_failure(cand.task, [0.0] * len(bounds)) is not None:
                    continue
            return cand
        return None

    def _arm_idle_balance(self, ctx, previous_slot: PolicySlot | None = None) -> None:
        if self.fallen:
            self._idle_slot = None
            self._idle_previous = None
            return
        slot = self._select_idle_balance_slot()
        self._idle_slot = slot
        if slot is None:
            self._idle_previous = None
            return
        if previous_slot is slot:
            self._idle_previous = slot.previous.copy()
        else:
            self._idle_previous = np.zeros(len(slot.spec.action.jointKeys), dtype=np.float64)
        self._next_idle_inference = ctx.sim_time
        self._step_idle_balance(ctx)

    def _step_idle_balance(self, ctx) -> None:
        slot = self._idle_slot
        if slot is None or self._idle_previous is None:
            return
        if ctx.sim_time + 1e-10 < self._next_idle_inference:
            return
        try:
            bounds = slot_command_bounds(slot)
            cmd = [0.0] * len(bounds) if bounds is not None else None
            obs = assemble(
                slot.spec,
                extract_observation_state(ctx.physics, slot.spec, {}),
                previous_action=self._idle_previous,
                command=cmd,
            )
            inputs = obs.astype(np.float32)[None, :]
            if not np.isfinite(inputs).all():
                return
            raw_out = slot.model.session.run([slot.model.output], {slot.model.input: inputs})[0]
            output = np.asarray(raw_out[0], dtype=np.float64)
            if output.shape != (slot.model.actions,) or not np.isfinite(output).all():
                return
            positions = [
                ctx.physics.joint_q(self.joints[key])[0] for key in slot.spec.action.jointKeys
            ]
            targets = joint_targets(slot.spec, positions, output)
            for key, raw_target in zip(slot.spec.action.jointKeys, targets, strict=True):
                value = float(raw_target)
                phys_lo, phys_hi = self.joint_limits.get(key, (None, None))
                if phys_lo is not None:
                    value = max(phys_lo, value)
                if phys_hi is not None:
                    value = min(phys_hi, value)
                ctx.physics.set_joint_target(self.joints[key], [value])
        except (ValueError, KeyError, RuntimeError):
            return
        self._idle_previous = output.copy()
        self._next_idle_inference = ctx.sim_time + 1.0 / float(slot.task["control"]["hz"])

    def _on_action_start(self, inv, ctx, slot: PolicySlot):
        self._idle_slot = None
        self._idle_previous = None
        self.active_action_name = inv.action
        self.last_completed_action = None
        self._dwell_elapsed = 0.0
        self._collapse_elapsed = 0.0
        self._bow_peaked = False
        self._fall_tilt_reached_at = None
        self._smoothed_cmd = [0.0, 0.0, 0.0]
        self._action_start_inferences = slot.model.inference_calls
        if trajectory := self.trajectories.get(inv.action):
            trajectory[1].reset()
            self.target_waypoint = None
            self._pre_fall_direction = None
        elif inv.action == "walkTo":
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
        p, tilt_deg, pitch_deg, _, yaw_rad, v_body = read_biped_kinematics(ctx, self.base_body)
        self._latest_pos = p
        self._latest_tilt_deg = tilt_deg
        self._latest_pitch_deg = pitch_deg
        self._latest_yaw_rad = yaw_rad

        inv = self.handle
        if inv is None:
            self._collapse_elapsed = advance_collapse_elapsed(
                ctx, float(p[2]), tilt_deg, self._collapse_elapsed, dt
            )
            collapsed = is_collapsed_height(ctx, float(p[2]), tilt_deg, self._collapse_elapsed)
            if not self.fallen and (tilt_deg > ctx.config.maxTiltDeg or collapsed):
                self.fallen = True
                self.last_completed_action = None
                self.phase = "fallen"
                self._idle_slot = None
                self._idle_previous = None
                self._hold(ctx)
                self._facts(ctx)
                return
            if not self.fallen:
                self._step_idle_balance(ctx)
            return

        if trajectory := self.trajectories.get(inv.action):
            completion, tracker = trajectory
            self._dwell_elapsed = tracker.sample(ctx, self.base_body, ctx.sim_time - self.started, completion)
            self.fallen = tracker.fallen
            return

        if inv.action != "standUp":
            self._collapse_elapsed = advance_collapse_elapsed(
                ctx, float(p[2]), tilt_deg, self._collapse_elapsed, dt
            )

        height_ok = (
            ctx.config.minStandHeight is None or float(p[2]) >= float(ctx.config.minStandHeight)
        )

        if inv.action == "walkTo":
            if tilt_deg <= ctx.config.maxTiltDeg and height_ok and self.target_waypoint in self.waypoints:
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
                target_fall_tilt = float(
                    ctx.config.fallMinTiltDeg
                    if ctx.config.fallMinTiltDeg is not None
                    else ctx.config.maxTiltDeg
                )
                if self._fall_tilt_reached_at is None and tilt_deg >= target_fall_tilt:
                    self._fall_tilt_reached_at = ctx.sim_time
                elif self._fall_tilt_reached_at is not None and tilt_deg < ctx.config.maxTiltDeg:
                    self._fall_tilt_reached_at = None
                if (
                    self._fall_tilt_reached_at is not None
                    and ctx.sim_time - self._fall_tilt_reached_at + 1e-9
                    >= ctx.config.fallSettleSeconds
                ):
                    self.fallen = True
                    self.last_completed_action = None
                    self._pre_fall_direction = None
                    self._fall_tilt_reached_at = None
                    self._dwell_elapsed = 0.0
                    if self.active_slot is not None:
                        self._action_start_inferences = self.active_slot.model.inference_calls
                    self.next_inference = ctx.sim_time
                    self._facts(ctx)
                return
            lin_speed, ang_speed = read_biped_calm_speeds(ctx, self.base_body, v_body)
            calm_ok = (
                lin_speed <= ctx.config.standMaxLinearSpeed
                and ang_speed <= ctx.config.standMaxAngularSpeed
            )
            if tilt_deg <= ctx.config.uprightTiltDeg and height_ok and calm_ok:
                self._dwell_elapsed += dt
            else:
                self._dwell_elapsed = 0.0
            return

        if inv.action == "bow":
            if ctx.config.bowMinPitchDeg == 0.0 or pitch_deg >= ctx.config.bowMinPitchDeg:
                if not self._bow_peaked:
                    self._bow_peaked = True
                    self._facts(ctx)
            returned_upright = (
                tilt_deg <= ctx.config.uprightTiltDeg
                and height_ok
                and (
                    ctx.config.bowMinPitchDeg == 0.0
                    or abs(pitch_deg) < ctx.config.bowMinPitchDeg * 0.75
                )
            )
            if self._bow_peaked and returned_upright:
                self._dwell_elapsed += dt
            else:
                self._dwell_elapsed = 0.0

    def _fault_failure(self, inv, ctx, slot: PolicySlot) -> tuple[str, str] | None:
        if trajectory := self.trajectories.get(inv.action):
            if self.fallen:
                return "robot_fallen", "Base height or tilt left the commissioned range"
            if trajectory[1].failed:
                return "motion_tracking_failed", "Measured pose left the pinned trajectory tolerance"
            return None
        if inv.action == "standUp":
            if (
                self._pre_fall_direction is not None
                and ctx.sim_time - self.started >= ctx.config.fallTimeoutSeconds
            ):
                return (
                    "fall_induction_timeout",
                    "Controlled fall posture did not reach and settle at target fall tilt "
                    "within fallTimeoutSeconds",
                )
            return None
        if self._latest_tilt_deg > ctx.config.maxTiltDeg:
            self.fallen = True
            msg = (
                f"Torso tilt {self._latest_tilt_deg:.1f} deg exceeded "
                f"safety limit {ctx.config.maxTiltDeg:.1f} deg"
            )
            return ("robot_fallen", msg)
        if is_collapsed_height(
            ctx, float(self._latest_pos[2]), self._latest_tilt_deg, self._collapse_elapsed
        ):
            self.fallen = True
            msg = (
                f"Base height {float(self._latest_pos[2]):.3f} m collapsed below "
                f"standing threshold {float(ctx.config.minStandHeight):.3f} m"
            )
            return ("robot_fallen", msg)
        return None

    def _action_completed(self, inv, ctx, slot: PolicySlot) -> bool:
        if slot.model.inference_calls <= self._action_start_inferences:
            return False
        if trajectory := self.trajectories.get(inv.action):
            return not self.fallen and self._dwell_elapsed >= trajectory[0].dwellSeconds
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
        if inv.action in self.trajectories:
            return False
        if inv.action == "standUp" and self._pre_fall_direction is not None:
            self._apply_posture(ctx, self._pre_fall_posture(ctx, self._pre_fall_direction))
            return True
        if inv.action == "bow":
            bow_dur = float(inv.params.get("duration", ctx.config.bowDurationSeconds))
            if (
                self._bow_peaked
                and ctx.sim_time - self.started >= bow_dur
                and slot_command_bounds(slot) is None
                and ctx.config.defaultPosition is not None
            ):
                self._apply_posture(ctx, ctx.config.defaultPosition)
                return True
        return False

    def _on_action_complete(self, inv, ctx, slot: PolicySlot):
        if inv.action not in self.trajectories and inv.action == "walkTo":
            self.current_waypoint = self.target_waypoint
        elif inv.action not in self.trajectories and inv.action == "standUp":
            self.fallen = False
        self._collapse_elapsed = 0.0
        self.last_completed_action = inv.action
        self._set_idle_state(ctx)
        self._arm_idle_balance(ctx, previous_slot=slot)

    def _sync_interrupted_fall_state(self, ctx):
        p, tilt_deg, _, _, _, _ = read_biped_kinematics(ctx, self.base_body)
        self._latest_pos = p
        self._latest_tilt_deg = tilt_deg
        self._collapse_elapsed = 0.0
        self.fallen = is_interrupted_fallen(ctx, float(p[2]), tilt_deg)
        if self.fallen:
            self.last_completed_action = None
        self._set_idle_state(ctx)
        self._arm_idle_balance(ctx, previous_slot=self._last_slot)

    def _on_cancel(self, inv, ctx):
        self._sync_interrupted_fall_state(ctx)

    def _on_stop(self, inv, ctx):
        self._sync_interrupted_fall_state(ctx)

    def _on_timeout(self, inv, ctx, slot: PolicySlot):
        self._collapse_elapsed = 0.0
        if inv.action == "standUp" and is_interrupted_fallen(
            ctx, float(self._latest_pos[2]), self._latest_tilt_deg
        ):
            self.fallen = True
            self.last_completed_action = None
        self._set_idle_state(ctx)
        self._arm_idle_balance(ctx, previous_slot=slot)

"""Explicit, engine-free state observations and bounded joint position actions."""

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.info.schema_subset import json_equal
from packages.protocol.math import rotate_vector


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Observation(Schema):
    name: str = Field(min_length=1)
    source: Literal[
        "joint_position",
        "joint_velocity",
        "body_position",
        "body_orientation",
        "body_angular_velocity",
        "projected_gravity",
        "site_position",
        "site_orientation",
        "previous_action",
        "command",
    ]
    keys: list[str] = Field(default_factory=list, max_length=64)
    key: str | None = Field(default=None, min_length=1)
    frame: str | None = Field(default=None, exclude_if=lambda value: value is None)
    unit: Literal["rad", "rad/s", "m", "m/s", "xyzw", "1"]
    shape: list[int]
    offset: list[float]
    scale: list[float]
    lower: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)
    upper: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def dimensions(self):
        joint = self.source.startswith("joint_")
        vector_term = self.source in ("previous_action", "command")
        if joint:
            size = len(self.keys)
        elif vector_term:
            size = self.shape[0] if len(self.shape) == 1 else 0
        else:
            size = 4 if self.source.endswith("orientation") else 3
        units = {
            "joint_position": {"rad", "m"},
            "joint_velocity": {"rad/s", "m/s"},
            "body_angular_velocity": {"rad/s"},
            "projected_gravity": {"1"},
            "previous_action": {"1"},
            "command": {"1", "m", "m/s", "rad", "rad/s"},
        }
        allowed_units = units.get(self.source, {"xyzw"} if size == 4 else {"m"})
        if (
            size < 1
            or self.shape != [size]
            or len(self.offset) != size
            or len(self.scale) != size
            or self.unit not in allowed_units
            or any(value == 0 for value in self.scale)
        ):
            raise ValueError("Observation shape, unit, offset and scale must match its source")
        if joint:
            if (
                self.key is not None
                or len(set(self.keys)) != size
                or not all(value.startswith("/") for value in self.keys)
            ):
                raise ValueError("Joint observations require distinct ordered keys")
        elif self.keys or (self.key is None) != vector_term:
            raise ValueError("Pose/body observations require key; vector observations have no keys")
        if self.key is not None:
            body_ref = self.source.startswith("body_") or self.source == "projected_gravity"
            aliases = {"$object"} if body_ref else {"$from", "$to"}
            if not self.key.startswith("/") and self.key not in aliases:
                raise ValueError("Pose key must be canonical or a supported body/Site reference")
        if self.source == "command":
            if (
                self.lower is None
                or self.upper is None
                or len(self.lower) != size
                or len(self.upper) != size
                or any(lo > hi for lo, hi in zip(self.lower, self.upper, strict=True))
            ):
                raise ValueError("command observation requires ordered finite lower and upper bounds")
        elif self.lower is not None or self.upper is not None:
            raise ValueError("Only command observations declare lower and upper bounds")
        if self.source == "projected_gravity":
            if self.frame is not None and self.frame != self.key:
                raise ValueError("projected_gravity frame must be omitted or match key")
        else:
            if not self.frame:
                raise ValueError("Observation frame must be non-empty")
            if joint or self.source in ("previous_action", "body_orientation", "site_orientation"):
                if self.frame != "world":
                    raise ValueError("Observation source only supports the world frame")
            elif self.source == "command":
                if self.frame not in ("world", "body"):
                    raise ValueError("command frame must be 'world' or 'body'")
            elif self.source == "body_angular_velocity":
                if (
                    self.frame not in ("world", "body")
                    and not self.frame.startswith("/")
                    and self.frame != "$object"
                ):
                    raise ValueError(
                        "Angular velocity frame must be 'world', 'body', a canonical body or $object"
                    )
            elif self.frame != "world" and not self.frame.startswith("/") and self.frame != "$object":
                raise ValueError("Relative frame must identify a canonical body or $object")
        return self


class Action(Schema):
    space: Literal["joint_position_delta", "joint_position_offset"]
    jointKeys: list[str] = Field(min_length=1, max_length=64)
    units: list[Literal["rad", "m"]]
    defaultPosition: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)
    scale: list[float]
    lower: list[float]
    upper: list[float]
    maxDelta: list[float] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def dimensions(self):
        size = len(self.jointKeys)
        if (
            len(set(self.jointKeys)) != size
            or not all(self.jointKeys)
            or any(len(values) != size for values in (self.units, self.scale, self.lower, self.upper))
            or any(lo >= hi for lo, hi in zip(self.lower, self.upper, strict=True))
            or any(value == 0 for value in self.scale)
        ):
            raise ValueError("Action requires distinct joints and finite ordered limits/scales")
        if self.space == "joint_position_delta":
            if self.defaultPosition is not None or self.maxDelta is None:
                raise ValueError("joint_position_delta requires maxDelta and forbids defaultPosition")
        else:
            if self.defaultPosition is None or len(self.defaultPosition) != size:
                raise ValueError("joint_position_offset requires defaultPosition matching jointKeys")
            if any(
                not (lo <= default <= hi)
                for lo, default, hi in zip(self.lower, self.defaultPosition, self.upper, strict=True)
            ):
                raise ValueError("defaultPosition must lie within [lower, upper]")
        if self.maxDelta is not None and (
            len(self.maxDelta) != size or any(value <= 0 for value in self.maxDelta)
        ):
            raise ValueError("maxDelta must contain positive per-joint limits")
        return self


class ObservationSpec(Schema):
    schema_version: Literal["lw.obs-spec/1"] = Field(alias="schema")
    observations: list[Observation] = Field(min_length=1, max_length=128)
    action: Action

    @model_validator(mode="after")
    def names(self):
        if len({item.name for item in self.observations}) != len(self.observations):
            raise ValueError("Observation names must be distinct")
        if any(
            item.source == "previous_action" and item.shape != [len(self.action.jointKeys)]
            for item in self.observations
        ):
            raise ValueError("previous_action must match the ONNX output dimension")
        return self

    @property
    def size(self):
        return sum(item.shape[0] for item in self.observations)

    def assemble(self, state, *, previous_action, command=None, references=None):
        return assemble(
            self,
            state,
            previous_action=previous_action,
            command=command,
            references=references,
        )


def vector(value, size):
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError(f"Expected finite vector of shape ({size},)")
    return result


def _unit_quaternion(value, label="Frame"):
    q = vector(value, 4)
    if abs(np.linalg.norm(q) - 1) > 1e-5:
        raise ValueError(f"{label} must have a unit xyzw quaternion")
    return q


def assemble(spec, state, *, previous_action, command=None, references=None):
    """State has joints:{key:{q,qd}}, bodies:{key:{p,q,w?}}, sites:{key:{p,q}} in Newton coordinates."""
    references = references or {}

    def key(value):
        return references[value] if value.startswith("$") else value

    result = []
    for item in spec.observations:
        source = item.source
        if source == "previous_action":
            raw = vector(previous_action, item.shape[0])
        elif source == "command":
            if command is None:
                raise ValueError(f"{item.name} requires a command vector")
            raw = vector(command, item.shape[0])
            if any(
                not (lo <= float(val) <= hi)
                for val, lo, hi in zip(raw, item.lower, item.upper, strict=True)
            ):
                raise ValueError(f"{item.name} command is outside declared [lower, upper] bounds")
        elif source.startswith("joint_"):
            field = "q" if source == "joint_position" else "qd"
            raw = vector([state["joints"][key(name)][field] for name in item.keys], len(item.keys))
        elif source == "body_angular_velocity":
            body_name = key(item.key)
            raw = vector(state["bodies"][body_name]["w"], 3)
            if item.frame != "world":
                frame_name = body_name if item.frame == "body" else key(item.frame)
                q = _unit_quaternion(state["bodies"][frame_name]["q"])
                raw = np.asarray(rotate_vector([-q[0], -q[1], -q[2], q[3]], raw))
        elif source == "projected_gravity":
            body_name = key(item.key)
            q = _unit_quaternion(state["bodies"][body_name]["q"], "Orientation")
            gravity = np.array([0.0, 0.0, -1.0], dtype=np.float64)
            raw = np.asarray(rotate_vector([-q[0], -q[1], -q[2], q[3]], gravity))
        else:
            pose = state["bodies" if source.startswith("body_") else "sites"][key(item.key)]
            raw = vector(pose["q" if source.endswith("orientation") else "p"], item.shape[0])
            if source.endswith("orientation") and abs(np.linalg.norm(raw) - 1) > 1e-5:
                raise ValueError("Orientation must be a unit xyzw quaternion")
            if source.endswith("position") and item.frame != "world":
                frame = state["bodies"][key(item.frame)]
                q = _unit_quaternion(frame["q"])
                raw = np.asarray(rotate_vector([-q[0], -q[1], -q[2], q[3]], raw - vector(frame["p"], 3)))
        result.extend((raw - np.asarray(item.offset)) * np.asarray(item.scale))
    result = np.asarray(result, dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Observation exceeds finite float32 range")
    return result


def _numeric_in_bounds(value, lower, upper):
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and math.isfinite(value)
        and float(lower) <= float(value) <= float(upper)
    )


def coverage_failure(task, action, params):
    """Evaluation grids never widen the known training episode parameter coverage."""
    episode = task["episode"]
    trained = episode.get("params", {})
    bounds = episode.get("paramBounds")
    expected_action = task["implements"].rsplit(".", 1)[1]
    details = {
        "trainedAction": task["implements"],
        "trainedParams": trained,
        **({"paramBounds": bounds} if bounds else {}),
    }
    def reject():
        return {
            "code": "precondition_failed",
            "message": "Action parameters exceed training coverage",
            "details": details,
        }

    if action != expected_action or not isinstance(params, dict):
        return reject()
    if not bounds:
        return None if json_equal(params, trained) else reject()
    if not set(params) <= (set(trained) | set(bounds)):
        return reject()
    for name, expected in trained.items():
        if name not in bounds and (name not in params or not json_equal(params[name], expected)):
            return reject()
    for name, (lower, upper) in bounds.items():
        if name not in params:
            if name not in trained or not _numeric_in_bounds(trained[name], lower, upper):
                return reject()
        elif not _numeric_in_bounds(params[name], lower, upper):
            return reject()
    return None


def command_envelope_failure(task, command):
    """Verify runtime command vectors stay inside the Task's declared commandEnvelope."""
    envelope = task.get("episode", {}).get("commandEnvelope")
    if envelope is None:
        return None
    try:
        values = vector(command, len(envelope))
    except (ValueError, TypeError):
        return {
            "code": "precondition_failed",
            "message": "Action command exceeds training command envelope",
            "details": {"commandEnvelope": envelope},
        }
    if any(
        not (float(lower) <= float(val) <= float(upper))
        for val, (lower, upper) in zip(values, envelope, strict=True)
    ):
        return {
            "code": "precondition_failed",
            "message": "Action command exceeds training command envelope",
            "details": {"commandEnvelope": envelope},
        }
    return None


def joint_targets(spec, positions, output):
    size = len(spec.action.jointKeys)
    current, raw = vector(positions, size), vector(output, size)
    if spec.action.space == "joint_position_delta":
        limit = np.asarray(spec.action.maxDelta, dtype=np.float64)
        delta = np.clip(raw * spec.action.scale, -limit, limit)
        return np.clip(current + delta, spec.action.lower, spec.action.upper)
    target = np.asarray(spec.action.defaultPosition, dtype=np.float64) + raw * np.asarray(
        spec.action.scale, dtype=np.float64
    )
    if spec.action.maxDelta is not None:
        limit = np.asarray(spec.action.maxDelta, dtype=np.float64)
        target = current + np.clip(target - current, -limit, limit)
    return np.clip(target, spec.action.lower, spec.action.upper)


def sim_gap(train_success, platform_success):
    """Rates are fractions; strictly more than fifteen percentage points is a gap."""
    if train_success is None or platform_success is None:
        return None
    if isinstance(train_success, bool) or isinstance(platform_success, bool):
        raise ValueError("Success rates must be numeric fractions")
    values = vector([train_success, platform_success], 2)
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("Success rates must be fractions in [0,1]")
    # Decimal text avoids classifying e.g. .9-.75 as greater than .15 by float roundoff.
    from decimal import Decimal

    return Decimal(str(train_success)) - Decimal(str(platform_success)) > Decimal("0.15")

"""Explicit, engine-free state observations and bounded joint delta actions."""

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
        "site_position",
        "site_orientation",
        "previous_action",
    ]
    keys: list[str] = Field(default_factory=list, max_length=64)
    key: str | None = Field(default=None, min_length=1)
    frame: str
    unit: Literal["rad", "rad/s", "m", "m/s", "xyzw", "1"]
    shape: list[int]
    offset: list[float]
    scale: list[float]

    @model_validator(mode="after")
    def dimensions(self):
        joint = self.source.startswith("joint_")
        previous = self.source == "previous_action"
        if joint:
            size = len(self.keys)
        elif previous:
            size = self.shape[0] if len(self.shape) == 1 else 0
        else:
            size = 4 if self.source.endswith("orientation") else 3
        units = {"joint_position": {"rad", "m"}, "joint_velocity": {"rad/s", "m/s"}, "previous_action": {"1"}}
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
        elif self.keys or (self.key is None) != previous:
            raise ValueError("Pose observations require key; previous_action has no keys")
        if self.key is not None:
            aliases = {"$object"} if self.source.startswith("body_") else {"$from", "$to"}
            if not self.key.startswith("/") and self.key not in aliases:
                raise ValueError("Pose key must be canonical or a supported body/Site reference")
        if not self.frame or (not self.source.endswith("position") or joint) and self.frame != "world":
            raise ValueError("Only Cartesian positions support a relative body frame")
        if self.frame != "world" and not self.frame.startswith("/") and self.frame != "$object":
            raise ValueError("Relative frame must identify a canonical body or $object")
        return self


class Action(Schema):
    space: Literal["joint_position_delta"]
    jointKeys: list[str] = Field(min_length=1, max_length=64)
    units: list[Literal["rad", "m"]]
    scale: list[float]
    lower: list[float]
    upper: list[float]
    maxDelta: list[float]

    @model_validator(mode="after")
    def dimensions(self):
        size = len(self.jointKeys)
        if (
            len(set(self.jointKeys)) != size
            or not all(self.jointKeys)
            or any(
                len(values) != size
                for values in (self.units, self.scale, self.lower, self.upper, self.maxDelta)
            )
            or any(lo >= hi for lo, hi in zip(self.lower, self.upper, strict=True))
            or any(value <= 0 for value in self.maxDelta)
            or any(value == 0 for value in self.scale)
        ):
            raise ValueError("Action requires distinct joints and finite ordered limits/scales")
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


def vector(value, size):
    result = np.asarray(value, dtype=np.float64)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError(f"Expected finite vector of shape ({size},)")
    return result


def assemble(spec, state, *, previous_action, references=None):
    """State has joints:{key:{q,qd}}, bodies/sites:{key:{p,q}} in Newton coordinates."""
    references = references or {}

    def key(value):
        return references[value] if value.startswith("$") else value

    result = []
    for item in spec.observations:
        source = item.source
        if source == "previous_action":
            raw = vector(previous_action, item.shape[0])
        elif source.startswith("joint_"):
            field = "q" if source == "joint_position" else "qd"
            raw = vector([state["joints"][key(name)][field] for name in item.keys], len(item.keys))
        else:
            pose = state["bodies" if source.startswith("body_") else "sites"][key(item.key)]
            raw = vector(pose["q" if source.endswith("orientation") else "p"], item.shape[0])
            if source.endswith("orientation") and abs(np.linalg.norm(raw) - 1) > 1e-5:
                raise ValueError("Orientation must be a unit xyzw quaternion")
            if source.endswith("position") and item.frame != "world":
                frame = state["bodies"][key(item.frame)]
                q = vector(frame["q"], 4)
                if abs(np.linalg.norm(q) - 1) > 1e-5:
                    raise ValueError("Frame must have a unit xyzw quaternion")
                raw = np.asarray(rotate_vector([-q[0], -q[1], -q[2], q[3]], raw - vector(frame["p"], 3)))
        result.extend((raw - np.asarray(item.offset)) * np.asarray(item.scale))
    result = np.asarray(result, dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError("Observation exceeds finite float32 range")
    return result


def coverage_failure(task, action, params):
    """Evaluation grids never widen the known training episode parameter coverage."""
    trained = task["episode"]["params"]
    if action != task["implements"].rsplit(".", 1)[1] or not json_equal(params, trained):
        return {
            "code": "precondition_failed",
            "message": "Action parameters exceed training coverage",
            "details": {"trainedAction": task["implements"], "trainedParams": trained},
        }
    return None


def joint_targets(spec, positions, output):
    size = len(spec.action.jointKeys)
    current, raw = vector(positions, size), vector(output, size)
    delta = np.clip(raw * spec.action.scale, -np.asarray(spec.action.maxDelta), spec.action.maxDelta)
    return np.clip(current + delta, spec.action.lower, spec.action.upper)


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

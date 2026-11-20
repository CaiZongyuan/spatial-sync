"""Immutable joint-coordinate mapping supplied by the source Asset."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceJoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    jointKey: str = Field(pattern=r"^/Asset/")
    sourceName: str = Field(min_length=1, max_length=1000)
    sign: Literal[-1, 1]
    zeroOffset: float
    unit: Literal["m", "rad"]


class JointCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    schema_version: Literal["lw.joint-calibration/1"] = Field(alias="schema")
    baseTransform: Annotated[list[float], Field(min_length=16, max_length=16)]
    joints: list[SourceJoint] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def canonical_mapping(self):
        if (
            self.baseTransform[-4:] != [0.0, 0.0, 0.0, 1.0]
            or len({joint.jointKey for joint in self.joints}) != len(self.joints)
            or len({joint.sourceName for joint in self.joints}) != len(self.joints)
        ):
            raise ValueError("Calibration requires a homogeneous base transform and one-to-one joints")
        rows = [self.baseTransform[i : i + 3] for i in (0, 4, 8)]
        if any(
            abs(sum(a * b for a, b in zip(left, right, strict=True)) - (i == j)) > 1e-6
            for i, left in enumerate(rows)
            for j, right in enumerate(rows)
        ):
            raise ValueError("Base transform must be rigid")
        a, b, c = rows
        determinant = math.fsum(
            a[i] * (b[(i + 1) % 3] * c[(i + 2) % 3] - b[(i + 2) % 3] * c[(i + 1) % 3]) for i in range(3)
        )
        if determinant < 0:
            raise ValueError("Base transform must preserve handedness")
        return self

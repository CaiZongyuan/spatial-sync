"""Data accepted by the platform-owned single-robot mjlab compiler."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.policies.action_contract import command_endpoints


class Data(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, serialize_by_alias=True)


Positive = Annotated[float, Field(gt=0)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class MotionFrame(Data):
    time: float = Field(ge=0)
    position: list[float] = Field(min_length=3, max_length=3)
    orientation: list[float] = Field(min_length=4, max_length=4)
    joints: list[float] = Field(max_length=64)


class ReferenceMotion(Data):
    asset: str = Field(pattern=r"^robot/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    assetContentHash: Hash
    jointKeys: list[str] = Field(min_length=1, max_length=64)
    loop: bool
    frames: list[MotionFrame] = Field(min_length=2, max_length=2048)
    license: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def frame_layout(self):
        if (
            self.frames[0].time != 0
            or len(set(self.jointKeys)) != len(self.jointKeys)
            or any(not key.startswith("/Asset/") for key in self.jointKeys)
            or any(right.time <= left.time for left, right in zip(self.frames, self.frames[1:]))
            or any(
                len(frame.joints) != len(self.jointKeys) or abs(math.hypot(*frame.orientation) - 1) > 1e-6
                for frame in self.frames
            )
        ):
            raise ValueError("Motion needs increasing timestamps and canonical unit-quaternion frames")
        return self


class Reward(Data):
    kind: Literal["upright", "height", "velocity_tracking", "joint_tracking", "action_rate"]
    weight: float
    target: float | None = None


class Termination(Data):
    kind: Literal["fall_height", "tilt"]
    threshold: Positive


class Reset(Data):
    jointPosition: list[float] = Field(default_factory=list, max_length=64)
    jointVelocity: float = Field(default=0, ge=0)


class LayerA(Data):
    schema_version: Literal["lw.mjlab-task/1"] = Field(default="lw.mjlab-task/1", alias="schema")
    baseBody: str = Field(pattern=r"^/World/Lab/")
    episodeSeconds: Positive
    rewards: list[Reward] = Field(min_length=1, max_length=16)
    terminations: list[Termination] = Field(default_factory=list, max_length=8)
    command: list[list[float]] = Field(default_factory=list, max_length=3)
    reset: Reset = Field(default_factory=Reset)

    @model_validator(mode="after")
    def primitives(self):
        if self.command and (
            len(self.command) != 3 or any(len(pair) != 2 or pair[0] > pair[1] for pair in self.command)
        ):
            raise ValueError("Command sampling requires three [lower, upper] body-velocity bounds")
        if self.command:
            command_endpoints(self.command)
        if any((item.kind == "height") != (item.target is not None) for item in self.rewards):
            raise ValueError("Only the height reward takes a target")
        return self


class Completion(Data):
    mode: Literal["phase_clip", "posture_recovery", "command_track", "waypoint_nav"]
    dwellSeconds: Positive = 0.25
    maxTiltDeg: Annotated[float, Field(gt=0, lt=180)] = 55.0
    uprightTiltDeg: Annotated[float, Field(gt=0, lt=90)] = 20.0
    minHeight: float = Field(default=0.0, ge=0)
    maxSpeed: Positive = 0.06
    maxAngularSpeed: Positive = 0.50
    trackingTolerance: Positive = 0.1
    cycles: int = Field(default=1, ge=1, le=1000)

    @model_validator(mode="after")
    def tilt_limits(self):
        if self.uprightTiltDeg >= self.maxTiltDeg:
            raise ValueError("Upright tilt must be less than maximum tilt")
        return self

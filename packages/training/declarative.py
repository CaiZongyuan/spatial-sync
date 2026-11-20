"""Data accepted by the platform-owned single-robot mjlab compiler."""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from packages.policies.action_contract import command_endpoints
from packages.training.reward_state import compile_expression, state_references


class Data(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, serialize_by_alias=True)


Positive = Annotated[float, Field(gt=0)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ReferencePin(Data):
    id: str = Field(pattern=r"^reference-motion/[a-f0-9]{64}@1$")
    sha256: Hash

    @model_validator(mode="after")
    def content_address(self):
        if self.id != f"reference-motion/{self.sha256}@1":
            raise ValueError("Reference Motion identity must match its content digest")
        return self


class MotionFrame(Data):
    time: float = Field(ge=0)
    position: list[float] = Field(min_length=3, max_length=3)
    orientation: list[float] = Field(min_length=4, max_length=4)
    joints: list[float] = Field(max_length=64)
    bodyLocal: dict[str, list[float]] | None = Field(
        default=None, max_length=6, exclude_if=lambda value: value is None,
    )


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
        fields = self.frames[0].bodyLocal
        for frame in self.frames:
            if (frame.bodyLocal is None) != (fields is None) or (
                frame.bodyLocal is not None and (
                    fields.keys() != frame.bodyLocal.keys()
                    or any(
                        not key.startswith("/Asset/") or len(point) != 3
                        for key, point in frame.bodyLocal.items()
                    )
                )
            ):
                raise ValueError("Motion body coordinates need the same canonical XYZ channels in each frame")
        return self


class Reward(Data):
    kind: Literal[
        "upright", "height", "velocity_tracking", "joint_tracking", "action_rate",
        "root_pose_tracking", "root_velocity_tracking", "body_local_tracking",
    ]
    weight: float
    target: float | None = None


class Termination(Data):
    kind: Literal["fall_height", "tilt"]
    threshold: Positive


class Reset(Data):
    jointPosition: list[float] = Field(default_factory=list, max_length=64)
    jointVelocity: float = Field(default=0, ge=0)


ScenePath = Annotated[str, Field(pattern=r"^/World/Lab/", max_length=256)]
Span = Annotated[list[float], Field(min_length=2, max_length=2)]


class RigidReset(Data):
    key: ScenePath
    translation: list[Span] = Field(min_length=3, max_length=3)
    yaw: Span = Field(default_factory=lambda: [0.0, 0.0])

    @model_validator(mode="after")
    def bounds(self):
        if any(lo > hi or lo < -100 or hi > 100 for lo, hi in (*self.translation, self.yaw)):
            raise ValueError("Rigid reset ranges must be ordered and bounded")
        return self


class TerrainLevel(Data):
    spawn: ScenePath
    target: ScenePath


class TerrainCurriculum(Data):
    levels: list[TerrainLevel] = Field(min_length=2, max_length=32)
    successDistance: Positive
    demoteDistance: Positive

    @model_validator(mode="after")
    def bands(self):
        if self.successDistance >= self.demoteDistance:
            raise ValueError("Curriculum promotion distance must be below demotion distance")
        return self


class RewardExpression(Data):
    weight: Annotated[float, Field(ge=-100, le=100)]
    expression: JsonValue


class LayerB(Data):
    state: Literal["lw.reward-state/1"]
    rewards: list[RewardExpression] = Field(default_factory=list, max_length=16)
    terminations: list[JsonValue] = Field(default_factory=list, max_length=8)

    def expressions(self):
        for index, reward in enumerate(self.rewards):
            yield compile_expression(reward.expression, path=f"layerB.rewards[{index}].expression")
        for index, expression in enumerate(self.terminations):
            yield compile_expression(expression, "boolean", f"layerB.terminations[{index}]")

    @model_validator(mode="after")
    def bounded(self):
        if not self.rewards and not self.terminations:
            raise ValueError("Layer B requires a reward or termination expression")
        tuple(self.expressions())
        return self


class LayerA(Data):
    schema_version: Literal["lw.mjlab-task/1"] = Field(default="lw.mjlab-task/1", alias="schema")
    baseBody: str = Field(pattern=r"^/World/Lab/")
    episodeSeconds: Positive
    rewards: list[Reward] = Field(max_length=16)
    layerB: LayerB | None = Field(default=None, exclude_if=lambda value: value is None)
    rigidReset: list[RigidReset] | None = Field(
        default=None, max_length=32, exclude_if=lambda value: value is None
    )
    targetSites: list[ScenePath] | None = Field(
        default=None, min_length=1, max_length=32, exclude_if=lambda value: value is None
    )
    curriculum: TerrainCurriculum | None = Field(default=None, exclude_if=lambda value: value is None)
    terminations: list[Termination] = Field(default_factory=list, max_length=8)
    command: list[list[float]] = Field(default_factory=list, max_length=3)
    reset: Reset = Field(default_factory=Reset)

    @model_validator(mode="after")
    def primitives(self):
        if not self.rewards and not (self.layerB and self.layerB.rewards):
            raise ValueError("Task requires at least one Layer A or Layer B reward")
        if self.rigidReset and len({item.key for item in self.rigidReset}) != len(self.rigidReset):
            raise ValueError("Rigid reset keys must be distinct")
        if self.targetSites and self.curriculum:
            raise ValueError("Curriculum levels define their own target Sites")
        if (
            self.layerB
            and not (self.targetSites or self.curriculum)
            and any(
                reference.value[0] == "target_position_world"
                for expression in self.layerB.expressions()
                for reference in state_references(expression)
            )
        ):
            raise ValueError("Target state requires targetSites or curriculum")
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

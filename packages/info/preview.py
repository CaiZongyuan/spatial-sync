"""Read-only program preview values, independent of action and wire messages."""

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field, StrictBool, model_validator

from packages.protocol.schema import (
    EntityKey,
    Finite,
    JsonValue,
    Model,
    NonEmpty,
    NonNegative,
    Pose,
    UInt32,
    Vec3,
)


class PathPreview(Model):
    points: list[Vec3] = Field(default_factory=list, max_length=64)
    startJointPositions: list[Finite] = Field(default_factory=list)
    targetJointPositions: list[Finite] = Field(default_factory=list)
    targetPose: Pose
    targetBodyPoses: dict[EntityKey, Pose] = Field(default_factory=dict)
    policyDriven: StrictBool = False

    @model_validator(mode="after")
    def taught_path(self):
        if not self.policyDriven and (not self.points or not self.startJointPositions
                                     or len(self.startJointPositions) != len(self.targetJointPositions)):
            raise ValueError("A taught preview requires a path and equal-length start/target joints")
        return self


class PreviewRequest(Model):
    entityKey: EntityKey
    action: NonEmpty
    params: dict[str, JsonValue] = Field(default_factory=dict)
    planId: NonEmpty
    revision: int = Field(strict=True, ge=1)
    stepId: int = Field(strict=True, ge=1)


class SourcePreview(Model):
    sceneRevision: NonEmpty
    epoch: UInt32
    simTime: NonNegative
    sampleTimeMs: NonNegative
    preview: PathPreview | None = None


@dataclass(frozen=True)
class PreviewContext:
    """Owned values and geometry only; no host, actuator or invocation methods."""

    config: Any
    values: Mapping[str, JsonValue]
    kinematics: Any
    _cached: Callable[[Hashable, Callable[[], PathPreview]], PathPreview]

    def cached(self, fingerprint, build):
        return self._cached(fingerprint, build)

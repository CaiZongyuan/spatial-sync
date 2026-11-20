"""Data-only requests for a calibrated Reference Motion."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from packages.motion.contract import VideoLicense
from packages.training.declarative import Data, Hash, ReferenceMotion

AssetPath = Annotated[str, Field(pattern=r"^/Asset/", max_length=256)]
Limb = Literal["leftLeg", "rightLeg", "leftArm", "rightArm"]


class ClipPin(Data):
    id: str = Field(pattern=r"^motion-clip/[a-f0-9]{64}@1$")
    sha256: Hash


class Bend(Data):
    joint: AssetPath
    gain: float = Field(ge=-2, le=2)


class Mapping(Data):
    pelvis: AssetPath
    trunk: AssetPath
    head: AssetPath | None = None
    leftFoot: AssetPath
    rightFoot: AssetPath
    leftHand: AssetPath | None = None
    rightHand: AssetPath | None = None
    bends: dict[Limb, Bend] = Field(default_factory=dict, max_length=4)
    auxiliaryJoints: list[AssetPath] = Field(default_factory=list, max_length=32)
    # Rotate video camera-relative Z-up polar vectors into canonical Asset coordinates.
    sourceToAsset: list[float] = Field(
        default_factory=lambda: [0.0, 0.0, 0.0, 1.0], min_length=4, max_length=4
    )


class ReferenceRequest(Data):
    asset: str = Field(pattern=r"^robot/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    assetContentHash: Hash
    mapping: Mapping
    clip: ClipPin | None = None
    keyframes: ReferenceMotion | None = None
    keyframeLicense: VideoLicense | None = None
    inPlace: bool = True
    terminalHoldSeconds: float = Field(default=0.4, ge=0.2, le=2)
    maxJointSpeed: float = Field(default=2.0, gt=0, le=4)
    maxSlideSpeed: float = Field(default=0.1, gt=0, le=0.5)

    @model_validator(mode="after")
    def source(self):
        if (self.clip is None) == (self.keyframes is None):
            raise ValueError("Select one Motion Clip or one external keyframe sequence")
        if self.clip is not None and self.keyframeLicense is not None:
            raise ValueError("Motion Clip rights come from its immutable source")
        if self.keyframes is not None and (
            self.keyframeLicense is None
            or self.keyframes.asset != self.asset
            or self.keyframes.assetContentHash != self.assetContentHash
        ):
            raise ValueError("External keyframes require the same calibrated Asset and source attribution")
        return self

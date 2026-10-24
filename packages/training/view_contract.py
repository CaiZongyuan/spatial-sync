"""The Job view transport wraps unchanged spatial-v2 messages."""

import math
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from packages.protocol.schema import SceneManifest, SourceInfo, TransformFrame
from packages.training.schema import Schema, Text

VIEW_SCHEMA = "lw.training-view/1"
SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
EnvironmentIndex = Annotated[int, Field(ge=0, le=3)]


def stream_identity(view_id, env_index):
    return f"training-view:{view_id}:env:{env_index}"


class Envelope(Schema):
    schema_: Literal["lw.training-view/1"] = Field(alias="schema")
    jobId: Text
    attemptId: Text | None = None
    viewId: Text | None = None


class ProducerEnvelope(Envelope):
    attemptId: Text
    viewId: Text


class AssetIdentity(Schema):
    id: Text
    contentHash: SHA256


class CaptureSource(Schema):
    source: SourceInfo


class HelloPayload(Schema):
    task: Text
    taskContentHash: SHA256
    scene: Text
    sceneContentHash: SHA256
    assets: list[AssetIdentity]
    descriptorDigest: SHA256
    mappingDigest: SHA256
    environments: list[EnvironmentIndex] = Field(min_length=1, max_length=4)
    source: SourceInfo
    maxRateHz: float = Field(gt=0, le=10)
    capture: CaptureSource | None = None

    @model_validator(mode="after")
    def identities(self):
        if len(set(self.environments)) != len(self.environments):
            raise ValueError("Environment identities must be distinct")
        if len({item.id for item in self.assets}) != len(self.assets):
            raise ValueError("Asset identities must be distinct")
        if self.source.kind != "newton" or self.source.newtonVersion is None or self.source.solver is None:
            raise ValueError("Actual Newton producer version and solver are required")
        if self.source.basePublishHz != self.maxRateHz:
            raise ValueError("Source rate must describe the bounded training publisher")
        return self


class ViewHello(ProducerEnvelope):
    type: Literal["hello"]
    payload: HelloPayload


class ViewManifest(ProducerEnvelope):
    type: Literal["manifest"]
    envIndex: EnvironmentIndex
    payload: SceneManifest

    @model_validator(mode="after")
    def stream(self):
        if self.payload.sessionId != stream_identity(self.viewId, self.envIndex):
            raise ValueError("Manifest spatial identity differs from its view/environment")
        return self


class ViewFrame(ProducerEnvelope):
    type: Literal["frame"]
    envIndex: EnvironmentIndex
    payload: TransformFrame

    @model_validator(mode="after")
    def poses(self):
        if self.payload.sessionId != stream_identity(self.viewId, self.envIndex):
            raise ValueError("Frame spatial identity differs from its view/environment")
        identities = [row[0] for row in self.payload.poses]
        if len(set(identities)) != len(identities):
            raise ValueError("A complete sample cannot repeat a body streamId")
        for row in self.payload.poses:
            if not all(math.isfinite(value) for value in row[1:]):
                raise ValueError("View poses must be finite")
            if abs(sum(value * value for value in row[4:]) - 1) > 1e-4:
                raise ValueError("View pose quaternion must be unit length")
        return self


class StatusPayload(Schema):
    state: Literal["waiting", "live", "stale"]
    message: Text | None = None


class ViewStatus(Envelope):
    type: Literal["status"]
    payload: StatusPayload


class ErrorPayload(Schema):
    code: Text
    message: Text


class ViewError(Envelope):
    type: Literal["error"]
    envIndex: EnvironmentIndex | None = None
    payload: ErrorPayload


class EndedPayload(Schema):
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    reason: Text | None = None


class ViewEnded(Envelope):
    type: Literal["ended"]
    payload: EndedPayload


VIEW_MESSAGE = TypeAdapter(
    Annotated[
        ViewHello | ViewManifest | ViewFrame | ViewStatus | ViewError | ViewEnded,
        Field(discriminator="type"),
    ]
)

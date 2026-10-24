"""Shared immutable Job/material/sample facts for declared and native captures."""

from pydantic import Field

from .subjects import Hash, Schema, Text


class Source(Schema):
    jobId: Text
    task: Text
    taskContentHash: Hash
    scene: Text
    sceneContentHash: Hash
    seed: int = Field(ge=0)
    attemptId: Text
    versions: dict


class Weights(Schema):
    checkpointSha256: Hash
    onnxSha256: Hash
    observationSha256: Hash


class Sample(Schema):
    sourceStateId: Text
    state: dict
    trainingObservation: list[float]
    previousAction: list[float]
    references: dict[str, str] = Field(default_factory=dict)

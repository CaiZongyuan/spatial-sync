"""Versioned native input evidence; these data models do not grant source trust."""

from typing import Literal

from pydantic import AwareDatetime, Field, JsonValue, TypeAdapter, model_validator

from .evidence_contract import Sample, Source, Weights
from .subjects import Hash, Schema, Text


class Implementation(Schema):
    module: Text
    qualname: Text
    sha256: Hash


class NativeRuntime(Schema):
    collectorSha256: Hash
    observerSha256: Hash
    runner: Implementation
    actor: Implementation
    act: Implementation
    exporter: Implementation
    stateProvider: Implementation
    observationGroups: list[Text] = Field(min_length=1)
    environments: list[int] = Field(min_length=1, max_length=4)
    boundary: Literal["rsl-mlp-pre-normalizer"]
    normalization: Literal["inside-exported-graph"]
    previousAction: Literal["raw-native-algorithm-output"]
    previousActionReset: Literal["zero-on-environment-epoch"]

    @model_validator(mode="after")
    def actual_environment_indices(self):
        if len(set(self.environments)) != len(self.environments) or min(self.environments) < 0:
            raise ValueError("Native source environments must be distinct nonnegative indices")
        return self


class NativeProfile(NativeRuntime):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    evidenceMode: Literal["native", "controlled"]
    taskContentHash: Hash
    stateProviderFile: Text
    versions: dict[Text, Text]
    inputWidth: int = Field(ge=1, le=4096)
    inputName: Text
    actionWidth: int = Field(ge=1, le=64)

    def runtime(self):
        return {key: self.model_dump(mode="json")[key] for key in NativeRuntime.model_fields}


class NativeCapture(Schema):
    producer: Literal["native-onnx-input/1"]
    profile: Text
    profileSha256: Hash
    runtime: NativeRuntime
    capturedAt: AwareDatetime


class NativeSamples(Schema):
    schema_version: Literal["lw.policy-native-observation-samples/1"] = Field(alias="schema")
    source: Source
    policy: Weights
    capture: NativeCapture
    samples: list[Sample] = Field(min_length=1, max_length=256)


class Witness(Schema):
    sourceStateId: Text
    environment: int = Field(ge=0)
    epoch: int = Field(ge=0)
    physicsStep: int = Field(ge=0)
    stateSha256: Hash
    nativeInputSha256: Hash


class NativeReceipt(Schema):
    schema_version: Literal["lw.policy-native-observation-capture/1"] = Field(alias="schema")
    source: Source
    policy: Weights
    capture: NativeCapture
    samplesArtifact: Text
    samplesSha256: Hash
    sampleCount: Literal[256]
    witnesses: list[Witness] = Field(min_length=256, max_length=256)
    normalizerStateSha256: Hash
    normalizerState: dict[str, JsonValue]
    afterLearn: Literal[True]
    iteration: int = Field(ge=0)


class FinalizedNativeReceipt(NativeReceipt):
    schema_version: Literal["lw.policy-native-observation-capture/2"] = Field(alias="schema")
    afterLearn: Literal[False]
    finalizedCheckpointSha256: Hash


def validate_witnesses(receipt, envelope):
    from apps.node.observation_capture import canonical_hash

    unique, scopes = set(), {}
    for sample, witness in zip(envelope.samples, receipt.witnesses, strict=True):
        state = {key: getattr(sample, key) for key in ("state", "references", "previousAction")}
        if sample.command is not None:
            state["command"] = sample.command
        identity = f"env-{witness.environment}/epoch-{witness.epoch}/step-{witness.physicsStep}"
        scope = (witness.epoch, witness.physicsStep)
        if (
            sample.sourceStateId != identity
            or witness.sourceStateId != identity
            or witness.stateSha256 != canonical_hash(state)
            or witness.nativeInputSha256 != canonical_hash(sample.trainingObservation)
            or witness.stateSha256 in unique
            or scope <= scopes.get(witness.environment, (-1, -1))
            or witness.environment not in receipt.capture.runtime.environments
        ):
            raise ValueError("Actual native state/input witnesses are duplicate, foreign or inconsistent")
        unique.add(witness.stateSha256)
        scopes[witness.environment] = scope
    if len(unique) != 256:
        raise ValueError("Native source did not capture 256 distinct actual policy states")
    return len(unique)


NATIVE_RECEIPTS = TypeAdapter(NativeReceipt | FinalizedNativeReceipt)

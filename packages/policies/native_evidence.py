"""Versioned native input evidence; these data models do not grant source trust."""

import math
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
    compilerHash: Hash | None = Field(default=None, exclude_if=lambda value: value is None)
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
    compilerHash: Hash | None = Field(default=None, exclude_if=lambda value: value is None)
    capturedAt: AwareDatetime


class NativeSamples(Schema):
    schema_version: Literal[
        "lw.policy-native-observation-samples/1", "lw.policy-native-observation-samples/2"
    ] = Field(alias="schema")
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
    schema_version: Literal[
        "lw.policy-native-observation-capture/1", "lw.policy-native-observation-capture/2"
    ] = Field(alias="schema")
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


def validate_native_schema(receipt, envelope, *, compiler, finalized):
    sample_version = 2 if compiler else 1
    receipt_version = 2 if compiler or finalized else 1
    if (
        receipt.schema_version != f"lw.policy-native-observation-capture/{receipt_version}"
        or envelope.schema_version != f"lw.policy-native-observation-samples/{sample_version}"
        or receipt.capture != envelope.capture
        or receipt.capture.compilerHash != compiler
        or receipt.afterLearn == finalized
    ):
        raise ValueError("Native input schema does not match its source authority")


def validate_witnesses(receipt, envelope, *, task=None):
    from apps.node.observation_capture import canonical_hash

    unique, scopes = set(), {}
    physical_states, motion_steps = set(), {}
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
        if task is not None:
            physical = {key: sample.state[key] for key in ("joints", "bodies", "sites")}
            physical_hash = canonical_hash(physical)
            if physical_hash in physical_states or not (physical["bodies"] and physical["joints"]):
                raise ValueError("Declarative capture requires distinct measured physical states")
            physical_states.add(physical_hash)
            if task.get("referenceMotion") is not None:
                _validate_motion_step(sample, witness, task, motion_steps)
        unique.add(witness.stateSha256)
        scopes[witness.environment] = scope
    if len(unique) != 256:
        raise ValueError("Native source did not capture 256 distinct actual policy states")
    return len(unique)


def _validate_motion_step(sample, witness, task, motion_steps):
    step, elapsed = sample.state["motionStep"], sample.state["motionElapsed"]
    if (
        type(step) is not int or step < 0 or isinstance(elapsed, bool)
        or not math.isclose(elapsed, step / task["control"]["hz"], rel_tol=0., abs_tol=1e-9)
    ):
        raise ValueError("Phase witness differs from the pinned control clock")
    prior = motion_steps.get(witness.environment)
    if prior and prior[0] == witness.epoch and (
        (step - prior[2]) * task["mjlab"]["decimation"] != witness.physicsStep - prior[1]
    ):
        raise ValueError("Phase witness step did not advance with physical simulation")
    motion_steps[witness.environment] = (witness.epoch, witness.physicsStep, step)


NATIVE_RECEIPTS = TypeAdapter(NativeReceipt | FinalizedNativeReceipt)

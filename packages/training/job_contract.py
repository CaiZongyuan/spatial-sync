"""GPU-free HTTP metadata shared with the isolated node process."""

from typing import Annotated, Literal

from pydantic import Field

from packages.training.schema import Schema, Text

NodeId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")]
VALIDATION_ITERATIONS = 3
VALIDATION_TIMEOUT = 180


class NodeRegistration(Schema):
    nodeId: NodeId | None = None
    ttlSeconds: int = Field(default=600, ge=1, le=900)
    colocated: bool = False


class ExchangeRegistration(Schema):
    code: str = Field(min_length=32, max_length=128)


class CleanupReceipt(Schema):
    schemaVersion: Literal[1]
    nodeId: NodeId
    instanceId: Text
    jobId: Text
    leaseId: Text
    gpuId: str = Field(min_length=1, max_length=128)


class StartJob(Schema):
    planId: Text | None = None
    seed: int | None = None
    planRevision: int | None = Field(default=None, ge=1)
    requestId: str | None = Field(default=None, min_length=1, max_length=200)
    kind: Literal["training", "plan_validation", "checkpoint_finalize", "motion_preprocess"] = "training"
    motionVideo: str | None = Field(default=None, pattern=r"^motion-video/[a-f0-9]{64}$")
    targetNodeId: NodeId | None = None
    checkpoint: str | None = Field(default=None, pattern=r"^job-[a-f0-9]{32}/[a-f0-9]{64}$")


class ResumeJob(Schema):
    targetNodeId: NodeId | None = None


class TimingProfile(Schema):
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    iterationSeconds: float = Field(gt=0)
    setupSeconds: float = Field(default=0, ge=0)
    dispatchOverheadSeconds: float = Field(default=0, ge=0)
    samples: int = Field(ge=1)
    measuredAt: float = Field(gt=0)
    evidence: Text


class GPU(Schema):
    id: str = Field(min_length=1, max_length=128)
    name: Text
    memoryTotalMb: float | None = Field(default=None, gt=0)
    memoryUsedMb: float | None = Field(default=None, ge=0)
    utilization: float | None = Field(default=None, ge=0, le=100)
    observedAt: float | None = Field(default=None, ge=0)
    unavailableReason: str | None = None
    computeCapability: str | None = Field(default=None, pattern=r"^\d{1,2}\.\d{1,2}$")


class ValidationTiming(Schema):
    numEnvs: int = Field(ge=1)
    setupSeconds: float = Field(ge=0, le=VALIDATION_TIMEOUT)
    warmupSeconds: float = Field(gt=0, le=VALIDATION_TIMEOUT)
    samplesSeconds: list[Annotated[float, Field(gt=0, le=VALIDATION_TIMEOUT)]] = Field(
        min_length=VALIDATION_ITERATIONS - 1, max_length=VALIDATION_ITERATIONS - 1
    )
    peakMemoryMb: float | None = Field(default=None, gt=0)
    initialization: dict | None = Field(default=None, exclude_if=lambda value: value is None)


class JobReport(Schema):
    jobId: Text
    leaseId: Text
    status: Literal["running", "succeeded", "failed", "cancelled", "cleaned"]
    iteration: int | None = Field(default=None, ge=0)
    elapsedSeconds: float | None = Field(default=None, ge=0)
    meanReward: float | None = None
    trainSuccess: float | None = Field(default=None, ge=0, le=1)
    memoryUsedMb: float | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=4000)
    logAfter: int = Field(default=0, ge=0)
    logs: list[str] = Field(default_factory=list, max_length=500)
    tailEnd: int | None = Field(default=None, ge=0)
    validation: ValidationTiming | None = None


class NodeHeartbeat(Schema):
    instanceId: Text
    hardware: Text
    legacyHardware: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    gpus: list[GPU] = Field(max_length=16)
    cpuAvailable: bool = False
    poseRuntime: Literal["mediapipe-pose-landmarker-lite/1"] | None = None
    hostname: Text | None = None
    trainerVersions: dict[str, Text] | None = Field(default=None, max_length=16)
    isaaclabVersions: dict[str, Text] | None = Field(default=None, max_length=16)
    compilerHash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    trainingEntries: dict[Literal["isaaclab", "mjlab"], str] | None = Field(default=None, max_length=2)
    checkpointVersion: Literal[1] | None = None
    profiles: list[TimingProfile] = Field(default_factory=list, max_length=128)
    reports: list[JobReport] = Field(default_factory=list, max_length=16)

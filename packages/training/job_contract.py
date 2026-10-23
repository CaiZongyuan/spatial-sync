"""GPU-free HTTP metadata shared with the isolated node process."""

from typing import Literal

from pydantic import Field

from packages.training.schema import Schema, Text


class StartJob(Schema):
    planId: Text
    seed: int | None = None
    planRevision: int | None = Field(default=None, ge=1)
    requestId: str | None = Field(default=None, min_length=1, max_length=200)


class TimingProfile(Schema):
    key: str = Field(pattern=r"^[a-f0-9]{64}$")
    iterationSeconds: float = Field(gt=0)
    setupSeconds: float = Field(default=0, ge=0)
    samples: int = Field(ge=1)
    measuredAt: float = Field(gt=0)
    evidence: Text


class GPU(Schema):
    id: str = Field(min_length=1, max_length=128)
    name: Text
    memoryTotalMb: float | None = Field(default=None, gt=0)
    memoryUsedMb: float | None = Field(default=None, ge=0)
    utilization: float | None = Field(default=None, ge=0, le=100)


class JobReport(Schema):
    jobId: Text
    leaseId: Text
    status: Literal["running", "succeeded", "failed", "cancelled", "cleaned"]
    iteration: int = Field(default=0, ge=0)
    elapsedSeconds: float = Field(default=0, ge=0)
    meanReward: float | None = None
    trainSuccess: float | None = Field(default=None, ge=0, le=1)
    memoryUsedMb: float | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, max_length=4000)
    logAfter: int = Field(default=0, ge=0)
    logs: list[str] = Field(default_factory=list, max_length=500)


class NodeHeartbeat(Schema):
    instanceId: Text
    hardware: Text
    gpus: list[GPU] = Field(max_length=16)
    cpuAvailable: bool = False
    profiles: list[TimingProfile] = Field(default_factory=list, max_length=128)
    reports: list[JobReport] = Field(default_factory=list, max_length=16)

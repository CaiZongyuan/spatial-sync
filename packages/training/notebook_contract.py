"""Public Lab Notebook snapshots deliberately exclude evaluator-private materials."""

from typing import Literal

from pydantic import Field, JsonValue

from packages.protocol.schema import Actor
from packages.training.schema import GoalRef, Hypothesis, Schema, TaskRef, Text

Verdict = Literal["confirmed", "refuted", "inconclusive"]


class HypothesisJudgement(Schema):
    verdict: Verdict
    ruleVersion: Text
    metric: Text
    operator: Literal[">=", ">", "<=", "<", "=", "=="]
    target: float
    observed: float | None
    ci95: list[float] | None
    reason: Text


class Override(Schema):
    requestId: Text
    verdict: Verdict
    actor: Actor
    atMs: float
    note: Text


class RecordedHypothesis(Schema):
    definition: Hypothesis
    automatic: HypothesisJudgement
    overrides: list[Override] = Field(default_factory=list)


class RecordedPlan(Schema):
    id: Text
    revision: int = Field(ge=1)
    task: TaskRef
    goalId: GoalRef
    goal: Text
    title: Text
    changedFields: list[Text]
    targetSuccessRate: float | None = Field(default=None, ge=0, le=1)


class ScoreCounts(Schema):
    passed: int = Field(ge=0)
    episodes: int = Field(ge=1)
    rate: float = Field(ge=0, le=1)


class RecordedScore(Schema):
    successes: int = Field(ge=0)
    episodes: int = Field(ge=1)
    score: float = Field(ge=0, le=1)
    ci95: list[float]
    phases: dict[str, ScoreCounts] = Field(default_factory=dict)


class RecordedEpisode(Schema):
    caseId: Text | None = None
    status: Literal["starting", "running", "valid", "invalid", "cancelled"] | None = None
    success: bool | None = None
    failedStage: str | None = None
    recordingId: str | None = None
    sessionId: str | None = None
    cleanupComplete: bool | None = None
    invocationId: str | None = None
    invocationStatus: str | None = None
    reason: str | None = None
    milestones: dict[str, bool] = Field(default_factory=dict)
    policyExecution: dict[str, JsonValue] | None = None
    programExecution: dict[str, JsonValue] | None = None


class Comparison(Schema):
    state: Literal["baseline", "comparable", "unknown", "different", "changed_during_execution"]
    reason: str | None
    conditionsGroup: Text
    referenceEvaluationId: str | None
    referenceScore: float | None
    remainingGapReduction: float | None
    gapReason: str | None


class Deployment(Schema):
    status: Literal["not_requested", "pending", "approved", "denied", "revoked", "expired", "unavailable"]
    approvalId: str | None = None
    # No raw approval.details: it may contain held-out promotion results.


class NotebookEntry(Schema):
    schemaVersion: Literal[1] = 1
    entryId: str = Field(pattern=r"^entry-[a-f0-9]{32}$")
    evaluationId: str = Field(pattern=r"^eval-[a-f0-9]{32}$")
    goalId: GoalRef
    split: Literal["train"] = "train"
    plan: RecordedPlan
    suite: Text
    subject: Text
    candidateVersion: str | None
    candidateIdentity: dict[str, JsonValue] | None = None
    status: Literal["completed", "failed", "cancelled"]
    at: Text
    endedAt: Text | None
    score: RecordedScore | None
    partialEpisodes: list[RecordedEpisode]
    attemptedEpisodes: int | None = Field(ge=0)
    reportedDecision: Literal["baseline", "accepted", "rejected"] | None
    acceptanceRule: Literal["wilson95-lower>reference-point/1"] = "wilson95-lower>reference-point/1"
    comparison: Comparison
    deployment: Deployment
    hypotheses: list[RecordedHypothesis]
    recordings: list[Text]
    errorCode: str | None
    knownJobGpuHours: float | None = Field(ge=0)
    costCoverage: Text


class RecordedAgentPlan(Schema):
    id: Text
    revision: int = Field(ge=1)
    agentId: Text
    goalId: GoalRef
    goal: Text
    title: Text
    referenceVersion: int = Field(ge=1)
    candidateVersion: int = Field(ge=1)
    changedFields: list[Text]


class RecordedAgentScore(Schema):
    score: float = Field(ge=0, le=100)
    minimum: float = Field(ge=0, le=100)
    maximum: float = Field(ge=0, le=100)
    cases: int = Field(ge=1)
    runs: int = Field(ge=3)
    episodes: int = Field(ge=3)
    ci95: None = None


class AgentNotebookEntry(Schema):
    schemaVersion: Literal[2] = 2
    layer: Literal["agent"] = "agent"
    entryId: str = Field(pattern=r"^entry-[a-f0-9]{32}$")
    evaluationId: str = Field(pattern=r"^eval-[a-f0-9]{32}$")
    goalId: GoalRef
    split: Literal["train"] = "train"
    plan: RecordedAgentPlan
    suite: Text
    subject: Text
    candidateVersion: int = Field(ge=1)
    status: Text
    at: Text
    endedAt: Text | None
    score: RecordedAgentScore | None
    partialCases: list[dict]
    attemptedCases: int = Field(ge=0)
    reportedDecision: Literal["accepted", "rejected", "inconclusive", "contaminated"]
    acceptanceRule: Literal["agent-train-mean-strictly-higher-min3/1"] = (
        "agent-train-mean-strictly-higher-min3/1"
    )
    comparison: Comparison
    deployment: Deployment
    hypotheses: list[RecordedHypothesis]
    recordings: list[Text]
    errorCode: str | None
    cost: dict
    costCoverage: Text
    files: dict


def parse_notebook_entry(value):
    model = AgentNotebookEntry if value.get("layer") == "agent" else NotebookEntry
    return model.model_validate(value).model_dump(mode="json", by_alias=True)

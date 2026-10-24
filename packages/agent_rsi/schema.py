"""Agent Plan metadata has no embodied Task, PPO or GPU Job fields."""

from typing import Literal

from pydantic import Field, model_validator

from packages.protocol.schema import Actor
from packages.training.schema import GoalRef, Hypothesis, Schema, Text


class Runtime(Schema):
    name: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    thinking: str | None = Field(default=None, max_length=128)


class Change(Schema):
    kind: Literal["instructions", "skill", "config"]
    path: Text
    field: Text | None = None
    description: Text


class Budget(Schema):
    cases: int = Field(ge=1, le=100000)
    wallSeconds: int = Field(ge=1, le=7200)
    llmUsd: float | None = Field(default=None, ge=0)


class GoalBudget(Schema):
    cases: int = Field(ge=1, le=1000000)
    days: float = Field(gt=0)
    llmUsd: float | None = Field(default=None, ge=0)


class AgentPlan(Schema):
    id: str = Field(pattern=r"^plan-[A-Za-z0-9-]+$")
    kind: Literal["training"] = "training"
    layer: Literal["agent"] = "agent"
    title: Text
    goal: Text
    goalId: GoalRef
    agentId: str = Field(pattern=r"^agent/[a-z0-9]+(?:-[a-z0-9]+)*$")
    suite: str = Field(pattern=r"^suite/[a-z0-9]+(?:-[a-z0-9]+)*@[1-9][0-9]*$")
    runtime: Runtime
    referenceEvaluationId: Text
    referenceVersion: int = Field(ge=1)
    candidateVersion: int = Field(ge=1)
    change: Change
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=32)
    notebookRead: list[Text]
    rationale: Text
    interview: str = Field(min_length=1, max_length=128000)
    budget: Budget
    goalBudget: GoalBudget
    previous: Text | None = None
    revision: int = Field(default=1, ge=1)
    status: Text = "draft"

    @model_validator(mode="after")
    def unique_hypotheses(self):
        if len({hypothesis.id for hypothesis in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("Hypothesis identities must be distinct")
        return self


class StoredAgentPlan(Schema):
    plan: AgentPlan
    requestedBy: Actor
    createdAtMs: float
    approvalId: str | None = None
    context: dict
    changes: list[dict]
    runId: str | None = None
    result: dict | None = None
    sessionId: None = None
    sceneRevision: None = None
    epoch: int = 0

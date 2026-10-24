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
    llmUsd: float | None = Field(default=None, ge=0, le=1e12)


class GoalBudget(Schema):
    cases: int = Field(ge=1, le=1000000)
    days: float = Field(gt=0, le=36500)
    llmUsd: float | None = Field(default=None, ge=0, le=1e12)


class BudgetException(Schema):
    reason: Text
    additionalCases: int = Field(default=0, ge=0, le=1000000)
    additionalDays: float = Field(default=0, ge=0, le=365)
    additionalLlmUsd: float = Field(default=0, ge=0, le=1e12)

    @model_validator(mode="after")
    def finite_extension(self):
        if not (self.additionalCases or self.additionalDays or self.additionalLlmUsd):
            raise ValueError("Specify a finite extension")
        return self


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
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=1)
    notebookRead: list[Text]
    rationale: Text
    interview: str = Field(min_length=1, max_length=128000)
    budget: Budget
    goalBudget: GoalBudget
    budgetException: BudgetException | None = None
    previous: Text | None = None
    revision: int = Field(default=1, ge=1)
    status: Text = "draft"


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

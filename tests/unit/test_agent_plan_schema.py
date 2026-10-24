"""Layer validation preserves old PPO intent and rejects dummy embodied Agent inputs."""

import pytest
from pydantic import ValidationError

from packages.agents.bundle import PROFILE
from packages.training.schema import PLAN_DOCUMENT, AgentTrainingPlan, TrainingPlan
from tests.fixtures.training_task import training_plan


def agent_plan():
    common = training_plan() | {"id": "plan-agent-schema"}
    for name in ("task", "algorithm", "model", "hyperparameters", "jobs", "budget", "evaluation"):
        common.pop(name)
    return common | {
        "layer": "agent",
        "goalId": "goal/lab-operator",
        "agent": {"id": "agent/lab-operator", "harnessVersion": 1},
        "suite": "suite/lab-ops-basic@1",
        "model": {"ref": "anthropic/fixture-model", "thinking": "high"},
        "credentialRef": "llm/fixture",
        "executionProfile": PROFILE,
        "goalBudget": {"caseRuns": 90, "llmUsd": 6.0, "days": 7.0},
        "budget": {
            "caseRuns": 30,
            "requests": 120,
            "tokens": 500000,
            "llmUsd": 2.0,
            "wallSeconds": 900.0,
            "maxOutputTokensPerRequest": 512,
        },
    }


def test_old_omitted_layer_normalizes_before_union_but_keeps_full_embodied_validation():
    old = training_plan() | {"id": "plan-old-schema"}
    parsed = PLAN_DOCUMENT.validate_python(old)
    assert isinstance(parsed, TrainingPlan) and parsed.layer == "embodied"
    assert "layer" not in old
    old.pop("task")
    with pytest.raises(ValidationError):
        PLAN_DOCUMENT.validate_python(old)


def test_agent_branch_round_trips_no_dummy_task_ppo_or_episodes():
    plan = PLAN_DOCUMENT.validate_python(agent_plan())
    assert isinstance(plan, AgentTrainingPlan)
    document = plan.model_dump(mode="json")
    assert not {"task", "algorithm", "hyperparameters", "jobs", "evaluation"} & document.keys()
    assert "episodes" not in document["budget"] and "gpuHours" not in document["budget"]
    assert PLAN_DOCUMENT.validate_json(plan.model_dump_json()) == plan


@pytest.mark.parametrize(
    "change",
    [
        {"task": "task/fake@1"},
        {"jobs": {"seeds": [1]}},
        {"split": "test"},
        {"layer": "unknown"},
        {"goalId": None},
        {"credentialRef": "../private"},
        {"model": {"ref": "anthropic/fixture-model", "thinking": "high", "apiKey": "not-accepted"}},
    ],
)
def test_agent_plan_refuses_ambiguous_or_cross_layer_authority(change):
    with pytest.raises(ValidationError):
        PLAN_DOCUMENT.validate_python(agent_plan() | change)


@pytest.mark.parametrize(
    "budget",
    [
        None,
        {"caseRuns": 30, "llmUsd": 2.0, "days": 0.0},
        {"caseRuns": 30, "llmUsd": float("nan"), "days": 1.0},
    ],
)
def test_agent_goal_authority_requires_its_own_finite_days_and_budget(budget):
    with pytest.raises(ValidationError):
        PLAN_DOCUMENT.validate_python(agent_plan() | {"goalBudget": budget})


def test_agent_goal_days_is_independent_of_execution_deadline():
    document = agent_plan()
    document["budget"]["wallSeconds"] = 1.0
    parsed = PLAN_DOCUMENT.validate_python(document)
    assert parsed.goalBudget.days == 7 and parsed.budget.wallSeconds == 1

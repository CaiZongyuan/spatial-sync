"""Goal capacity honors the existing cross-revision Plan pool and durable approvals."""

from collections import defaultdict
from types import SimpleNamespace

import pytest

from packages.info.approvals import ApprovalStore
from packages.info.plans import PlanError
from packages.protocol.schema import Actor
from packages.training.loop_budgets import LoopBudgets
from packages.training.plans import StoredTrainingPlan
from packages.training.schema import TrainingPlan
from tests.fixtures.training_task import training_plan


@pytest.fixture
def domain(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("packages.info.approvals.time.time", lambda: clock[0])
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    approvals.startup()
    plans = SimpleNamespace(records={}, history=defaultdict(dict))
    plans.snapshot = lambda: (
        tuple(record for versions in plans.history.values() for record in versions.values()),
        dict(plans.records),
    )
    manager = SimpleNamespace(
        training_plans=plans,
        approvals=approvals,
        jobs=SimpleNamespace(
            records={}, nodes={}, _require_available=lambda: None, _has_lease=lambda job: False
        ),
        evaluations=SimpleNamespace(store=SimpleNamespace(records={}), _blocked=set()),
    )
    budgets = LoopBudgets(manager, clock=lambda: clock[0])
    task = {
        "contentHash": "a" * 64,
        "task": {
            "loopBudget": {
                "gpuHours": 20.0,
                "episodes": 100,
                "days": 1.0,
                "llmUsd": None,
            }
        },
    }

    def submit(identity, gpu=10.0, revision=1, **changes):
        task_id = changes.pop("task", "task/probe@1")
        body = training_plan(task_id, **changes) | {"id": identity, "revision": revision}
        body["budget"] = {"gpuHours": gpu, "episodes": 10}
        plan = TrainingPlan.model_validate(body).model_dump(mode="json", by_alias=True)
        context = budgets.prepare(plan, task)
        approval = approvals.submit(subject="plan", subject_id=identity, requester=Actor(), title=identity)
        record = StoredTrainingPlan(
            plan=TrainingPlan.model_validate(plan),
            requestedBy=Actor(),
            createdAtMs=clock[0] * 1000,
            approvalId=approval.approvalId,
            goalContext=context,
        )
        plans.records[identity] = record
        plans.history[identity][revision] = record
        return record

    def approve(record):
        budgets.require_fresh_approval(record)
        approvals.decide(record.approvalId, "approve", Actor(kind="user", name="budget-test"))

    return manager, budgets, clock, task, submit, approve


def test_revision_replaces_one_plan_pool_without_double_charging_old_actual(domain):
    manager, budgets, _, _, submit, approve = domain
    first = submit("plan-a")
    approve(first)
    manager.jobs.records["done"] = {
        "jobId": "done",
        "planId": "plan-a",
        "planRevision": 1,
        "status": "failed",
        "chargedGpuHours": 2.0,
        "leaseId": None,
    }
    second = submit("plan-a", revision=2)
    approve(second)
    view = budgets.summary("goal/probe")
    assert view["actual"]["gpuHours"] == 2
    assert view["unspentCommitment"]["gpuHours"] == 8
    assert view["effective"]["gpuHours"] == 10
    assert len(view["pools"]) == 1


def test_superseded_queued_no_node_job_retains_its_frozen_capacity(domain):
    manager, budgets, _, _, submit, approve = domain
    first = submit("plan-a")
    approve(first)
    manager.jobs.records["queued"] = {
        "jobId": "queued",
        "planId": "plan-a",
        "planRevision": 1,
        "status": "queued",
        "chargedGpuHours": 0.0,
        "leaseId": None,
        "estimatedGpuHours": None,
    }
    second = submit("plan-a", gpu=3.0, revision=2)
    approve(second)
    view = budgets.summary("goal/probe")
    assert view["effective"]["gpuHours"] == 10
    assert view["pools"][0]["heldJobs"] == ["queued"]
    manager.jobs.records["queued"].update(status="cancelled", chargedGpuHours=1.0)
    assert budgets.summary("goal/probe")["effective"]["gpuHours"] == 3


def test_new_round_counts_separately_and_an_old_underbudget_card_must_be_refreshed(domain):
    _, budgets, _, _, submit, approve = domain
    first, second = submit("plan-a"), submit("plan-b")
    approve(first)
    with pytest.raises(PlanError) as stale:
        approve(second)
    assert stale.value.detail["code"] == "loop_budget_changed"
    fresh = submit("plan-b", revision=2)
    approve(fresh)
    view = budgets.summary("goal/probe")
    assert view["effective"]["gpuHours"] == 20 and view["exhausted"]
    third = submit("plan-c")
    assert third.goalContext["review"]["exhaustedBefore"]
    approve(third)  # Explicit human override is per Plan; no reset of the Goal limit.
    assert budgets.summary("goal/probe")["limit"]["gpuHours"] == 20
    assert budgets.summary("goal/probe")["effective"]["gpuHours"] == 30


def test_clock_progress_does_not_make_approval_naturally_stale_but_deadline_crossing_does(domain):
    _, budgets, clock, _, submit, approve = domain
    first = submit("plan-a")
    clock[0] += 1
    approve(first)
    second = submit("plan-b")
    clock[0] += 1
    approve(second)
    third = submit("plan-c")
    clock[0] += 86400
    with pytest.raises(PlanError):
        approve(third)
    fresh = submit("plan-c", revision=2)
    assert "days" in fresh.goalContext["review"]["exhaustedDimensions"]
    approve(fresh)


def test_revoke_retains_real_failures_and_first_limit_survives_task_budget_edits(domain):
    manager, budgets, _, task, submit, approve = domain
    first = submit("plan-a")
    approve(first)
    manager.jobs.records["failed"] = {
        "jobId": "failed",
        "planId": "plan-a",
        "planRevision": 1,
        "status": "failed",
        "chargedGpuHours": 2.0,
        "leaseId": None,
    }
    manager.approvals.decide(first.approvalId, "revoke", Actor(kind="user", name="test"))
    task["task"]["loopBudget"]["gpuHours"] = 999.0
    # A later Task declaration cannot mutate the first approved frozen snapshot.
    assert first.goalContext["declaredBudget"]["gpuHours"] == 20.0
    second = submit("plan-b", gpu=1.0, task="task/probe@2")
    approve(second)
    view = budgets.summary("goal/probe")
    assert view["limit"]["gpuHours"] == 20
    assert view["actual"]["gpuHours"] == 2 and view["effective"]["gpuHours"] == 3
    assert second.plan.goalId == first.plan.goalId
    with pytest.raises(PlanError):
        submit("plan-b", revision=2, goalId="goal/new")


def test_repeated_failed_evaluation_projection_never_double_charges_or_drops_attempts(domain):
    manager, budgets, _, _, submit, approve = domain
    first = submit("plan-a")
    approve(first)
    manager.evaluations.store.records["invalid"] = {
        "evaluationId": "invalid",
        "planId": "plan-a",
        "planRevision": 1,
        "status": "failed",
        "attemptedEpisodes": 3,
        "episodes": [{"success": False}],
    }
    one, two = budgets.summary("goal/probe"), budgets.summary("goal/probe")
    assert one["actual"]["episodes"] == two["actual"]["episodes"] == 3
    assert one["effective"]["episodes"] == two["effective"]["episodes"] == 10


@pytest.mark.parametrize("later_time", [100.0, 99.0])
def test_first_limit_and_anchor_follow_durable_approval_order_not_random_id_or_wallclock(domain, later_time):
    manager, budgets, clock, task, submit, approve = domain
    task["task"]["loopBudget"]["gpuHours"] = 4.0
    first = submit("plan-z-first", gpu=1.0)
    approve(first)
    anchor = manager.approvals.records[first.approvalId].decisions[0].atMs / 1000
    clock[0] = later_time
    task["task"]["loopBudget"] = {"gpuHours": 999.0, "episodes": 9999, "days": 999.0}
    second = submit("plan-a-later", gpu=1.0, task="task/probe@2")
    assert second.goalContext["review"]["limit"]["gpuHours"] == 4
    approve(second)
    summary = budgets.summary("goal/probe")
    assert summary["limit"]["gpuHours"] == 4
    assert summary["limit"]["days"] == 1
    assert summary["firstApprovedAt"] == anchor
    assert summary["deadline"] == anchor + 86400
    manager.approvals.records.clear()
    manager.approvals._started = False
    manager.approvals.startup()
    restored = budgets.summary("goal/probe")
    assert restored["authority"] == {"planId": "plan-z-first", "revision": 1}
    assert restored["firstApprovedAt"] == anchor and restored["limit"]["gpuHours"] == 4

"""Project existing approval, evaluation and native usage facts; no second ledger."""

from datetime import UTC, datetime

from packages.info.plans import PlanError


def native_total(service, *, goal=None, plan=None):
    ledger = service.manager.costs
    records = [
        record
        for record in ledger._all_items()
        if record.get("source") == "runtime-import"
        and (goal is None or record["associations"].get("goalId") == goal)
        and (plan is None or record["associations"].get("planId") == plan)
    ]
    values = ledger.aggregate(records, ledger.window("all"))
    fields = (
        "llmUsd",
        "knownLlmUsd",
        "requests",
        "tokens",
        "inputTokens",
        "outputTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "costSource",
        "runtimeSessionCount",
    )
    public = [record for record in records if record.get("visibility") != "private"]
    return {key: values.get(key) for key in fields} | {
        "runtimeRecordIds": [record["id"] for record in public],
        "runtimeRecordHashes": {record["id"]: record["contentHash"] for record in public},
        "detailsCoverage": "restricted" if len(public) != len(records) else "complete",
        "observationBasis": "native-high-water-at-observation",
        "observedAt": datetime.now(UTC).isoformat(),
        "timeBasis": "importedAt",
    }


def authority(service, goal):
    records = [record for record in service.history.values() if record.plan.goalId == goal]
    decision = service.manager.approvals.first_decision(
        [record.approvalId for record in records if record.approvalId], "approve"
    )
    if decision is None:
        return None, None
    selected = next(record for record in records if record.approvalId == decision[0])
    return selected, decision[1]


def attempts(service, *, goal=None, plan=None, proposed=None):
    values = {run["evaluationId"]: run for run in service.manager.loop_budgets._evaluation_records()}
    if proposed is not None:
        values[proposed["evaluationId"]] = proposed
    actual = committed = 0
    for run in values.values():
        if (
            run.get("layer") != "agent"
            or run.get("planId") is None
            or goal is not None
            and run.get("goalId") != goal
            or plan is not None
            and run.get("planId") != plan
        ):
            continue
        used = sum(case["status"] != "waiting" for case in run["cases"])
        held = service.manager.loop_budgets._held_evaluation(run)
        actual += used
        committed += max(used, len(run["cases"])) if held else used
    return {"attemptedCases": actual, "committedCases": committed}


def budget(service, goal, *, proposed=None):
    original, approval = authority(service, goal)
    limits = original.plan.goalBudget.model_dump(mode="json") if original else None
    cases, usage = attempts(service, goal=goal, proposed=proposed), native_total(service, goal=goal)
    deadline = approval.atMs / 1000 + limits["days"] * 86400 if approval else None
    return {
        "goalId": goal,
        "limits": limits,
        **cases,
        "usage": usage,
        "basis": {
            "planId": original.plan.id,
            "revision": original.plan.revision,
            "approvalId": original.approvalId,
            "startedAtMs": approval.atMs,
        }
        if original
        else None,
        "deadlineAt": datetime.fromtimestamp(deadline, UTC).isoformat() if deadline else None,
        "expired": deadline is not None and service.clock() >= deadline,
        "remainingCases": max(0, limits["cases"] - cases["committedCases"]) if limits else None,
        "usdEnforcement": "Observed native reports stop future admissions; "
        "no external pre-call hard USD guarantee",
    }


def require_budget(service, record, proposed=None, *, additional_cases=0):
    view = budget(service, record.plan.goalId, proposed=proposed)
    original, _ = authority(service, record.plan.goalId)
    if original is None or original.plan.goalBudget != record.plan.goalBudget:
        raise PlanError("agent_goal_budget_changed", "Keep the Goal's first approved limits", status=409)
    own_cases, own_cost = (
        attempts(service, plan=record.plan.id, proposed=proposed),
        native_total(service, plan=record.plan.id),
    )
    if (
        view["expired"]
        or view["committedCases"] + additional_cases > view["limits"]["cases"]
        or own_cases["committedCases"] + additional_cases > record.plan.budget.cases
        or record.plan.budget.llmUsd is not None
        and (own_cost.get("knownLlmUsd") or 0) > record.plan.budget.llmUsd
        or view["limits"]["llmUsd"] is not None
        and (view["usage"].get("knownLlmUsd") or 0) > view["limits"]["llmUsd"]
    ):
        raise PlanError(
            "agent_budget_exhausted", "The existing Plan/Goal pool cannot admit this work", status=409
        )

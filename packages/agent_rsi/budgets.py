"""Project existing approval, evaluation and native usage facts; no second ledger."""

from copy import deepcopy
from datetime import UTC, datetime

from packages.info.plans import PlanError


def public_cost(service, value):
    """Historical scalar observations stay fixed; newly private capture links stay hidden."""
    result = deepcopy(value)
    private = {
        identity
        for identity, record in service.external.runtime_records.records.items()
        if service.external.runtime_records.is_private(record)
    }
    hidden = False
    if "runtimeRecordIds" in result:
        identifiers = result["runtimeRecordIds"]
        result["runtimeRecordIds"] = [identity for identity in identifiers if identity not in private]
        hidden = len(identifiers) != len(result["runtimeRecordIds"])
    if "runtimeRecordHashes" in result:
        hashes = result["runtimeRecordHashes"]
        result["runtimeRecordHashes"] = {key: value for key, value in hashes.items() if key not in private}
        hidden |= len(hashes) != len(result["runtimeRecordHashes"])
    if hidden:
        result["detailsCoverage"] = "restricted"
    return result


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
        "approvedExceptions": [
            {
                "planId": record.plan.id,
                "revision": record.plan.revision,
                "extension": record.plan.budgetException.model_dump(mode="json"),
            }
            for record in service.history.values()
            if record.plan.goalId == goal
            and record.plan.budgetException is not None
            and (approval := service.manager.approvals.records.get(record.approvalId)) is not None
            and approval.status == "approved"
        ],
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
    extension = record.plan.budgetException
    limits = dict(view["limits"])
    deadline = view["basis"]["startedAtMs"] / 1000 + limits["days"] * 86400
    if extension is not None:
        limits["cases"] += extension.additionalCases
        deadline += extension.additionalDays * 86400
        if limits["llmUsd"] is not None:
            limits["llmUsd"] += extension.additionalLlmUsd
    if (
        service.clock() >= deadline
        or view["committedCases"] + additional_cases > limits["cases"]
        or own_cases["committedCases"] + additional_cases > record.plan.budget.cases
        or record.plan.budget.llmUsd is not None
        and (own_cost.get("knownLlmUsd") or 0) > record.plan.budget.llmUsd
        or limits["llmUsd"] is not None
        and (view["usage"].get("knownLlmUsd") or 0) > limits["llmUsd"]
    ):
        raise PlanError(
            "agent_budget_exhausted", "The existing Plan/Goal pool cannot admit this work", status=409
        )

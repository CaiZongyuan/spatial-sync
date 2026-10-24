"""Public Agent projections use0–100 observed means and never invent confidence intervals."""

from copy import deepcopy

from packages.training.notebook_contract import AgentNotebookEntry
from packages.training.notebook_rules import COMPARISONS
from packages.training.predictions import prediction


def hypothesis_result(definition, result, run):
    declared = definition["predict"]
    operation, target = prediction(declared["to"])
    value = {
        "score": result["score"],
        "meanScore": result["score"],
        "durationSeconds": run.get("result", {}).get("durationSeconds"),
        "llmUsd": result["cost"].get("llmUsd"),
    }.get(declared["metric"])
    complete = result["decision"] in ("accepted", "rejected")
    met = complete and value is not None and COMPARISONS[operation](value, target)
    return {
        "verdict": "confirmed" if met else "inconclusive",
        "ruleVersion": "agent-observed-point/1",
        "metric": declared["metric"],
        "operator": operation,
        "target": target,
        "observed": value if complete else None,
        "ci95": None,
        "reason": "prediction_met"
        if met
        else "metric_unavailable"
        if value is None
        else "evaluation_incomplete"
        if not complete
        else "insufficient_evidence",
    }


def notebook_entry(record, run):
    plan, result = record.plan, record.result
    reference = record.context["reference"]
    score = None
    if result["score"] is not None:
        points = [case["score"] for case in run["cases"]]
        score = {
            "score": result["score"],
            "minimum": min(points),
            "maximum": max(points),
            "cases": run["result"]["cases"],
            "runs": run["result"]["runs"],
            "episodes": run["result"]["episodes"],
            "ci95": None,
        }
    comparable = result["decision"] in ("accepted", "rejected")
    return AgentNotebookEntry.model_validate(
        {
            "entryId": "entry-" + run["evaluationId"].removeprefix("eval-"),
            "evaluationId": run["evaluationId"],
            "goalId": plan.goalId,
            "plan": {
                "id": plan.id,
                "revision": plan.revision,
                "agentId": plan.agentId,
                "goalId": plan.goalId,
                "goal": plan.goal,
                "title": plan.title,
                "referenceVersion": plan.referenceVersion,
                "candidateVersion": plan.candidateVersion,
                "changedFields": [change["field"] for change in record.changes],
            },
            "suite": plan.suite,
            "subject": plan.agentId,
            "candidateVersion": plan.candidateVersion,
            "status": run["status"],
            "at": run["at"],
            "endedAt": run.get("endedAt"),
            "score": score,
            "partialCases": [
                {key: deepcopy(value) for key, value in case.items() if key != "completionHash"}
                for case in run["cases"]
            ],
            "attemptedCases": sum(case["status"] != "waiting" for case in run["cases"]),
            "reportedDecision": result["decision"],
            "comparison": {
                "state": "comparable" if comparable else "unknown",
                "reason": result["reason"],
                "conditionsGroup": reference["evaluationId"],
                "referenceEvaluationId": reference["evaluationId"],
                "referenceScore": reference["score"],
                "remainingGapReduction": None,
                "gapReason": "agent_mean_scale",
            },
            "deployment": {"status": "not_requested"},
            "hypotheses": [
                {
                    "definition": definition.model_dump(mode="json", by_alias=True),
                    "automatic": hypothesis_result(
                        definition.model_dump(mode="json", by_alias=True), result, run
                    ),
                    "overrides": [],
                }
                for definition in plan.hypotheses
            ],
            "recordings": [case["recordingId"] for case in run["cases"] if case.get("recordingId")],
            "errorCode": result["reason"],
            "cost": result["cost"],
            "costCoverage": "Completion snapshots of explicitly linked native records; "
            "missing metrics stay unknown",
            "files": result["files"],
        }
    ).model_dump(mode="json", by_alias=True)

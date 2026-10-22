"""Evaluator-only weighted conditions over final state and recorded process evidence.

This module does not run an Agent, execute a tool, write a scoreboard or expose a
grading endpoint. The runner supplies its trusted Trace and final observations.
"""

from math import isfinite

from packages.info.conditions import condition_state
from packages.info.schema_subset import json_equal


def process_metrics(trace, elapsed_seconds):
    if type(elapsed_seconds) not in (int, float) or not isfinite(elapsed_seconds) or elapsed_seconds < 0:
        raise ValueError("Invalid evaluation elapsed time")
    previous, plans, first_operation = 0, {}, None
    rejected, steps, action_counts = 0, 0, {}
    for event in trace:
        at, kind = event["atSeconds"], event["kind"]
        if (type(at) not in (int, float) or not isfinite(at) or not previous <= at <= elapsed_seconds
            or kind not in ("plan", "action", "command", "read", "check", "wait")):
            raise ValueError("Invalid evaluation process evidence")
        previous = at
        steps += 1
        if kind == "plan":
            plan = event["plan"]
            if (plan.get("kind") == "operation"
                and plan.get("status") in ("approved", "submitted", "completed")):
                plans[plan["id"]] = plan
        if kind in ("action", "command") and first_operation is None:
            first_operation = event, dict(plans)
        if kind == "action" and event.get("risk") == "high" and event.get("status") == "rejected":
            rejected += 1
        if kind == "action" and event.get("status") in ("succeeded", "rejected"):
            key = event["status"] + ":" + event["entityKey"] + "." + event["action"]
            action_counts[key] = action_counts.get(key, 0) + 1
        if kind == "action" and event.get("status") in ("running", "succeeded"):
            key = "started:" + event["entityKey"] + "." + event["action"]
            action_counts[key] = action_counts.get(key, 0) + 1
    planned = False
    if first_operation is not None:
        event, preceding = first_operation
        plan = preceding.get(event.get("planId"), {})
        scope = ("sessionId", "sceneRevision", "epoch")
        if event["kind"] == "action" and all(key in event and event[key] == plan.get(key) for key in scope):
            planned = any(
                step["id"] == event.get("stepId") and json_equal(step.get("do"), {
                    "entity": event["entityKey"], "action": event["action"], "params": event.get("params", {})
                }) for step in plan.get("steps", [])
            )
    return {"rejectedHighRisk": rejected, "steps": steps, "elapsedSeconds": elapsed_seconds,
            "planFirst": planned, **action_counts}


def final_observations(state, session, alerts):
    """Project authoritative HTTP reads without changing any spatial/entity identity."""
    values = {key: entity["values"] for key, entity in state["entities"].items()}
    metadata = {key: entity.get("meta", {}) for key, entity in state["entities"].items()}
    values["$session"] = {"state": session["state"], "epoch": session["epoch"]}
    values["$alerts"] = {"acknowledgedCount": sum(bool(item.get("acknowledgement")) for item in alerts)}
    return values, metadata


def grade_agent_case(suite, case_id, values, trace, elapsed_seconds, metadata=None):
    """Return scores only; private predicates and expected values never enter diagnostics."""
    suite.verify()
    metrics = process_metrics(trace, elapsed_seconds)
    observations = dict(values) | {"$process": metrics}
    dimensions = {}
    for item in suite.grading[case_id]["dimensions"]:
        passed = condition_state([item["when"]], "", observations, metadata) is True
        dimensions[item["id"]] = item["weight"] if passed else 0
    return {"score": sum(dimensions.values()), "dimensions": dimensions, "metrics": metrics}

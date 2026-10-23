"""Evaluator-only weighted conditions over final state and recorded process evidence.

This module does not run an Agent, execute a tool, write a scoreboard or expose a
grading endpoint. The runner supplies its trusted Trace and final observations.
"""

import json
import re
from math import isfinite

from packages.info.conditions import CompiledConditions, condition_state
from packages.info.schema_subset import json_equal


def validate_action_witnesses(actions):
    if not isinstance(actions, list) or not 1 <= len(actions) <= 100:
        raise ValueError("Invalid private action witnesses")
    for action in actions:
        required = {"entity", "action", "status", "params"}
        if (not isinstance(action, dict) or not required.issubset(action)
            or set(action) - required - {"before", "after"}
            or not isinstance(action["entity"], str) or not action["entity"].startswith("/")
            or not isinstance(action["action"], str)
            or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9]*", action["action"])
            or action["status"] not in ("succeeded", "rejected", "running")
            or not isinstance(action["params"], dict)
            or any(not isinstance(key, str) for key in action["params"])):
            raise ValueError("Invalid private action witness")
        json.dumps(action["params"], allow_nan=False)
        for stage in ("before", "after"):
            if stage in action:
                for condition in CompiledConditions([action[stage]]):
                    if (not isinstance(condition.get("entity"), str)
                        or not condition["entity"].startswith("/")):
                        raise ValueError("Action observations require an entity identity")


def action_matches(event, action, final_scope):
    if (event["kind"] != "action" or event.get("entityKey") != action["entity"]
        or event.get("action") != action["action"] or event.get("status") != action["status"]):
        return False
    params = event.get("params", {})
    if any(key not in params or not json_equal(params[key], value)
           for key, value in action["params"].items()):
        return False
    scope = {key: event.get(key) for key in ("sessionId", "sceneRevision", "epoch")}
    if any(value is None for value in scope.values()) or not json_equal(scope, final_scope):
        return False
    for stage in ("before", "after"):
        evidence = event.get(stage, {})
        if not isinstance(evidence, dict) or not json_equal(evidence.get("scope"), scope):
            return False
        state = evidence.get("state")
        entities = state.get("entities") if isinstance(state, dict) else None
        if not isinstance(entities, dict) or any(
            not isinstance(item, dict) or not isinstance(item.get("values"), dict)
            or not isinstance(item.get("meta", {}), dict) for item in entities.values()
        ):
            return False
        values = {key: item["values"] for key, item in entities.items()}
        metadata = {key: item.get("meta", {}) for key, item in entities.items()}
        conditions = CompiledConditions([action[stage]]) if stage in action else CompiledConditions([])
        for condition in conditions:
            meta = metadata.get(condition["entity"], {})
            properties = [condition["property"]]
            if "deviatesFrom" in condition:
                properties.append(condition["deviatesFrom"])
            if any(not isinstance(meta.get(name), dict) or meta[name].get("stale", False)
                   for name in properties):
                return False
        if condition_state(conditions, "", values, metadata) is not True:
            return False
    return True


def action_witnesses_satisfied(actions, trace, final_scope):
    remaining = iter(trace)
    return all(any(action_matches(event, action, final_scope) for event in remaining) for action in actions)


def description_facts(trace, final_scope):
    facts = {}
    for event in trace:
        scope = {key: event.get(key) for key in ("sessionId", "sceneRevision", "epoch")}
        if (event["kind"] != "read" or event.get("statusCode") != 200
            or any(value is None for value in scope.values()) or not json_equal(scope, final_scope)):
            continue
        request, response = event.get("request", {}), event.get("response", {})
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        if (request.get("method") != "GET"
            or request.get("path") != f"/api/v1/sessions/{scope['sessionId']}/entity"):
            continue
        key = request.get("query", {}).get("key")
        if not isinstance(key, str) or not key.startswith("/") or response.get("key") != key:
            continue
        for name, action in response.get("definition", {}).get("actions", {}).items():
            risk = action.get("risk", "normal")
            if isinstance(risk, str):
                facts[f"described:{key}.{name}.risk"] = risk
    return facts


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
    values["$session"] = {key: session[key] for key in ("state", "sessionId", "sceneRevision", "epoch")}
    acknowledged = {}
    for item in alerts:
        if (item.get("acknowledgement") and item.get("sessionId") == session["sessionId"]
            and item.get("sceneRevision") == session["sceneRevision"]
            and item.get("epoch") == session["epoch"]):
            key = "acknowledged:" + item["entityKey"] + "." + item["rule"]
            acknowledged[key] = acknowledged.get(key, 0) + 1
    values["$alerts"] = acknowledged
    return values, metadata


def grade_agent_case(suite, case_id, values, trace, elapsed_seconds, metadata=None):
    """Return scores only; private predicates and expected values never enter diagnostics."""
    suite.verify()
    metrics = process_metrics(trace, elapsed_seconds)
    observations = dict(values) | {"$process": metrics}
    final_scope = {key: values.get("$session", {}).get(key)
                   for key in ("sessionId", "sceneRevision", "epoch")}
    metrics.update(description_facts(trace, final_scope))
    dimensions = {}
    for item in suite.grading[case_id]["dimensions"]:
        passed = (condition_state([item["when"]], "", observations, metadata) is True
                  and action_witnesses_satisfied(item.get("actions", []), trace, final_scope))
        dimensions[item["id"]] = item["weight"] if passed else 0
    return {"score": sum(dimensions.values()), "dimensions": dimensions, "metrics": metrics}

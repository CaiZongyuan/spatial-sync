"""Classify required physical evidence before a case can be a scored outcome."""

from math import isfinite

from packages.info.agent_grading import running_witness
from packages.info.conditions import CompiledConditions, condition_state
from packages.info.plans import PlanError
from packages.info.schema_subset import compile_schema, json_equal


def unavailable():
    raise PlanError(
        "agent_evidence_unavailable", "Required physical observations are unavailable", status=503
    )


def require_scope(sample, scope):
    if not isinstance(sample, dict) or not json_equal(sample.get("scope"), scope):
        unavailable()


def require_physical(conditions, sample, scope, catalog):
    require_scope(sample, scope)
    state = sample.get("state", {})
    entities = state.get("entities", {}) if isinstance(state, dict) else {}
    if not isinstance(entities, dict):
        unavailable()
    for condition in CompiledConditions(conditions):
        entity = condition.get("entity", "")
        if not entity.startswith("/"):
            continue  # Absent derived process/alert events are legitimate negatives.
        observed = entities.get(entity, {})
        if not isinstance(observed, dict):
            unavailable()
        values, metadata = observed.get("values", {}), observed.get("meta", {})
        if not isinstance(values, dict) or not isinstance(metadata, dict):
            unavailable()
        properties = [condition["property"]]
        if "deviatesFrom" in condition:
            properties.append(condition["deviatesFrom"])
        for name in properties:
            meta = metadata.get(name)
            if (
                name not in values
                or not isinstance(meta, dict)
                or meta.get("stale", False)
                or meta.get("source") not in ("sim", "virtual", "device")
                or any(
                    type(meta.get(key)) not in (int, float) or not isfinite(meta[key]) or meta[key] < 0
                    for key in ("simTime", "sampleTimeMs")
                )
            ):
                unavailable()
            try:
                definition = catalog.types[catalog.entities[entity]["type"]]["properties"][name]
                compile_schema(definition["schema"]).validate_python(values[name])
            except (KeyError, ValueError, TypeError):
                unavailable()
        if condition_state([condition], "", {entity: values}, {entity: metadata}) is None:
            unavailable()


def require_case_evidence(suite, case_id, final, trace, catalog):
    scope = {key: final["session"].get(key) for key in ("sessionId", "sceneRevision", "epoch")}
    if any(value is None for value in scope.values()):
        unavailable()
    sample = {"scope": final.get("scope"), "state": final["state"]}
    require_scope(sample, scope)
    for dimension in suite.grading[case_id]["dimensions"]:
        require_physical([dimension["when"]], sample, scope, catalog)
        for action in dimension.get("actions", []):
            for event in trace:
                if (
                    event["kind"] != "action"
                    or event.get("entityKey") != action["entity"]
                    or event.get("action") != action["action"]
                    or any(
                        key not in event.get("params", {}) or not json_equal(event["params"][key], value)
                        for key, value in action["params"].items()
                    )
                ):
                    continue
                candidate = (running_witness(event) or event) if action["status"] == "running" else event
                if candidate.get("status") != action["status"]:
                    continue  # Not performing the required action is an ability negative.
                observed_scope = {key: candidate.get(key) for key in scope}
                for stage in ("before", "after"):
                    require_scope(candidate.get(stage), observed_scope)
                if not json_equal(observed_scope, scope):
                    continue  # A legitimate earlier epoch does not earn final-epoch credit.
                for stage in ("before", "after"):
                    if stage in action:
                        require_physical([action[stage]], candidate[stage], scope, catalog)

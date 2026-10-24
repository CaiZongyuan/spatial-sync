"""Classify physical evidence used by the pinned private Agent rubric."""

from packages.info.agent_grading import running_witness
from packages.info.evaluation_evidence import require_physical, require_scope, unavailable
from packages.info.schema_subset import json_equal


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

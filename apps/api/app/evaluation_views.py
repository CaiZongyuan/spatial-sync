"""Public evaluation DTOs: private package inputs stay inside the evaluator."""

import math


def fields(value, names, types):
    return {
        name: value[name]
        for name in names
        if name in value and (value[name] is None or type(value[name]) in types)
    }


def numbers(value, names):
    return {
        name: value[name]
        for name in names
        if name in value
        and (value[name] is None or type(value[name]) in (int, float) and math.isfinite(value[name]))
    }


def public_conditions(value):
    return fields(
        value,
        (
            "suite",
            "suiteContentHash",
            "scene",
            "sceneContentHash",
            "source",
            "newtonVersion",
            "solver",
            "target",
            "bindingHash",
            "embodiment",
            "model",
            "thinking",
        ),
        (str,),
    )


def public_episode(value):
    result = fields(
        value, ("caseId", "failedStage", "invocationId", "invocationStatus", "recordingId"), (str,)
    )
    result.update(fields(value, ("success",), (bool,)))
    if isinstance(value.get("milestones"), dict):
        result["milestones"] = {
            key: passed for key, passed in value["milestones"].items() if type(passed) is bool
        }
    return result


def public_score(value, rows=()):
    result = fields(
        value,
        (
            "evaluationId",
            "suite",
            "subject",
            "split",
            "decision",
            "reference",
            "at",
            "plan",
        ),
        (str,),
    )
    result.update(fields(value, ("version",), (str, int)))
    result.update(numbers(value, ("successes", "episodes", "score", "durationSeconds")))
    if "ci95" in value:
        interval = value["ci95"]
        result["ci95"] = (
            interval
            if isinstance(interval, list)
            and len(interval) == 2
            and all(type(number) in (int, float) and math.isfinite(number) for number in interval)
            else None
        )
    if isinstance(value.get("key"), dict):
        result["key"] = public_conditions(value["key"])
        # Expose equivalence only, never a guessable digest of possibly private inputs.
        anchor = next(
            (
                row
                for row in rows
                if row.get("split") == "train"
                and row.get("suite") == value.get("suite")
                and row.get("subject") == value.get("subject")
                and row.get("key") == value["key"]
            ),
            value,
        )
        result["conditionsGroup"] = anchor["evaluationId"]
    if isinstance(value.get("phases"), dict):
        result["phases"] = {
            name: numbers(phase, ("passed", "episodes", "rate"))
            for name, phase in value["phases"].items()
            if isinstance(phase, dict)
        }
    for name in ("recordings", "traces"):
        if isinstance(value.get(name), list):
            result[name] = [item for item in value[name] if isinstance(item, str)]
    if isinstance(value.get("cost"), dict):
        result["cost"] = numbers(value["cost"], ("llmUsd", "tokens", "gpuHours"))
    return result


def public_evaluation(value, rows=()):
    result = fields(
        value,
        (
            "evaluationId",
            "suite",
            "suiteContentHash",
            "subject",
            "split",
            "status",
            "at",
            "runtimeHash",
            "startedAt",
            "endedAt",
        ),
        (str,),
    )
    if isinstance(value.get("episodes"), list):
        result["episodes"] = [
            public_episode(episode) for episode in value["episodes"] if isinstance(episode, dict)
        ]
    if isinstance(value.get("key"), dict):
        result["key"] = public_conditions(value["key"])
    if isinstance(value.get("result"), dict):
        result["result"] = public_score(value["result"], rows)
    if isinstance(value.get("error"), dict):
        result["error"] = fields(value["error"], ("code",), (str,))
    return result

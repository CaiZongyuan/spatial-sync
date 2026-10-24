"""Hypothesis support is distinct from Candidate acceptance and deployment approval."""

import math
import operator

from packages.info.evaluations import wilson_interval
from packages.training.predictions import prediction

RULE_VERSION = "hypothesis-threshold/1"
COMPARISONS = {">=": operator.ge, ">": operator.gt, "<=": operator.le,
               "<": operator.lt, "=": operator.eq, "==": operator.eq}


def rate_metric(row):
    successes, episodes = row.get("successes", row.get("passed")), row.get("episodes")
    if (
        type(successes) is not int or type(episodes) is not int
        or not 0 <= successes <= episodes or episodes < 1
    ):
        return None, None
    return successes / episodes, wilson_interval(successes, episodes)


def metric(row, name):
    if name in ("success", "successRate", "score"):
        return rate_metric(row)
    if name.startswith("stage."):
        phase = row.get("phases", {}).get(name.removeprefix("stage."))
        return rate_metric(phase) if isinstance(phase, dict) else (None, None)
    if name == "durationSeconds":
        value = row.get(name)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return value, None
    return None, None


def judge_hypothesis(hypothesis, row, *, complete=True):
    declared = hypothesis["predict"]
    comparison, target = prediction(declared["to"])
    result = {"verdict": "inconclusive", "ruleVersion": RULE_VERSION, "metric": declared["metric"],
              "operator": comparison, "target": target, "observed": None, "ci95": None}
    if row.get("split") != "train":
        return result | {"reason": "private_or_unknown_split"}
    if not complete:
        return result | {"reason": "evaluation_incomplete"}
    observed, interval = metric(row, declared["metric"])
    if observed is None:
        return result | {"reason": "metric_unavailable"}
    result.update(observed=observed, ci95=interval)
    if COMPARISONS[comparison](observed, target):
        return result | {"verdict": "confirmed", "reason": "prediction_met"}
    if interval is not None:
        lower, upper = interval
        impossible = {">=": upper < target, ">": upper <= target,
                      "<=": lower > target, "<": lower >= target,
                      "=": not lower <= target <= upper, "==": not lower <= target <= upper}
        if impossible[comparison]:
            return result | {"verdict": "refuted", "reason": "interval_excludes_prediction"}
    return result | {"reason": "insufficient_evidence"}


def remaining_gap_reduction(score, reference, *, comparable=True):
    if not comparable:
        return {"value": None, "reason": "not_comparable"}
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1
           for value in (score, reference)):
        return {"value": None, "reason": "score_unavailable"}
    if reference == 1:
        return {"value": None, "reason": "reference_at_ceiling"}
    return {"value": (score - reference) / (1 - reference), "reason": None}

"""Controller promotion consumes platform evidence; callers cannot submit a score or qualification."""

from copy import deepcopy
from dataclasses import dataclass

from packages.info.evaluation_identity import comparable, comparison_identity
from packages.info.evaluations import evaluation_decision, wilson_interval
from packages.info.plans import PlanError


@dataclass(frozen=True)
class TrainEvidence:
    current_accepted: dict
    prior_reference: dict
    interval: list[float]
    decision: str = "accepted"


def _refuse(code, *, label="unverified", **facts):
    raise PlanError(code, "Promotion requires current complete accepted train evidence",
                    decision={"label": label, **facts})


def _complete_train(store, row):
    record = store.records.get(row.get("evaluationId"), {})
    if (
        row.get("split") != "train" or row.get("layer", "embodied") != "embodied"
        or record.get("status") != "completed" or record.get("result") != row
        or record.get("split", "train") != "train"
        or not comparison_identity(row)
        or not isinstance(row.get("candidateIdentity"), dict)
        or not all(row["candidateIdentity"].get(key) for key in ("kind", "id", "contentHash"))
    ):
        _refuse("incomplete_train_evidence")
    try:
        interval = wilson_interval(row["successes"], row["episodes"])
        episodes = record["episodes"]
        valid = (
            interval is not None and row.get("score") == row["successes"] / row["episodes"]
            and len(episodes) == row["episodes"]
            and all(episode.get("status") == "valid" and type(episode.get("success")) is bool
                    and episode.get("cleanupComplete") is True for episode in episodes)
            and sum(episode["success"] for episode in episodes) == row["successes"]
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        valid = False
    if not valid:
        _refuse("incomplete_train_evidence")
    return interval


def accepted_train_evidence(store, candidate, scene, *, evaluation_id=None):
    """Freeze current accepted Candidate, its prior comparator and their unchanged relation."""
    matching = [
        row for row in store.rows
        if row.get("split") == "train" and row.get("layer", "embodied") == "embodied"
        and row.get("candidateIdentity") == candidate
        and row.get("key", {}).get("scene") == scene.id
        and row["key"].get("sceneContentHash") == scene.content_hash
        and (evaluation_id is None or row.get("evaluationId") == evaluation_id)
    ]
    accepted = [row for row in matching if row.get("decision") == "accepted"]
    if not accepted:
        label = matching[-1].get("decision", "reported") if matching else "reported"
        _refuse("candidate_not_accepted", label=label)
    current = [row for row in accepted if (store.reference(row["key"]) or {}).get("evaluationId")
               == row["evaluationId"]]
    if not current:
        _refuse("train_reference_changed")
    if len(current) != 1:
        _refuse("promotion_evaluation_ambiguous", evaluations=[row["evaluationId"] for row in current])
    row = current[0]
    _complete_train(store, row)
    reference = next(
        (value for value in store.rows if value.get("evaluationId") == row.get("reference")), None
    )
    if (reference is None or reference is row or reference.get("decision") not in ("baseline", "accepted")
            or not comparable(row, reference) or store.rows.index(reference) >= store.rows.index(row)):
        _refuse("comparison_reference_unavailable")
    _complete_train(store, reference)
    decision = evaluation_decision(row["successes"], row["episodes"], reference["score"])
    interval = wilson_interval(row["successes"], row["episodes"])
    if decision != "accepted":
        _refuse("candidate_not_accepted", label=decision, ci95=interval, referenceScore=reference["score"])
    return TrainEvidence(deepcopy(row), deepcopy(reference), interval)

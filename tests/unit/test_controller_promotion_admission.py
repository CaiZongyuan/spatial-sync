"""Promotion consumes the accepted Reference relation without comparing a row to itself."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from packages.info.evaluations import EvaluationStore, wilson_interval
from packages.info.plans import PlanError
from packages.training.promotions import accepted_train_evidence

CANDIDATE = {"kind": "policy", "id": "policy/probe@1", "contentHash": "a" * 64}
SCENE = SimpleNamespace(id="robot-and-tubes@2", content_hash="b" * 64)
IDENTITY = {"environment": "actual-environment", "judge": "actual-judge"}
KEY = {"suite": "suite/probe@1", "scene": SCENE.id, "sceneContentHash": SCENE.content_hash, **IDENTITY}


def result(store, identifier, candidate, successes, *, reference=None, decision="accepted", key=None):
    row = {
        "evaluationId": identifier, "subject": candidate["id"], "candidateIdentity": candidate,
        "suite": "suite/probe@1", "split": "train", "decision": decision, "reference": reference,
        "successes": successes, "episodes": 10, "score": successes / 10,
        "key": key or deepcopy(KEY), "comparisonIdentity": deepcopy(IDENTITY), "at": "2026-10-25",
    }
    store.append(row)
    store.save({
        "evaluationId": identifier, "status": "completed", "result": row,
        "episodes": [{"status": "valid", "success": index < successes, "cleanupComplete": True}
                     for index in range(10)],
    })
    return store.rows[-1]


def accepted_pair(tmp_path, *, successes=10):
    store = EvaluationStore(tmp_path)
    reference = result(store, "eval-reference", {"kind": "program", "id": "robot_arm.newton",
                                               "contentHash": "1" * 64},
                       0, decision="baseline")
    candidate = result(store, "eval-candidate", CANDIDATE, successes, reference=reference["evaluationId"])
    return store, reference, candidate


def test_current_accepted_candidate_is_compared_to_its_prior_reference_not_itself(tmp_path):
    store, reference, candidate = accepted_pair(tmp_path)
    assert store.reference(candidate["key"])["evaluationId"] == candidate["evaluationId"]
    proof = accepted_train_evidence(store, CANDIDATE, SCENE)
    assert proof.current_accepted == candidate and proof.prior_reference == reference
    assert proof.interval == wilson_interval(10, 10)
    assert proof.decision == "accepted"


def test_later_accepted_candidate_makes_the_old_promotion_stale(tmp_path):
    store, _, candidate = accepted_pair(tmp_path, successes=7)
    result(store, "eval-later", CANDIDATE | {"id": "policy/probe@2", "contentHash": "c" * 64},
           10, reference=candidate["evaluationId"])
    with pytest.raises(PlanError) as error:
        accepted_train_evidence(store, CANDIDATE, SCENE)
    assert error.value.detail["code"] == "train_reference_changed" and error.value.status == 409


def test_an_accepted_label_cannot_override_the_strict_wilson_rule(tmp_path):
    store, reference, _ = accepted_pair(tmp_path)
    store = EvaluationStore(tmp_path / "forged-label")
    reference = result(store, "eval-reference", reference["candidateIdentity"], 8, decision="baseline")
    result(store, "eval-candidate", CANDIDATE, 10, reference=reference["evaluationId"])
    with pytest.raises(PlanError) as error:
        accepted_train_evidence(store, CANDIDATE, SCENE)
    assert error.value.detail["code"] == "candidate_not_accepted"
    assert error.value.detail["decision"]["label"] == "rejected"
    assert error.value.detail["decision"]["referenceScore"] == 0.8


@pytest.mark.parametrize("fault", ["unknown", "foreign-reference", "partial", "different-material"])
def test_unknown_foreign_partial_or_different_candidate_is_not_promotion_evidence(tmp_path, fault):
    store, reference, candidate = accepted_pair(tmp_path)
    requested = deepcopy(CANDIDATE)
    if fault == "unknown":
        candidate.pop("comparisonIdentity")
    elif fault == "foreign-reference":
        reference["key"] = reference["key"] | {"environment": "another-environment"}
    elif fault == "partial":
        store.records[candidate["evaluationId"]]["episodes"][0].pop("success")
    else:
        requested["contentHash"] = "f" * 64
    with pytest.raises(PlanError):
        accepted_train_evidence(store, requested, SCENE)

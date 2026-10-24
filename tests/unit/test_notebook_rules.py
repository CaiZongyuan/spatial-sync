"""A supported Hypothesis never silently accepts or deploys its Candidate."""

import pytest

from packages.info.evaluations import evaluation_decision
from packages.training.notebook_rules import judge_hypothesis, remaining_gap_reduction


def hypothesis(target, name="success"):
    return {"id": "H1", "predict": {"metric": name, "to": target}}


def test_confirmed_prediction_can_coexist_with_rejected_candidate_and_no_deployment():
    row = {"split": "train", "successes": 100, "episodes": 100}
    assert judge_hypothesis(hypothesis(">= 0.95"), row)["verdict"] == "confirmed"
    assert evaluation_decision(100, 100, 0.97) == "rejected"
    assert evaluation_decision(100, 100, 1.0) == "rejected"  # Existing policy remains strict.
    assert "decision" not in judge_hypothesis(hypothesis(0.95), row)
    assert "deployment" not in judge_hypothesis(hypothesis(0.95), row)


@pytest.mark.parametrize("successes,episodes,target,expected", [
    (60, 100, ">= 0.9", "refuted"), (8, 10, ">= 0.9", "inconclusive"),
    (90, 100, ">= 0.9", "confirmed"), (90, 100, "> 0.9", "inconclusive"),
    (95, 100, "<= 0.5", "refuted"), (5, 100, "< 0.1", "confirmed"),
])
def test_threshold_support_uses_actual_counts_and_correct_interval_direction(
    successes, episodes, target, expected
):
    assert judge_hypothesis(hypothesis(target), {
        "split": "train", "successes": successes, "episodes": episodes,
    })["verdict"] == expected


def test_partial_failed_private_and_unknown_metrics_never_manufacture_a_conclusion():
    row = {"split": "train", "successes": 10, "episodes": 10,
           "phases": {"insert": {"passed": 8, "episodes": 10}}}
    assert judge_hypothesis(hypothesis(0.8, "stage.insert"), row)["observed"] == 0.8
    for partial in (row | {"split": "test"}, row | {"split": "private"}):
        outcome = judge_hypothesis(hypothesis(0.8), partial)
        assert outcome["verdict"] == "inconclusive" and outcome["observed"] is None
    assert judge_hypothesis(hypothesis(0.8), row, complete=False)["observed"] is None
    assert judge_hypothesis(hypothesis(0.8, "trainSuccess"), row)["reason"] == "metric_unavailable"


def test_gap_reduction_keeps_regressions_and_ceiling_or_missing_basis_unknown():
    assert remaining_gap_reduction(0.9, 0.8)["value"] == pytest.approx(0.5)
    assert remaining_gap_reduction(0.7, 0.8)["value"] == pytest.approx(-0.5)
    assert remaining_gap_reduction(1, 1) == {"value": None, "reason": "reference_at_ceiling"}
    assert remaining_gap_reduction(1, 0.8, comparable=False)["value"] is None

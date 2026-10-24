"""New Goal IDs reach the real cost consumer; frozen old Job attribution is preserved."""

from packages.cost.ledger import CostLedger


def test_new_goal_resource_takes_precedence_without_rewriting_old_goal_text():
    job = {"requestedBy": {"kind": "agent", "name": "tester"}, "nodeId": "local",
           "planId": "plan-a", "planRevision": 1, "jobId": "job-a", "plan": {"goal": "Improve width"}}
    assert CostLedger.attribution(job)["goal"] == "Improve width"
    current = job | {"plan": {"goal": "A revised natural-language description", "goalId": "goal/fr3-width"}}
    assert CostLedger.attribution(current)["goal"] == "goal/fr3-width"
    assert CostLedger.attribution(job)["goal"] == "Improve width"

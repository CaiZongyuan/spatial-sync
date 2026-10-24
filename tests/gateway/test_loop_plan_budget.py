"""Goal approval guards never revoke already admitted work or invent another ledger."""

import pytest
from fastapi.testclient import TestClient
from test_training_jobs import HEARTBEAT, NODE, TOKEN, approve, heartbeat, setup_plan

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.fixtures.training_task import training_plan


def test_old_approved_revision_queued_without_node_still_dispatches_after_new_revision_approval():
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        first, task = setup_plan(client)
        approve(client, first["approvalId"])
        queued = client.post("/api/v1/jobs", json={"planId": first["id"], "seed": 42}).json()
        assert queued["leaseId"] is None and queued["reason"] == "estimate_unavailable"
        revised = client.post(f"/api/v1/plans/{first['id']}/revisions", json=training_plan(task["id"])).json()
        approve(client, revised["approvalId"])
        # New Goal capacity changes cannot invalidate the old frozen approval/seed.
        response = client.post(HEARTBEAT, headers=NODE, json=heartbeat(first, task))
        assert response.status_code == 200, response.text
        current = client.get(f"/api/v1/jobs/{queued['jobId']}").json()
        assert current["planRevision"] == 1 and current["leaseId"]
        pools = app.state.manager.loop_budgets.summary(first["goalId"])["pools"]
        assert len(pools) == 1 and pools[0]["effective"]["gpuHours"] == 1


def test_stale_card_requires_fresh_revision_and_approved_retry_remains_idempotent():
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        first, task = setup_plan(client)
        second = client.post("/api/v1/plans", json=training_plan(task["id"])).json()
        approve(client, first["approvalId"])
        stale = client.post(
            f"/api/v1/approvals/{second['approvalId']}/decision", json={"decision": "approve"}
        )
        assert stale.status_code == 409 and stale.json()["detail"]["code"] == "loop_budget_changed"
        fresh = client.post(f"/api/v1/plans/{second['id']}/revisions", json=training_plan(task["id"])).json()
        card = client.get(f"/api/v1/approvals/{fresh['approvalId']}").json()
        assert card["details"]["loopBudget"]["exhaustedBefore"]  # First Plan reserved100episodes.
        approve(client, fresh["approvalId"])
        later = client.post("/api/v1/plans", json=training_plan(task["id"])).json()
        approve(client, later["approvalId"])
        approve(client, first["approvalId"])  # Capacity has changed; existing receipt is not re-gated.
        assert len(client.get(f"/api/v1/approvals/{first['approvalId']}").json()["decisions"]) == 1


@pytest.mark.parametrize("failure", ["plan", "approval"])
def test_persistence_failure_does_not_grant_plan_or_commit_an_extra_goal_pool(monkeypatch, failure):
    app = create_app(Settings(node_token=TOKEN))
    with TestClient(app) as client:
        first, _ = setup_plan(client)
        before = app.state.manager.loop_budgets.summary(first["goalId"])

        def fail(*args, **kwargs):
            raise OSError("owned injected persistence failure")

        if failure == "approval":
            monkeypatch.setattr(app.state.manager.approvals, "_save", fail)
            with pytest.raises(OSError):
                client.post(f"/api/v1/approvals/{first['approvalId']}/decision", json={"decision": "approve"})
        else:
            monkeypatch.setattr("packages.training.plans.durable_json", fail)
            response = client.post(
                f"/api/v1/plans/{first['id']}/revisions", json=training_plan(first["task"])
            )
            assert response.status_code == 503
        assert client.get(f"/api/v1/plans/{first['id']}").json()["status"] == "submitted"
        after = app.state.manager.loop_budgets.summary(first["goalId"])
        assert before["effective"] == after["effective"] == {"gpuHours": 0, "episodes": 0}
        assert client.post("/api/v1/jobs", json={"planId": first["id"], "seed": 42}).status_code == 409

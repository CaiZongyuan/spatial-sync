"""Public Plan approval keeps independent clocks, original Goal authority and uncertain spend."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest

from packages.info.plans import PlanError
from tests.fixtures.training_task import task_archive, training_plan
from tests.unit.test_agent_inputs import prepared as prepared


async def submit(http, document, identity=None):
    path = "/api/v1/plans" + (f"/{identity}/revisions" if identity else "")
    response = await http.post(path, json=document, headers={"X-Actor": "agent:optimizer"})
    assert response.status_code == 201, response.text
    return response.json()


async def approve(http, document):
    response = await http.post(
        f"/api/v1/approvals/{document['approvalId']}/decision",
        json={"decision": "approve"},
        headers={"X-Actor": "user:human"},
    )
    assert response.status_code == 200, response.text


async def test_public_agent_plan_approval_preserves_first_goal_source_and_both_clocks(prepared):
    inputs, plan, _, app = prepared
    store = app.state.manager.training_plans
    store.agent_inputs = inputs
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as http:
        first = await submit(http, plan.model_dump(mode="json"))
        assert first["layer"] == "agent" and "task" not in first
        assert first["frozenInputs"]["caseRunsRequired"] == 30
        assert first["loopBudget"]["limit"] == {"caseRuns": 90, "llmUsd": 6.0, "days": 7.0}
        assert first["loopBudget"]["deadline"] is None
        forbidden = await http.post(
            f"/api/v1/approvals/{first['approvalId']}/decision",
            json={"decision": "approve"},
            headers={"X-Actor": "agent:self"},
        )
        assert forbidden.status_code == 403
        await approve(http, first)
        wrong_dispatch = await http.post("/api/v1/jobs", json={"planId": first["id"], "requestId": "not-ppo"})
        assert wrong_dispatch.status_code == 422 and "agent_run_required" in wrong_dispatch.text
        with pytest.raises(PlanError, match="embodied Plan"):
            app.state.manager.notebooks.prepare_evaluation(
                SimpleNamespace(planId=first["id"], planRevision=1), None, None
            )
        summary = app.state.manager.loop_budgets.summary(plan.goalId)
        assert summary["deadline"] - summary["firstApprovedAt"] == 7 * 86400
        assert "episodes" not in summary["actual"]
        assert summary["actual"]["caseRuns"] == 0 and summary["remaining"]["llmUsd"] == 4.0
        revised = plan.model_dump(mode="json")
        revised["goalBudget"] = {"caseRuns": 999, "llmUsd": 999.0, "days": 999.0}
        revised["budget"]["wallSeconds"] = 1.0
        second = await submit(http, revised, first["id"])
        await approve(http, second)
        after = app.state.manager.loop_budgets.summary(plan.goalId)
        assert after["limit"] == summary["limit"] and after["deadline"] == summary["deadline"]
        assert after["authority"] == {"planId": first["id"], "revision": 1}
        source = store.get(first["id"], 1).goalContext["sourceSnapshot"]
        assert source["kind"] == "agent-plan" and source["humanDeclaredLimit"]["days"] == 7
        assert (await http.get("/api/v1/health")).json()["sessionCount"] == 0


async def test_actual_plus_unspent_prefix_survives_failed_run_and_plan_withdrawal(prepared):
    inputs, plan, _, app = prepared
    manager = app.state.manager
    manager.training_plans.agent_inputs = inputs
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as http:
        document = await submit(http, plan.model_dump(mode="json"))
        await approve(http, document)
        manager.agent_runs = SimpleNamespace(
            records={
                "job-agent-spent": {
                    "jobId": "job-agent-spent",
                    "planId": document["id"],
                    "planRevision": 1,
                    "status": "failed",
                    "attemptedCaseRuns": 2,
                    "allocatedLlmUsd": 2.0,
                    "allocatedCaseRuns": 30,
                    "usage": {
                        "knownLlmUsd": 0.25,
                        "llmUsd": None,
                        "reservedUsd": 0.75,
                        "unknownUsageRequests": 1,
                    },
                }
            }
        )
        before = manager.loop_budgets.summary(plan.goalId)
        assert before["actual"] == {"caseRuns": 2, "llmUsd": None}
        assert before["effective"]["llmUsd"] == 2.0 and before["unspentCommitment"]["llmUsd"] == 1.75
        assert before["remaining"]["llmUsd"] is None
        withdrawn = await http.post(
            f"/api/v1/approvals/{document['approvalId']}/decision",
            json={"decision": "revoke"},
            headers={"X-Actor": "user:human"},
        )
        assert withdrawn.status_code == 200, withdrawn.text
        after = manager.loop_budgets.summary(plan.goalId)
        assert after["effective"]["llmUsd"] == 1.0 and after["unspentCommitment"]["llmUsd"] == 0.75
        assert after["actual"]["caseRuns"] == 2 and after["actual"]["llmUsd"] is None
        assert after["deadline"] == before["deadline"] and after["remaining"]["llmUsd"] is None


async def test_agent_plan_on_existing_embodied_goal_keeps_original_missing_axes_unknown(prepared):
    inputs, plan, _, app = prepared
    manager = app.state.manager
    manager.training_plans.agent_inputs = inputs
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as http:
        pushed = await http.post(
            "/api/v1/tasks", content=task_archive(), headers={"content-type": "application/x-tar"}
        )
        assert pushed.status_code == 201, pushed.text
        old_plan = await submit(http, training_plan())
        await approve(http, old_plan)
        goal = "goal/training-probe"
        original = manager.loop_budgets.summary(goal)
        agent = deepcopy(plan.model_dump(mode="json"))
        agent["goalId"] = goal
        new_plan = await submit(http, agent)
        assert new_plan["loopBudget"]["authority"] == original["authority"]
        assert new_plan["loopBudget"]["remaining"]["caseRuns"] is None
        assert new_plan["loopBudget"]["remaining"]["llmUsd"] is None
        await approve(http, new_plan)
        after = manager.loop_budgets.summary(goal)
        assert after["limit"] == original["limit"] and after["deadline"] == original["deadline"]
        assert after["authority"] == original["authority"] and after["remaining"]["llmUsd"] is None

"""Metadata-only counterexamples for shared promises, clocks and human overage authority."""

import asyncio
import threading
from collections import deque
from copy import deepcopy

import httpx
import pytest

from apps.api.app.main import create_app
from tests.gateway.test_agent_runs import ExternalAdapter, configured
from tests.unit.test_agent_plan_schema import agent_plan


@pytest.fixture
async def metadata():
    adapter = ExternalAdapter()
    app = create_app(agent_adapter=adapter)
    async with app.router.lifespan_context(app):
        service = app.state.manager.evaluations
        worker = service._worker
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        service._worker = None  # Owned scheduler: these checks launch no Source/provider.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as http:
            yield app, http, adapter
        assert not app.state.manager.sessions and adapter.calls == []


async def request(http, plan, identity):
    return await http.post(
        "/api/v1/agents/lab-operator/runs",
        json={
            "suite": plan["suite"],
            "planId": plan["id"],
            "planRevision": plan["revision"],
            "requestId": identity,
        },
    )


async def revise(http, plan, document):
    response = await http.post(f"/api/v1/plans/{plan['id']}/revisions", json=document)
    assert response.status_code == 201, response.text
    revision = response.json()
    approved = await http.post(
        f"/api/v1/approvals/{revision['approvalId']}/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    return revision


def completed_counterfact(app, job, seconds):
    app.state.agent_runs.update(
        job["jobId"],
        status="succeeded",
        attemptedCaseRuns=30,
        startedAt=100.0,
        finishedAt=100.0 + seconds,
        executionSeconds=seconds,
    )
    evaluator = app.state.manager.evaluations
    evaluator._queue = deque(item for item in evaluator._queue if item[1] != job["evaluationId"])
    evaluator.store.save(evaluator.store.records[job["evaluationId"]] | {"status": "completed"})


async def test_durable_admitting_promise_survives_revision_until_known_admission_failure(
    metadata, monkeypatch
):
    app, http, _ = metadata
    plan = await configured(http, app)
    store = app.state.manager.evaluations.store
    entered, release = threading.Event(), threading.Event()
    original = store.save

    def barrier(record):
        if record.get("layer") == "agent" and record["status"] == "queued":
            entered.set()
            assert release.wait(10)
            raise OSError("controlled evaluation admission save failure")
        return original(record)

    monkeypatch.setattr(store, "save", barrier)
    pending = asyncio.create_task(request(http, plan, "admitting-barrier"))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        [run] = app.state.agent_runs.records.values()
        assert run["status"] == "admitting"
        document = agent_plan()
        document["budget"]["llmUsd"] = 1.0
        revision = await revise(http, plan, document)
        assert revision["revision"] == 2
        summary = app.state.manager.loop_budgets.summary(plan["goalId"])
        assert summary["pools"][0]["effective"]["llmUsd"] == 2.0
        assert summary["pools"][0]["heldJobs"] == [run["jobId"]]
        assert app.state.agent_runs.plan_in_use(plan["id"])
    finally:
        release.set()
    response = await pending
    assert response.status_code == 503, response.text
    assert app.state.agent_runs.get(run["jobId"])["status"] == "failed"
    assert app.state.manager.loop_budgets.summary(plan["goalId"])["effective"]["llmUsd"] == 1.0


async def test_plan_execution_seconds_are_shared_by_repeated_runs_and_revisions(metadata):
    app, http, _ = metadata
    document = agent_plan()
    document["budget"].update(caseRuns=60, wallSeconds=10.0)
    plan = await configured(http, app, document)
    first = (await request(http, plan, "first")).json()
    completed_counterfact(app, first, 8.0)
    second = await request(http, plan, "second")
    assert second.status_code == 202, second.text
    second = second.json()
    assert second["allocatedWallSeconds"] == second["budget"]["wallSeconds"] == 2.0
    revision_document = deepcopy(document)
    revision_document["budget"]["wallSeconds"] = 9.0
    revision = await revise(http, plan, revision_document)
    busy = await request(http, revision, "third-before-cancel")
    assert busy.status_code == 409 and "agent_plan_busy" in busy.text
    cancelled = await http.post(f"/api/v1/jobs/{second['jobId']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    third = await request(http, revision, "third")
    assert third.status_code == 202, third.text
    assert third.json()["budget"]["wallSeconds"] == 1.0


async def test_fresh_human_finite_overage_plan_runs_without_replacing_original_goal_limit(metadata):
    app, http, _ = metadata
    document = agent_plan()
    document["goalBudget"]["caseRuns"] = 30
    first_plan = await configured(http, app, document)
    first_run = (await request(http, first_plan, "first")).json()
    completed_counterfact(app, first_run, 1.0)
    before = app.state.manager.loop_budgets.summary(first_plan["goalId"])
    extra = deepcopy(document) | {"id": "plan-finite-overage"}
    extra["goalBudget"]["caseRuns"] = 999
    submitted = await http.post("/api/v1/plans", json=extra)
    assert submitted.status_code == 201, submitted.text
    extra = submitted.json()
    assert extra["loopBudget"]["limit"]["caseRuns"] == 30 and extra["loopBudget"]["exhausted"]
    approved = await http.post(
        f"/api/v1/approvals/{extra['approvalId']}/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    admitted = await request(http, extra, "explicit-finite-overage")
    assert admitted.status_code == 202, admitted.text
    after = app.state.manager.loop_budgets.summary(first_plan["goalId"])
    assert after["authority"] == before["authority"] and after["limit"] == before["limit"]
    assert after["deadline"] == before["deadline"]

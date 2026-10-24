"""Metadata-only counterexamples for shared promises, clocks and human overage authority."""

import asyncio
import threading
import time
from collections import deque
from copy import deepcopy
from types import SimpleNamespace

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


async def test_expired_goal_requires_fresh_expired_card_approval_and_keeps_original_clock(
    metadata, monkeypatch
):
    app, http, _ = metadata
    plan = await configured(http, app)
    manager = app.state.manager
    before = manager.loop_budgets.summary(plan["goalId"])
    clock = [before["deadline"] - 1]
    manager.loop_budgets.clock = lambda: clock[0]
    app.state.agent_runs.clock = lambda: clock[0]
    monkeypatch.setattr("packages.info.approvals.time", SimpleNamespace(time=lambda: clock[0]))
    early = (await request(http, plan, "approved-before-expiry")).json()
    app.state.agent_runs.update(early["jobId"], status="running")
    app.state.agent_runs._deadlines[early["jobId"]] = time.monotonic() + 10
    assert app.state.agent_runs.active(early["jobId"])
    clock[0] = before["deadline"] + 1
    assert not app.state.agent_runs.active(early["jobId"])
    app.state.agent_runs.update(early["jobId"], status="queued")
    assert (await http.post(f"/api/v1/jobs/{early['jobId']}/cancel")).status_code == 200
    old = await request(http, plan, "old-approval-does-not-revive")
    assert old.status_code == 409 and "Goal deadline expired" in old.text
    revision = await revise(http, plan, agent_plan())
    assert revision["loopBudget"]["expired"] is True
    admitted = await request(http, revision, "fresh-finite-expired-plan")
    assert admitted.status_code == 202, admitted.text
    run = admitted.json()
    assert run["goalException"]["dimensions"] == ["days"]
    assert run["goalException"]["approvalId"] == revision["approvalId"]
    assert run["goalException"]["originalDeadline"] == before["deadline"]
    app.state.agent_runs.update(run["jobId"], status="running")
    app.state.agent_runs._deadlines[run["jobId"]] = time.monotonic() + 10
    assert app.state.agent_runs.active(run["jobId"])
    after = manager.loop_budgets.summary(plan["goalId"])
    assert after["expired"] and after["limit"] == before["limit"]
    assert after["authority"] == before["authority"] and after["deadline"] == before["deadline"]
    app.state.agent_runs.update(run["jobId"], status="queued")


async def test_pending_preexpiry_card_cannot_approve_after_deadline_without_fresh_revision(
    metadata, monkeypatch
):
    app, http, _ = metadata
    plan = await configured(http, app)
    manager = app.state.manager
    deadline = manager.loop_budgets.summary(plan["goalId"])["deadline"]
    clock = [deadline - 1]
    manager.loop_budgets.clock = lambda: clock[0]
    app.state.agent_runs.clock = lambda: clock[0]
    monkeypatch.setattr("packages.info.approvals.time", SimpleNamespace(time=lambda: clock[0]))
    submitted = await http.post(f"/api/v1/plans/{plan['id']}/revisions", json=agent_plan())
    assert submitted.status_code == 201, submitted.text
    pending = submitted.json()
    assert pending["loopBudget"]["expired"] is False
    clock[0] = deadline + 1
    stale = await http.post(
        f"/api/v1/approvals/{pending['approvalId']}/decision", json={"decision": "approve"}
    )
    assert stale.status_code == 409 and "loop_budget_changed" in stale.text


async def test_first_registration_and_another_runs_update_share_one_durable_lock(metadata, monkeypatch):
    app, http, _ = metadata
    plan = await configured(http, app)
    first = (await request(http, plan, "existing-run")).json()
    another = agent_plan() | {"id": "plan-another-admission"}
    submitted = await http.post("/api/v1/plans", json=another)
    assert submitted.status_code == 201, submitted.text
    another = submitted.json()
    assert (
        await http.post(f"/api/v1/approvals/{another['approvalId']}/decision", json={"decision": "approve"})
    ).status_code == 200
    service = app.state.agent_runs
    entered, release, registering = threading.Event(), threading.Event(), threading.Event()
    import packages.agents.runs as runs_module

    original_save, original_create = runs_module.durable_json, service.create

    def save_barrier(path, records):
        if records[first["jobId"]].get("concurrentMarker") == "retained" and not release.is_set():
            entered.set()
            assert release.wait(10)
        original_save(path, records)

    def registering_record(record):
        registering.set()
        return original_create(record)

    monkeypatch.setattr(runs_module, "durable_json", save_barrier)
    monkeypatch.setattr(service, "create", registering_record)
    updating = asyncio.create_task(
        asyncio.to_thread(service.update, first["jobId"], concurrentMarker="retained")
    )
    pending = None
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        pending = asyncio.create_task(request(http, another, "new-run"))
        async with asyncio.timeout(5):
            while not registering.is_set():
                await asyncio.sleep(0.01)
        assert len(service.records) == 1 and not pending.done()
    finally:
        release.set()
    await updating
    response = await pending
    assert response.status_code == 202, response.text
    assert len(service.records) == 2 and service.get(first["jobId"])["concurrentMarker"] == "retained"
    assert service.get(response.json()["jobId"])["status"] == "queued"

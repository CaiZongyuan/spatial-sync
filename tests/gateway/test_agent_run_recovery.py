"""No-Source ownership/restart facts retain finite rights and the existing usage high-water record."""

import json
import time
from contextlib import asynccontextmanager

from apps.node.process import CleanupIncomplete
from packages.agents.provider import TOKEN_KEYS, usage_snapshot
from tests.gateway.test_agent_run_budgets import metadata as metadata
from tests.gateway.test_agent_run_budgets import request, revise
from tests.gateway.test_agent_runs import ExternalAdapter, configured
from tests.unit.test_agent_plan_schema import agent_plan


async def test_non_session_cleanup_failure_holds_owner_until_adapter_explicitly_confirms(
    metadata, monkeypatch
):
    app, http, _ = metadata
    plan = await configured(http, app)
    admitted = (await request(http, plan, "cleanup-promise")).json()
    service, evaluations = app.state.agent_runs, app.state.manager.evaluations

    class Unconfirmed(ExternalAdapter):
        ready = False

        @asynccontextmanager
        async def open(self, record, deadline, directory):
            async with super().open(record, deadline, directory) as transport:
                try:
                    yield transport
                finally:
                    raise CleanupIncomplete("Controlled owned adapter cleanup is unconfirmed")

        async def confirm_cleanup(self, identity):
            return self.ready

    adapter = Unconfirmed()
    service.adapter = adapter
    with monkeypatch.context() as controlled:
        controlled.setattr(service, "active", lambda identity: False)
        await service.evaluate(admitted["evaluationId"])
    run = service.get(admitted["jobId"])
    assert run["status"] == "failed" and run["cleanupIncomplete"]
    assert admitted["evaluationId"] in evaluations._blocked
    assert service.plan_in_use(plan["id"]) and service.wall_commitment(run) == run["allocatedWallSeconds"]
    document = agent_plan()
    document["budget"]["llmUsd"] = 1.0
    await revise(http, plan, document)
    goal = app.state.manager.loop_budgets.summary(plan["goalId"])
    assert goal["effective"]["llmUsd"] == 2.0
    denied = await http.post(f"/api/v1/evaluations/{admitted['evaluationId']}/cleanup")
    assert denied.status_code == 409 and service.get(run["jobId"])["cleanupIncomplete"]
    adapter.ready = True
    confirmed = await http.post(f"/api/v1/evaluations/{admitted['evaluationId']}/cleanup")
    assert confirmed.status_code == 200, confirmed.text
    assert (
        not service.get(run["jobId"])["cleanupIncomplete"]
        and admitted["evaluationId"] not in evaluations._blocked
    )
    assert app.state.manager.loop_budgets.summary(plan["goalId"])["effective"]["llmUsd"] == 1.0


async def test_restart_retains_unknown_wall_and_merges_private_prefix_without_reopening_work(metadata):
    app, http, _ = metadata
    plan = await configured(http, app)
    admitted = (await request(http, plan, "restart-prefix")).json()
    service = app.state.agent_runs
    attempt = {
        "id": admitted["jobId"] + "/request/1",
        "at": time.time(),
        "model": "fixture-model",
        "quoteRevision": 1,
        "rates": dict.fromkeys(TOKEN_KEYS, 1.0),
        "boundTokens": 120,
        "boundUsd": 0.00012,
        "status": "pending",
        "dispatchState": "reserved",
        "usage": {},
        "usageComplete": False,
    }
    service.update(
        admitted["jobId"],
        status="running",
        startedAt=time.time(),
        executionSeconds=None,
        provider={"requests": [attempt]},
        usage=usage_snapshot([attempt]),
    )
    private = service.root / admitted["jobId"] / "private-transport"
    private.mkdir(parents=True)
    (private / "observations.jsonl").write_text(
        json.dumps(
            {"attempt": attempt["id"], "usage": {"input_tokens": 20}, "messageId": "trusted-before-reap"}
        )
        + "\n",
        encoding="utf-8",
    )
    app.state.manager.evaluations.store.startup()
    service.startup()
    run = service.get(admitted["jobId"])
    assert run["status"] == "failed" and run["reason"] == "gateway_restarted"
    assert run["usage"]["knownTokens"] == 20 and run["usage"]["tokens"] is None
    assert run["usage"]["reservedTokens"] == 100 and run["usage"]["requests"] == 1
    assert service.wall_commitment(run) == run["allocatedWallSeconds"]
    assert not service.active(run["jobId"])
    saved = service.path.read_bytes()
    service.startup()
    assert service.path.read_bytes() == saved
    blocked = await request(http, plan, "after-unknown-execution")
    assert blocked.status_code == 409 and "agent_wall_budget_exhausted" in blocked.text

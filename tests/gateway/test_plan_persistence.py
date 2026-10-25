"""Actual persistence ordering keeps the loop responsive and plan grants fail closed."""

import asyncio
import json
import threading
from pathlib import Path

import httpx
import pytest
from test_info import CENTRIFUGE, risky_catalog
from test_plans import close_step, plan

from apps.api.app.main import create_app
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.journal import AppendJournal


def saved_records(path):
    # Later asynchronous observations may be in the middle of their final line.
    # The consumed record must already exist in this complete prefix.
    return [json.loads(line) for line in path.read_bytes().split(b"\n")[:-1]]


async def test_saved_records_keep_the_complete_prefix_during_an_actual_partial_append(tmp_path, monkeypatch):
    journal = AppendJournal(tmp_path / "prefix.jsonl")
    committed = {"consumed": True}
    await journal.wait(journal.append(json.dumps(committed)))
    entered, release = threading.Event(), threading.Event()
    original = journal._write_batch

    def partial_write(stream, entries):
        assert len(entries) == 1
        sequence, payload, ticket = entries[0]
        split = len(payload) // 2
        stream.write(payload[:split])
        stream.flush()
        entered.set()
        assert release.wait(5), "The test must release its owned partial append"
        original(stream, [(sequence, payload[split:], ticket)])

    monkeypatch.setattr(journal, "_write_batch", partial_write)
    pending = journal.append(json.dumps({"later": "another observation"}))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert saved_records(journal.path) == [committed]
        release.set()
        await journal.wait(pending)
        assert saved_records(journal.path) == [committed, {"later": "another observation"}]
    finally:
        release.set()
        await journal.close()
    assert not journal._thread.is_alive()


def test_saved_records_still_reject_corruption_in_a_complete_line(tmp_path):
    path = tmp_path / "corrupt.jsonl"
    path.write_bytes(b'{"consumed":true}\n{"broken":\n')
    with pytest.raises(json.JSONDecodeError):
        saved_records(path)


@pytest.mark.parametrize("writer_state", ["held", "failed"])
async def test_outside_actions_keep_their_own_permissions_when_plan_history_is_unavailable(
    tmp_path, monkeypatch, writer_state
):
    risky_catalog(tmp_path, monkeypatch)
    admissions = []

    class ObservedSource(SyntheticSource):
        async def invoke(self, request):
            admissions.append(request.action)
            return await super().invoke(request)

    app = create_app(source_factory=lambda kind, sid: ObservedSource(sid))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (
                await client.post(
                    "/api/v1/sessions",
                    json={
                        "sceneId": "lab-demo",
                        "realtimeFactor": 10,
                        "continueWithoutObservers": True,
                    },
                )
            ).json()
            route = "/api/v1/sessions/" + created["sessionId"]
            submitted = (await client.post("/api/v1/plans", json=plan(close_step(), close_step(2)))).json()
            approval = "/api/v1/approvals/" + submitted["approvalId"]
            assert (
                await client.post(approval + "/decision", json={"decision": "approve"})
            ).status_code == 200
            journal = app.state.manager.plans.journal
            entered, release = threading.Event(), threading.Event()
            original = journal._write_batch

            def unavailable_write(stream, entries):
                entered.set()
                if writer_state == "failed":
                    raise OSError("Injected actual plan history failure")
                assert release.wait(5), "The test must release its owned writer"
                original(stream, entries)

            monkeypatch.setattr(journal, "_write_batch", unavailable_write)
            context = {"planId": submitted["id"], "stepId": 1}
            body = {"entityKey": CENTRIFUGE, "action": "closeLid", **context}
            exact = asyncio.create_task(client.post(route + "/actions", json={**body, "requestId": "exact"}))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                if writer_state == "failed":
                    assert (await exact).status_code == 503
                    other_exact = await client.post(
                        route + "/actions",
                        json={
                            **body,
                            "requestId": "unconsumed-exact",
                            "stepId": 2,
                        },
                    )
                    assert other_exact.status_code == 503
                    assert other_exact.json()["error"]["code"] == "plan_persistence_unavailable"
                else:
                    assert not exact.done()
                assert admissions == []
                outside = await asyncio.wait_for(
                    client.post(
                        route + "/actions",
                        json={
                            **body,
                            "requestId": "ordinary-outside",
                            "action": "openLid",
                            "wait": True,
                        },
                    ),
                    1,
                )
                assert outside.status_code == 200 and outside.json()["status"] == "succeeded", outside.text
                assert admissions == ["openLid"]
                high = await asyncio.wait_for(
                    client.post(
                        route + "/actions",
                        json={
                            **body,
                            "requestId": "high-outside",
                        },
                    ),
                    1,
                )
                assert high.status_code == 202 and high.json()["status"] == "pending_approval", high.text
                assert admissions == ["openLid"]
                markers = [
                    row["message"]["data"]
                    for row in (await client.get(route + "/events", params={"timeoutSeconds": 0})).json()[
                        "events"
                    ]
                    if row["message"].get("event") == "plan.invocation"
                ]
                for invocation in (outside.json(), high.json()):
                    assert any(
                        item["invocationId"] == invocation["invocationId"] and item["outOfPlan"]
                        for item in markers
                    )
                await client.post(route + "/invocations/" + high.json()["invocationId"] + "/cancel")
                for invalid_context in ({"stepId": 99}, {"planRevision": 2}, {"planId": "unknown"}):
                    invalid = await client.post(
                        route + "/actions",
                        json={
                            **body,
                            "action": "openLid",
                            "requestId": "invalid-" + str(invalid_context),
                            **invalid_context,
                        },
                    )
                    assert invalid.status_code == 409
                    assert invalid.json()["error"]["code"] in ("invalid_plan_reference", "unknown_plan")
                await client.post(
                    route + "/commands",
                    json={
                        "type": "command.request",
                        "requestId": "reset",
                        "command": "reset",
                    },
                )
                stale = await client.post(
                    route + "/actions",
                    json={
                        **body,
                        "action": "openLid",
                        "requestId": "stale-target",
                    },
                )
                assert stale.status_code == 409 and stale.json()["error"]["code"] == "invalid_plan_reference"
                assert admissions == ["openLid"]
            finally:
                release.set()
                await asyncio.gather(exact, return_exceptions=True)
                await client.delete(route)
    assert not journal._thread.is_alive()


@pytest.mark.parametrize("reset_while_waiting", [False, True])
async def test_held_writer_does_not_block_health_or_dispatch_before_consumption_is_saved(
    tmp_path, monkeypatch, reset_while_waiting
):
    risky_catalog(tmp_path, monkeypatch)
    admissions = []

    class ObservedSource(SyntheticSource):
        async def invoke(self, request):
            admissions.append(request.invocationId)
            return await super().invoke(request)

    app = create_app(source_factory=lambda kind, sid: ObservedSource(sid))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()
            route = "/api/v1/sessions/" + created["sessionId"]
            await client.post(
                route + "/commands",
                json={
                    "type": "command.request",
                    "requestId": "pause",
                    "command": "pause",
                },
            )
            submitted = (await client.post("/api/v1/plans", json=plan(close_step()))).json()
            approval = "/api/v1/approvals/" + submitted["approvalId"]
            assert (
                await client.post(approval + "/decision", json={"decision": "approve"})
            ).status_code == 200
            journal = app.state.manager.plans.journal
            entered, release = threading.Event(), threading.Event()
            original = journal._write_batch

            def held_write(stream, entries):
                entered.set()
                assert release.wait(5), "The test must release its owned writer"
                original(stream, entries)

            monkeypatch.setattr(journal, "_write_batch", held_write)
            pending = asyncio.create_task(
                client.post(
                    route + "/actions",
                    json={
                        "requestId": "held-persistence",
                        "entityKey": CENTRIFUGE,
                        "action": "closeLid",
                        "planId": submitted["id"],
                        "stepId": 1,
                    },
                )
            )
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                assert not pending.done() and admissions == []
                cached = app.state.manager.plans.get(submitted["id"])
                assert cached.plan.steps[0].status == "running" and cached.results[1]["consumed"]
                assert journal.status["status"] == "pending"
                # The loop must run this unrelated actual HTTP request while disk work is held.
                health = await asyncio.wait_for(client.get("/api/v1/health"), 1)
                assert health.status_code == 200
                conflict = await client.post(approval + "/decision", json={"decision": "revoke"})
                assert conflict.status_code == 409 and admissions == []
                if reset_while_waiting:
                    reset = await client.post(
                        route + "/commands",
                        json={
                            "type": "command.request",
                            "requestId": "reset",
                            "command": "reset",
                        },
                    )
                    assert reset.status_code == 200
                release.set()
                accepted = await pending
                if reset_while_waiting:
                    assert accepted.status_code == 409 and admissions == []
                    assert accepted.json()["error"]["code"] == "invalid_plan_reference"
                else:
                    assert accepted.json()["status"] == "accepted", accepted.text
                    assert len(admissions) == 1
                saved = saved_records(journal.path)
                assert any(
                    row["results"].get("1", {}).get("consumed")
                    and row["plan"]["steps"][0]["status"] == "running"
                    for row in saved
                )
            finally:
                release.set()
                await asyncio.gather(pending, return_exceptions=True)
                await client.delete(route)
    assert not journal._thread.is_alive()


async def test_terminal_append_is_off_loop_fifo_and_close_drains_history(tmp_path, monkeypatch):
    app = create_app()
    loop_thread = threading.get_ident()
    writes = []
    original = Path.open

    def observe(path, *args, **kwargs):
        if path.name == "plans.jsonl" and (args[0] if args else kwargs.get("mode", "r")) == "a":
            writes.append(threading.get_ident())
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observe)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (
                await client.post("/api/v1/sessions", json={"sceneId": "lab-demo", "realtimeFactor": 10})
            ).json()
            route = "/api/v1/sessions/" + created["sessionId"]
            submitted = (await client.post("/api/v1/plans", json=plan(close_step()))).json()
            terminal_writes = []
            journal = app.state.manager.plans.journal
            write_batch = journal._write_batch

            def observe_batch(stream, entries):
                for _, payload, _ in entries:
                    if json.loads(payload)["plan"]["steps"][0]["status"] == "done":
                        terminal_writes.append(threading.get_ident())
                write_batch(stream, entries)

            monkeypatch.setattr(journal, "_write_batch", observe_batch)
            result = await client.post(
                route + "/actions",
                json={
                    "requestId": "observed-terminal",
                    "entityKey": CENTRIFUGE,
                    "action": "closeLid",
                    "planId": submitted["id"],
                    "stepId": 1,
                    "wait": True,
                },
            )
            assert result.json()["status"] == "succeeded"
            await client.delete(route)
    store = app.state.manager.plans
    assert writes and all(thread != loop_thread for thread in writes)
    assert terminal_writes and all(thread != loop_thread for thread in terminal_writes)
    assert not store.journal._thread.is_alive()
    history = [json.loads(line) for line in store.path.read_text("utf-8").splitlines()]
    transitions = [row["plan"]["steps"][0]["status"] for row in history]
    assert transitions.index("pending") < transitions.index("running") < transitions.index("done")
    assert history[-1]["plan"]["status"] == "completed"
    assert store.journal.status["status"] == "saved"


async def test_actual_worker_failure_after_terminal_does_not_report_a_host_failure(tmp_path, monkeypatch):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()
            route = "/api/v1/sessions/" + created["sessionId"]
            await client.post(
                route + "/commands",
                json={
                    "type": "command.request",
                    "requestId": "pause",
                    "command": "pause",
                },
            )
            submitted = (await client.post("/api/v1/plans", json=plan(close_step()))).json()
            accepted = (
                await client.post(
                    route + "/actions",
                    json={
                        "requestId": "complete-before-disk-failure",
                        "entityKey": CENTRIFUGE,
                        "action": "closeLid",
                        "planId": submitted["id"],
                        "stepId": 1,
                    },
                )
            ).json()
            store = app.state.manager.plans

            def fail_write(stream, entries):
                raise OSError("Injected actual append failure")

            monkeypatch.setattr(store.journal, "_write_batch", fail_write)
            stepped = await client.post(
                route + "/commands",
                json={
                    "type": "command.request",
                    "requestId": "finish",
                    "command": "step",
                    "args": {"count": 300},
                },
            )
            assert stepped.status_code == 200
            terminal = (
                await client.get(
                    route + "/invocations/" + accepted["invocationId"],
                    params={"wait": True, "timeoutSeconds": 1},
                )
            ).json()
            assert terminal["status"] == "succeeded"
            unavailable = await client.get("/api/v1/plans/" + submitted["id"])
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"]["code"] == "plan_persistence_unavailable"
            assert store.get(submitted["id"]).plan.steps[0].status == "done"
            session = (await client.get(route)).json()
            assert session["state"] == "PAUSED" and session["error"] is None
            refused = await client.post(
                route + "/actions",
                json={
                    "requestId": "no-follow-up-grant",
                    "entityKey": CENTRIFUGE,
                    "action": "closeLid",
                    "planId": submitted["id"],
                    "stepId": 1,
                },
            )
            # A completed ordinary step is outside; it needs no persisted plan grant.
            assert refused.status_code == 202 and refused.json()["status"] == "accepted", refused.text
            assert store.get(submitted["id"]).plan.steps[0].status == "done"
            await client.delete(route)
    assert not store.journal._thread.is_alive()


async def test_intake_append_failure_ends_the_attempt_without_host_work_or_idempotency_loss(
    tmp_path, monkeypatch
):
    risky_catalog(tmp_path, monkeypatch)
    admissions = []

    class ObservedSource(SyntheticSource):
        async def invoke(self, request):
            admissions.append(request.invocationId)
            return await super().invoke(request)

    app = create_app(source_factory=lambda kind, sid: ObservedSource(sid))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()
            route = "/api/v1/sessions/" + created["sessionId"]
            submitted = (await client.post("/api/v1/plans", json=plan(close_step()))).json()
            approval = "/api/v1/approvals/" + submitted["approvalId"]
            assert (
                await client.post(approval + "/decision", json={"decision": "approve"})
            ).status_code == 200
            store = app.state.manager.plans

            def fail_write(stream, entries):
                raise OSError("Injected intake append failure")

            monkeypatch.setattr(store.journal, "_write_batch", fail_write)
            body = {
                "requestId": "failed-intake",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "planId": submitted["id"],
                "stepId": 1,
            }
            failed = await client.post(route + "/actions", json=body)
            assert failed.status_code == 503 and admissions == []
            result = failed.json()
            assert result["status"] == "rejected" and result["startedSimTime"] is None
            assert result["error"]["code"] == "plan_persistence_unavailable"
            checked = (await client.get(route + "/invocations/" + result["invocationId"])).json()
            assert checked == result
            assert (await client.post(route + "/actions", json=body)).json() == result
            assert store.get(submitted["id"]).plan.steps[0].status == "failed"
            events = (await client.get(route + "/events", params={"timeoutSeconds": 0})).json()["events"]
            assert any(
                row["message"].get("type") == "plan.update" and row["message"].get("status") == "failed"
                for row in events
            )
            actual = (await client.get(route + "/state", params={"keys": CENTRIFUGE})).json()
            assert actual["entities"][CENTRIFUGE]["values"]["lidOpen"] is True
            assert admissions == []
            await client.delete(route)
    assert not store.journal._thread.is_alive()


async def test_actual_revoke_write_failure_keeps_the_durable_decision_and_no_grant(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()
            route = "/api/v1/sessions/" + created["sessionId"]
            submitted = (await client.post("/api/v1/plans", json=plan(close_step()))).json()
            approval = "/api/v1/approvals/" + submitted["approvalId"]
            await client.post(approval + "/decision", json={"decision": "approve"})
            journal = app.state.manager.plans.journal

            def fail_write(stream, entries):
                raise OSError("Injected actual revoke append failure")

            monkeypatch.setattr(journal, "_write_batch", fail_write)
            revoked = await client.post(approval + "/decision", json={"decision": "revoke"})
            assert revoked.status_code == 503
            decision = (await client.get(approval)).json()
            assert decision["status"] == "revoked"
            assert [item["decision"] for item in decision["decisions"]] == ["approve", "revoke"]
            forbidden = await client.post(
                route + "/actions",
                json={
                    "requestId": "after-worker-revoke-failure",
                    "entityKey": CENTRIFUGE,
                    "action": "closeLid",
                    "planId": submitted["id"],
                    "stepId": 1,
                },
            )
            assert forbidden.status_code == 503
            assert forbidden.json()["error"]["code"] == "plan_persistence_unavailable"
            actual = (await client.get(route + "/state", params={"keys": CENTRIFUGE})).json()
            assert actual["entities"][CENTRIFUGE]["values"]["lidOpen"] is True
            await client.delete(route)
    assert not journal._thread.is_alive()

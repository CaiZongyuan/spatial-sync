"""Real Synthetic information-plane journeys through HTTP and WebSocket."""

import asyncio
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.schema import EntityObservations, EntitySnapshot, InvocationUpdate, SessionState

CENTRIFUGE = "/World/Lab/Centrifuge"
ROBOT = "/World/Lab/RobotA"


def create(client, **options):
    session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo", **options}).json()
    return session, f"/api/v1/sessions/{session['sessionId']}"


def action(client, route, request_id, name, *, key=CENTRIFUGE, params=None, **options):
    return client.post(
        route + "/actions",
        json={"requestId": request_id, "entityKey": key, "action": name, "params": params or {}, **options},
    )


def until(ws, kind, **fields):
    for _ in range(1000):
        value = receive_message(ws)
        if value["type"] == kind and all(value.get(key) == expected for key, expected in fields.items()):
            return value
    raise AssertionError(f"Missing {kind}: {fields}")


def control(ws, request_id, command, **args):
    ws.send_json({"type": "command.request", "requestId": request_id, "command": command, "args": args})
    return until(ws, "command.ack", requestId=request_id)


def test_open_lid_rejection_then_close_start_and_observations_precede_success():
    with TestClient(create_app()) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        route = f"/api/v1/sessions/{session['sessionId']}"
        rejected = client.post(
            route + "/actions",
            json={
                "requestId": "open-lid",
                "entityKey": CENTRIFUGE,
                "action": "start",
                "params": {"rpm": 500, "durationSeconds": 1},
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["code"] == "precondition_failed"
        assert rejected.json()["error"]["details"]["failed"][0]["actual"] is True
        closed = client.post(
            route + "/actions",
            json={
                "requestId": "close-lid",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "wait": True,
                "timeoutSeconds": 4,
            },
        )
        assert closed.status_code == 200 and closed.json()["status"] == "succeeded", closed.text
        started = client.post(
            route + "/actions",
            json={
                "requestId": "run",
                "entityKey": CENTRIFUGE,
                "action": "start",
                "params": {"rpm": 500, "durationSeconds": 1},
            },
        )
        assert started.status_code == 202, started.text
        invocation_id = started.json()["invocationId"]
        done = client.get(route + f"/invocations/{invocation_id}", params={"wait": True, "timeoutSeconds": 6})
        assert done.status_code == 200 and done.json()["status"] == "succeeded", done.text
        state = client.get(route + "/state", params={"keys": CENTRIFUGE}).json()["entities"][CENTRIFUGE]
        assert state["values"]["phase"] == "idle" and state["values"]["rpm"] == 0
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        terminal = next(
            item
            for item in events
            if item["message"]["type"] == "invocation.update"
            and item["message"]["invocationId"] == invocation_id
            and item["message"]["status"] == "succeeded"
        )
        final_observation = max(
            item["seq"]
            for item in events
            if item["message"]["type"] == "entity.observations"
            and any(
                change["key"] == CENTRIFUGE and change["values"].get("phase") == "idle"
                for change in item["message"]["changes"]
            )
        )
        assert final_observation < terminal["seq"]
        completed_event = next(
            item
            for item in events
            if item["message"]["type"] == "entity.event" and item["message"]["event"] == "runCompleted"
        )
        assert final_observation < completed_event["seq"] < terminal["seq"]


def test_running_busy_precedes_conditions_pause_freezes_and_cancel_then_reset():
    with TestClient(create_app()) as client:
        session, route = create(client, realtimeFactor=10)
        assert action(client, route, "close", "closeLid", wait=True).json()["status"] == "succeeded"
        started = action(client, route, "start", "start", params={"rpm": 1000, "durationSeconds": 60}).json()
        repeated = action(client, route, "second", "start", params={"rpm": 1000, "durationSeconds": 60})
        assert repeated.status_code == 409 and repeated.json()["error"]["code"] == "busy"
        assert repeated.json()["error"]["details"]["activeInvocationId"] == started["invocationId"]
        with client.websocket_connect(route + "/ws") as ws:
            until(ws, "server.ready")
            assert control(ws, "pause", "pause")["status"] == "applied"
            before = client.get(route + f"/invocations/{started['invocationId']}").json()
            time.sleep(0.06)
            assert client.get(route + f"/invocations/{started['invocationId']}").json() == before
            timed = client.get(
                route + f"/invocations/{started['invocationId']}",
                params={"wait": True, "timeoutSeconds": 0.02},
            ).json()
            assert timed["timedOut"] is True and timed["status"] not in ("succeeded", "cancelled")
            cancelled = client.post(route + f"/invocations/{started['invocationId']}/cancel")
            assert cancelled.status_code == 202
            assert control(ws, "resume", "resume")["status"] == "applied"
            done = client.get(
                route + f"/invocations/{started['invocationId']}", params={"wait": True, "timeoutSeconds": 3}
            ).json()
            assert done["status"] == "cancelled"
            active = action(
                client, route, "reset-active", "start", params={"rpm": 1000, "durationSeconds": 60}
            ).json()
            assert control(ws, "reset", "reset")["status"] == "applied"
            # Subscribe to the reset revision's reliable info barrier.
            ws.send_json({"type": "client.subscribe", "sceneRevision": session["sceneRevision"]})
            snapshot = until(ws, "entity.snapshot", epoch=1)
            failed = client.get(route + f"/invocations/{active['invocationId']}").json()
            assert failed["status"] == "failed" and failed["error"]["code"] == "session_reset"
            assert (
                next(e for e in snapshot["entities"] if e["key"] == CENTRIFUGE)["values"]["lidOpen"] is True
            )


def test_catalog_discovery_schema_rejections_and_actor_audit():
    with TestClient(create_app()) as client:
        _, route = create(client)
        catalog = client.get(route + "/catalog")
        assert catalog.status_code == 200
        assert (
            client.get(route + "/catalog", headers={"If-None-Match": catalog.headers["etag"]}).status_code
            == 304
        )
        assert len(client.get(route + "/entities", params={"type": "tube/v1"}).json()["entities"]) == 20
        assert (
            client.get(route + "/entity", params={"key": CENTRIFUGE}).json()["definition"]["type"]
            == "centrifuge/v1"
        )
        for key, name, params, status, code in [
            ("/Missing", "start", {}, 404, "unknown_entity"),
            (CENTRIFUGE, "missing", {}, 404, "unknown_action"),
            (CENTRIFUGE, "start", {"rpm": "fast", "durationSeconds": 1}, 422, "invalid_params"),
        ]:
            response = action(client, route, code, name, key=key, params=params)
            assert response.status_code == status and response.json()["error"]["code"] == code
        response = client.post(
            route + "/actions",
            headers={"X-Actor": "agent:test-agent"},
            json={
                "requestId": "actor",
                "entityKey": CENTRIFUGE,
                "action": "start",
                "params": {"rpm": 1000, "durationSeconds": 1},
            },
        )
        assert response.json()["actor"] == {"kind": "agent", "name": "test-agent"}
        assert len(client.get(route + "/invocations").json()["invocations"]) == 4


def test_websocket_info_false_snapshot_order_and_shared_action_updates():
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.02))) as client:
        session, route = create(client, realtimeFactor=10)
        with (
            client.websocket_connect(route + "/ws") as operator,
            client.websocket_connect(route + "/ws") as spatial,
        ):
            until(operator, "server.ready")
            until(spatial, "server.ready")
            for ws, info in [(operator, True), (spatial, False)]:
                ws.send_json(
                    {"type": "client.subscribe", "sceneRevision": session["sceneRevision"], "info": info}
                )
                until(ws, "subscription.ack")
                assert receive_message(ws)["type"] == "stream.snapshot"
            assert receive_message(operator)["type"] == "entity.snapshot"
            operator.send_json(
                {
                    "type": "action.request",
                    "requestId": "ws-close",
                    "entityKey": CENTRIFUGE,
                    "action": "closeLid",
                }
            )
            accepted = until(operator, "invocation.update", requestId="ws-close", status="accepted")
            succeeded = until(
                operator, "invocation.update", invocationId=accepted["invocationId"], status="succeeded"
            )
            assert succeeded["actor"]["kind"] == "user"
            # Pause provides a deterministic FIFO barrier after all action messages.
            control(operator, "pause-space", "pause")
            for _ in range(100):
                value = receive_message(spatial)
                assert not value["type"].startswith(("entity.", "invocation."))
                if value["type"] == "session.state" and value["state"] == "PAUSED":
                    break
            else:
                raise AssertionError("No pause barrier on spatial-only connection")


def test_concurrent_request_ids_dispatch_once_and_wait_and_events_wake():
    invocations = []

    class CountSource(SyntheticSource):
        async def invoke(self, request):
            invocations.append(request.invocationId)
            await asyncio.sleep(0.02)
            return await super().invoke(request)

    with TestClient(create_app(source_factory=lambda kind, sid: CountSource(sid))) as client:
        _, route = create(client, realtimeFactor=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: action(client, route, "same", "closeLid").json(), range(2)))
        assert results[0]["invocationId"] == results[1]["invocationId"] and len(invocations) == 1
        result = client.get(
            route + f"/invocations/{results[0]['invocationId']}", params={"wait": True, "timeoutSeconds": 3}
        ).json()
        assert result["status"] == "succeeded" and result["timedOut"] is False
        assert action(client, route, "same", "openLid").json()["invocationId"] == result["invocationId"]
        assert len(invocations) == 1
        assert (
            client.get(
                route + "/wait",
                params={
                    "key": CENTRIFUGE,
                    "property": "lidOpen",
                    "op": "equals",
                    "value": "false",
                    "timeoutSeconds": 0,
                },
            ).json()["satisfied"]
            is True
        )
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()
        after = events["lastSeq"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            waiter = pool.submit(
                client.get,
                route + "/wait",
                params={
                    "key": CENTRIFUGE,
                    "property": "lidOpen",
                    "op": "equals",
                    "value": "true",
                    "timeoutSeconds": 3,
                },
            )
            event_waiter = pool.submit(
                client.get, route + "/events", params={"after": after, "timeoutSeconds": 3}
            )
            assert action(client, route, "open", "openLid").status_code == 202
            assert waiter.result(timeout=4).json() == {"satisfied": True, "value": True, "timedOut": False}
            assert event_waiter.result(timeout=4).json()["events"]


def test_old_epoch_source_update_cannot_resurrect_an_invocation_after_reset():
    class DelayedOldEpochSource(SyntheticSource):
        async def events(self):
            async for event in super().events():
                yield event
                if isinstance(event, EntitySnapshot) and event.epoch == 1:
                    yield InvocationUpdate(
                        sceneRevision=event.sceneRevision,
                        epoch=0,
                        invocationId="inv-delayed-old",
                        requestId="delayed-old",
                        entityKey=CENTRIFUGE,
                        action="closeLid",
                        status="accepted",
                        createdSimTime=0,
                        createdAtMs=1,
                    )

    with TestClient(
        create_app(Settings(heartbeat_interval_seconds=0.02), lambda kind, sid: DelayedOldEpochSource(sid))
    ) as client:
        session, route = create(client)
        with client.websocket_connect(route + "/ws") as ws:
            until(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "sceneRevision": session["sceneRevision"]})
            until(ws, "entity.snapshot")
            control(ws, "epoch-reset", "reset")
            until(ws, "entity.snapshot", epoch=1)
            until(ws, "server.heartbeat")
        assert client.get(route + "/invocations/inv-delayed-old").status_code == 404
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        assert not any(item["message"].get("invocationId") == "inv-delayed-old" for item in events)


def risky_catalog(tmp_path, monkeypatch):
    """Load a real temporary high-risk definition through the production loader."""
    from apps.api.app.sources import synthetic_source
    from packages.info.catalog import load_catalog

    device_root = tmp_path / "devices"
    shutil.copytree(Path(__file__).resolve().parents[2] / "devices", device_root)
    definition_path = device_root / "types/centrifuge.v1.yaml"
    definition = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
    definition["actions"]["closeLid"]["risk"] = "high"
    definition_path.write_text(yaml.safe_dump(definition), encoding="utf-8")
    monkeypatch.setattr(
        synthetic_source,
        "load_catalog",
        lambda manifest, scene_id, registry: load_catalog(manifest, scene_id, registry, root=device_root),
    )


def test_viewer_rejection_is_audited_actor_cannot_authorize_and_agent_cannot_approve(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    settings = Settings(
        remote=True,
        operator_token="operator",
        viewer_token="viewer",
        allowed_origins=("http://allowed.test",),
    )
    operator = {"Authorization": "Bearer operator", "Origin": "http://allowed.test"}
    viewer = {"Authorization": "Bearer viewer", "Origin": "http://allowed.test", "X-Actor": "user:operator"}
    with TestClient(create_app(settings), headers=operator) as client:
        _, route = create(client, approval={"high": "require"}, realtimeFactor=10)
        rejected = client.post(
            route + "/actions",
            headers=viewer,
            json={"requestId": "read-only", "entityKey": CENTRIFUGE, "action": "closeLid"},
        )
        assert rejected.status_code == 403 and rejected.json()["error"]["code"] == "not_permitted"
        assert (
            client.get(route + f"/invocations/{rejected.json()['invocationId']}").json()["status"]
            == "rejected"
        )
        with client.websocket_connect(route + "/ws", headers=viewer) as ws:
            ready = until(ws, "server.ready")
            assert ready["role"] == "viewer"
            ws.send_json({"type": "client.subscribe", "sceneRevision": ready["sceneRevision"]})
            until(ws, "entity.snapshot")
            ws.send_json(
                {
                    "type": "action.request",
                    "requestId": "viewer-ws",
                    "entityKey": CENTRIFUGE,
                    "action": "closeLid",
                }
            )
            denied = until(ws, "invocation.update", requestId="viewer-ws")
            assert denied["status"] == "rejected" and denied["error"]["code"] == "not_permitted"
        pending = action(client, route, "risky", "closeLid").json()
        assert pending["status"] == "pending_approval"
        assert (
            client.post(
                route + f"/invocations/{pending['invocationId']}/approve",
                headers={**operator, "X-Actor": "agent:agent-name"},
                json={"decision": "approve"},
            ).status_code
            == 403
        )
        assert (
            client.get(route + "/entity", params={"key": CENTRIFUGE}).json()["state"]["values"]["lidOpen"]
            is True
        )
        approved = client.post(
            route + f"/invocations/{pending['invocationId']}/approve", json={"decision": "approve"}
        )
        assert approved.status_code == 202
        completed = client.get(
            route + f"/invocations/{pending['invocationId']}", params={"wait": True, "timeoutSeconds": 3}
        ).json()
        assert completed["status"] == "succeeded"


def test_approval_deny_timeout_and_recheck_current_interlock(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    with TestClient(create_app(Settings(approval_timeout_seconds=0.05))) as client:
        _, route = create(client, approval={"high": "require"})
        pending = action(client, route, "deny", "closeLid").json()
        denied = client.post(
            route + f"/invocations/{pending['invocationId']}/approve", json={"decision": "deny"}
        )
        assert denied.json()["status"] == "rejected" and denied.json()["error"]["code"] == "approval_denied"
        pending = action(client, route, "expire", "closeLid").json()
        expired = client.get(
            route + f"/invocations/{pending['invocationId']}", params={"wait": True, "timeoutSeconds": 1}
        ).json()
        assert expired["status"] == "rejected" and expired["error"]["code"] == "approval_denied"
    with TestClient(create_app()) as client:
        _, route = create(client, approval={"high": "require"}, realtimeFactor=20)
        pending = action(client, route, "recheck", "closeLid").json()
        moved = action(
            client,
            route,
            "move-zone",
            "moveTo",
            key=ROBOT,
            params={"location": "centrifuge.slot1", "pose": "approach"},
            wait=True,
        ).json()
        assert moved["status"] == "succeeded"
        rejected = client.post(
            route + f"/invocations/{pending['invocationId']}/approve", json={"decision": "approve"}
        )
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "precondition_failed"
        assert rejected.json()["error"]["details"]["failed"][0]["actual"] == "centrifuge"


def test_concurrent_approval_requests_dispatch_once(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    dispatched = []

    class CountSource(SyntheticSource):
        async def invoke(self, request):
            dispatched.append(request.invocationId)
            await asyncio.sleep(0.03)
            return await super().invoke(request)

    with TestClient(create_app(source_factory=lambda kind, sid: CountSource(sid))) as client:
        _, route = create(client, approval={"high": "require"}, realtimeFactor=10)
        pending = action(client, route, "risky-once", "closeLid").json()
        assert dispatched == []
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(
                pool.map(
                    lambda _: client.post(
                        route + f"/invocations/{pending['invocationId']}/approve",
                        json={"decision": "approve"},
                    ).json(),
                    range(2),
                )
            )
        assert replies[0]["invocationId"] == replies[1]["invocationId"] == pending["invocationId"]
        assert dispatched == [pending["invocationId"]]


def test_real_single_tube_load_is_rejected_as_imbalanced():
    with TestClient(create_app()) as client:
        _, route = create(client, realtimeFactor=30)
        placed = action(
            client,
            route,
            "place-tube",
            "pickPlace",
            key=ROBOT,
            params={"from": "rack.A1", "to": "centrifuge.slot1"},
            wait=True,
        ).json()
        assert placed["status"] == "succeeded"
        assert action(client, route, "home", "home", key=ROBOT, wait=True).json()["status"] == "succeeded"
        assert action(client, route, "close-one", "closeLid", wait=True).json()["status"] == "succeeded"
        rejected = action(client, route, "imbalanced", "start", params={"rpm": 500, "durationSeconds": 1})
        assert rejected.status_code == 409 and rejected.json()["error"]["code"] == "imbalanced"


@pytest.mark.parametrize("accepted_first", [False, True])
async def test_dispatch_timeout_is_bounded_and_concurrent_reset_cancels_reserved_work(accepted_first):
    cancelled = []

    class SlowSource(SyntheticSource):
        async def invoke(self, request):
            if accepted_first:
                update = await super().invoke(request)
            await asyncio.sleep(0.7)
            return update if accepted_first else await super().invoke(request)

        async def cancel(self, invocation_id):
            cancelled.append(invocation_id)
            return await super().cancel(invocation_id)

    app = create_app(source_factory=lambda kind, sid: SlowSource(sid))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            session = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()
            route = f"/api/v1/sessions/{session['sessionId']}"
            start = time.monotonic()
            response = await client.post(
                route + "/actions", json={"requestId": "slow", "entityKey": CENTRIFUGE, "action": "closeLid"}
            )
            assert response.json()["error"]["code"] == "source_unavailable"
            assert response.json()["status"] == ("failed" if accepted_first else "rejected")
            assert time.monotonic() - start < 0.65
            await asyncio.sleep(0)
            assert response.json()["invocationId"] in cancelled
            task = asyncio.create_task(
                client.post(
                    route + "/actions",
                    json={
                        "requestId": "reload-reserved",
                        "entityKey": ROBOT,
                        "action": "moveJoints",
                        "params": {"positions": [1, 0.3, -0.6, 0, 0, 0, 0]},
                    },
                )
            )
            await asyncio.sleep(0.02)
            assert (await client.post(route + "/reload")).status_code == 200
            result = (await task).json()
            assert result["status"] == "failed" and result["error"]["code"] == "session_reset"


def test_source_failed_lifecycle_ends_active_invocations_and_rejects_new_work():
    class FailedLifecycleSource(SyntheticSource):
        async def events(self):
            async for event in super().events():
                yield event
                if isinstance(event, InvocationUpdate) and event.status == "running":
                    frame = self.latest_frame()
                    yield SessionState(
                        sessionId=self.session_id,
                        sceneRevision=frame.sceneRevision,
                        state="FAILED",
                        epoch=frame.epoch,
                        seq=frame.seq,
                        simTime=frame.simTime,
                        realtimeFactor=1,
                        actualRtf=0,
                        cause={
                            "kind": "error",
                            "code": "source_unavailable",
                            "message": "Injected control loss",
                        },
                    )
                    return

    with TestClient(create_app(source_factory=lambda kind, sid: FailedLifecycleSource(sid))) as client:
        _, route = create(client)
        accepted = action(client, route, "active-failure", "closeLid").json()
        failed = client.get(
            route + f"/invocations/{accepted['invocationId']}", params={"wait": True, "timeoutSeconds": 2}
        )
        assert failed.status_code == 200 and failed.json()["status"] == "failed"
        assert failed.json()["error"]["code"] == "source_unavailable"
        rejected = action(client, route, "after-failure", "closeLid")
        assert rejected.status_code == 503 and rejected.json()["error"]["code"] == "source_unavailable"


def test_cancel_terminal_cannot_bypass_its_final_observations(monkeypatch):
    from apps.api.app.sources import synthetic_source
    from packages.programs.robot_arm.virtual import RobotArmProgram

    class ImmediateCancelProgram(RobotArmProgram):
        def cancel(self, invocation, ctx):
            ctx.set(phase="idle")
            invocation.cancelled()
            self.handle = None

    monkeypatch.setitem(synthetic_source.REGISTRY, "robot_arm.virtual", ImmediateCancelProgram)

    class DelayedObservationSource(SyntheticSource):
        async def events(self):
            async for event in super().events():
                if isinstance(event, EntityObservations) and any(
                    change.key == ROBOT and change.values.get("phase") == "idle" for change in event.changes
                ):
                    await asyncio.sleep(0.05)
                yield event

    with TestClient(create_app(source_factory=lambda kind, sid: DelayedObservationSource(sid))) as client:
        _, route = create(client)
        accepted = action(
            client,
            route,
            "moving-cancel",
            "moveJoints",
            key=ROBOT,
            params={"positions": [1, 0.3, -0.6, 0, 0, 0, 0], "speed": 0.1},
        ).json()
        moving = client.get(
            route + "/wait",
            params={
                "key": ROBOT,
                "property": "phase",
                "op": "equals",
                "value": '"moving"',
                "timeoutSeconds": 2,
            },
        ).json()
        assert moving["satisfied"] is True
        client.post(route + f"/invocations/{accepted['invocationId']}/cancel")
        cancelled = client.get(
            route + f"/invocations/{accepted['invocationId']}", params={"wait": True, "timeoutSeconds": 2}
        ).json()
        assert cancelled["status"] == "cancelled"
        state = client.get(route + "/state", params={"keys": ROBOT}).json()["entities"][ROBOT]
        assert state["values"]["phase"] == "idle"


def test_remote_mutation_limit_is_shared_by_actions_cancellation_approval_and_commands():
    headers = {"Authorization": "Bearer operator", "Origin": "http://allowed.test"}
    settings = Settings(remote=True, operator_token="operator", allowed_origins=("http://allowed.test",))
    with TestClient(create_app(settings), headers=headers) as client:
        session, route = create(client)
        with client.websocket_connect(route + "/ws", headers=headers) as ws:
            until(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "sceneRevision": session["sceneRevision"]})
            until(ws, "entity.snapshot")
            for index in range(60):
                ws.send_json(
                    {
                        "type": "action.request",
                        "requestId": f"limited-{index}",
                        "entityKey": CENTRIFUGE,
                        "action": "missing",
                    }
                )
                assert (
                    until(ws, "invocation.update", requestId=f"limited-{index}")["error"]["code"]
                    == "unknown_action"
                )
            for message in [
                {"type": "action.request", "entityKey": CENTRIFUGE, "action": "missing"},
                {"type": "action.cancel", "invocationId": "missing"},
                {"type": "action.approve", "invocationId": "missing", "decision": "approve"},
                {"type": "command.request", "command": "pause"},
            ]:
                message["requestId"] = "over-limit"
                ws.send_json(message)
                for _ in range(100):
                    error = receive_message(ws)
                    if error["type"] in ("server.error", "invocation.update"):
                        break
                assert error["type"] == "server.error", error
                assert error["code"] == "invalid_message" and "rate limit" in error["message"].lower()
        assert len(client.get(route + "/invocations", params={"limit": 1000}).json()["invocations"]) == 60


@pytest.mark.parametrize("rpm", [10**20, 10**1000], ids=["over64bits", "overfloatrange"])
def test_oversized_numeric_parameter_is_rejected_and_audited_without_losing_original_integer(rpm):
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        session, route = create(client, realtimeFactor=10)
        with client.websocket_connect(route + "/ws") as ws:
            until(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "sceneRevision": session["sceneRevision"]})
            until(ws, "entity.snapshot")
            rejected = action(
                client, route, "huge-number", "start", params={"rpm": rpm, "durationSeconds": 1}
            )
            assert rejected.status_code == 422, rejected.text
            value = rejected.json()
            assert value["error"]["code"] == "invalid_params" and value["params"]["rpm"] == rpm
            observed = until(ws, "invocation.update", requestId="huge-number")
            assert observed["params"]["rpm"] == rpm
        retained = client.get(route + f"/invocations/{value['invocationId']}")
        assert retained.status_code == 200 and retained.json()["params"]["rpm"] == rpm
        assert (
            action(client, route, "healthy-after-number", "closeLid", wait=True).json()["status"]
            == "succeeded"
        )


def test_authoritative_acceptance_precedes_running_even_when_direct_reply_is_delayed():
    class DelayedReplySource(SyntheticSource):
        async def invoke(self, request):
            accepted = await super().invoke(request)
            await asyncio.sleep(0.08)
            return accepted

    with TestClient(create_app(source_factory=lambda kind, sid: DelayedReplySource(sid))) as client:
        session, route = create(client, realtimeFactor=10)
        with client.websocket_connect(route + "/ws") as ws:
            until(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "sceneRevision": session["sceneRevision"]})
            until(ws, "entity.snapshot")
            ws.send_json(
                {
                    "type": "action.request",
                    "requestId": "delayed-acceptance",
                    "entityKey": CENTRIFUGE,
                    "action": "closeLid",
                }
            )
            statuses = []
            for _ in range(100):
                message = receive_message(ws)
                if message["type"] == "invocation.update" and message["requestId"] == "delayed-acceptance":
                    statuses.append(message["status"])
                    if message["status"] == "succeeded":
                        break
            assert statuses[0] == "accepted" and statuses[-1] == "succeeded", statuses
            assert "running" in statuses


def test_unrepresentable_json_number_returns_structured_validation_error_and_keeps_gateway_usable():
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        _, route = create(client)
        response = client.post(
            route + "/actions",
            headers={"Content-Type": "application/json"},
            content='{"requestId":"infinite-literal","entityKey":"/World/Lab/Centrifuge",'
            '"action":"start","params":{"rpm":1e1000,"durationSeconds":1}}',
        )
        assert response.status_code == 422, response.text
        assert any(
            item["type"] == "finite_number" and "rpm" in item["loc"] for item in response.json()["detail"]
        )
        assert client.get("/api/v1/health").json()["status"] == "ok"


def test_reset_acknowledgement_closes_old_work_before_delayed_information_snapshot():
    class DelayedResetSnapshot(SyntheticSource):
        def __init__(self, session_id):
            super().__init__(session_id)
            self.deferred = self.forwarded = False
            self.defer_task = None

        async def events(self):
            async for event in super().events():
                if isinstance(event, EntitySnapshot) and event.epoch == 1 and not self.deferred:
                    self.deferred = True

                    async def later(snapshot):
                        await asyncio.sleep(0.15)
                        self._events.put_nowait(snapshot)

                    self.defer_task = asyncio.create_task(later(event))
                    continue
                yield event
                if isinstance(event, EntitySnapshot) and event.epoch == 1:
                    self.forwarded = True

        def stats(self):
            return {"deferredResetSnapshotApplied": self.forwarded}

        async def close(self):
            if self.defer_task:
                self.defer_task.cancel()
                await asyncio.gather(self.defer_task, return_exceptions=True)
            await super().close()

    with TestClient(create_app(source_factory=lambda kind, sid: DelayedResetSnapshot(sid))) as client:
        _, route = create(client)
        original = action(client, route, "before-reset", "closeLid").json()
        with client.websocket_connect(route + "/ws") as ws:
            until(ws, "server.ready")
            assert control(ws, "reset-barrier", "reset")["status"] == "applied"
            ended = client.get(route + f"/invocations/{original['invocationId']}").json()
            assert ended["status"] == "failed" and ended["error"]["code"] == "session_reset"
            cursor = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["lastSeq"]
            fresh = action(client, route, "after-reset", "closeLid")
            assert fresh.status_code == 202 and fresh.json()["epoch"] == 1, fresh.text
            moving = action(
                client,
                route,
                "moving-after-reset",
                "moveJoints",
                key=ROBOT,
                params={"positions": [1, 0.3, -0.6, 0, 0, 0, 0]},
            )
            assert moving.status_code == 202
            deadline = time.monotonic() + 2
            while not client.get(route + "/stats").json()["deferredResetSnapshotApplied"]:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            robot = client.get(route + "/state", params={"keys": ROBOT}).json()["entities"][ROBOT]
            assert robot["values"]["phase"] == "moving"
            active = client.get(route + "/entity", params={"key": CENTRIFUGE}).json()["active"]
            assert fresh.json()["invocationId"] in {item["invocationId"] for item in active}
            following = client.get(route + "/events", params={"after": cursor, "timeoutSeconds": 0}).json()[
                "events"
            ]
            assert not any(item["message"]["type"] == "entity.snapshot" for item in following)


def test_session_deduplication_survives_terminal_eviction_and_event_gap_is_explicit():
    dispatched = []

    class CountSource(SyntheticSource):
        async def invoke(self, request):
            dispatched.append(request.invocationId)
            return await super().invoke(request)

    with TestClient(create_app(source_factory=lambda kind, sid: CountSource(sid))) as client:
        _, route = create(client, realtimeFactor=30)
        original = action(client, route, "original", "closeLid", wait=True).json()
        assert original["status"] == "succeeded"
        with client.websocket_connect(route + "/ws") as ws:
            until(ws, "server.ready")
            control(ws, "pause-history", "pause")
        for index in range(10_001):
            response = action(client, route, f"rejected-{index}", "missing")
            assert response.status_code == 404
        assert len(client.get(route + "/invocations", params={"limit": 1000}).json()["invocations"]) == 1000
        replay = action(client, route, "original", "openLid", wait=True).json()
        assert replay == original and len(dispatched) == 1
        old = client.get(route + f"/invocations/{original['invocationId']}", params={"wait": True}).json()
        assert old == original
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()
        assert events["gap"] is True and len(events["events"]) == 10_000
        assert (
            client.get(route + "/entity", params={"key": CENTRIFUGE}).json()["state"]["values"]["lidOpen"]
            is False
        )


def test_unavailable_history_is_a_structured_query_error_across_http_and_websocket(monkeypatch):
    from packages.info.invocations import HistoryUnavailable, InvocationTable

    with TestClient(create_app()) as client:
        _, route = create(client)
        original = action(client, route, "known-history", "missing").json()
        invocation_id = original["invocationId"]
        canonical_lookup = InvocationTable.lookup

        async def unavailable(table, requested_id):
            if requested_id == invocation_id:
                raise HistoryUnavailable(invocation_id)
            return await canonical_lookup(table, requested_id)

        monkeypatch.setattr(InvocationTable, "lookup", unavailable)
        responses = [
            client.get(route + f"/invocations/{invocation_id}", params={"wait": True}),
            action(client, route, "known-history", "closeLid", wait=True),
            client.post(route + f"/invocations/{invocation_id}/cancel"),
            client.post(route + f"/invocations/{invocation_id}/approve", json={"decision": "approve"}),
        ]
        for response in responses:
            assert response.status_code == 503
            assert response.json()["detail"] == {
                "code": "history_unavailable",
                "message": "Invocation history is unavailable",
                "invocationId": invocation_id,
            }
        assert client.get(route + "/invocations/unknown").status_code == 404
        with client.websocket_connect(route + "/ws") as ws:
            until(ws, "server.ready")
            for request in (
                {
                    "type": "action.request", "requestId": "known-history",
                    "entityKey": CENTRIFUGE, "action": "closeLid",
                },
                {"type": "action.cancel", "requestId": "cancel-history", "invocationId": invocation_id},
                {
                    "type": "action.approve", "requestId": "approve-history",
                    "invocationId": invocation_id, "decision": "approve",
                },
            ):
                ws.send_json(request)
                error = until(ws, "server.error", requestId=request["requestId"])
                assert error["code"] == "history_unavailable" and error["invocationId"] == invocation_id
        history = client.get(route + "/invocations").json()["invocations"]
        assert len(history) == 1 and history[0]["invocationId"] == invocation_id
        assert client.get(route + "/entity", params={"key": CENTRIFUGE}).json()["state"]["values"]["lidOpen"]

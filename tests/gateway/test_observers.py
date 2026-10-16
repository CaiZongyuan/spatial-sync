import asyncio
import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.session_manager import SessionManager, default_source_factory
from apps.api.app.settings import Settings
from apps.api.app.stream_broadcaster import ClientConnection
from packages.protocol.schema import Command, SessionOptions, Subscribe


def observer(session):
    client = ClientConnection(None, Settings())
    session.clients.add(client)
    return client


async def subscribe(session, client):
    await session.observer_subscribe(client, Subscribe(sceneRevision=session.manifest.sceneRevision))


async def test_zero_one_two_zero_subscribers_pause_and_resume_only_by_policy():
    manager = SessionManager(Settings(), default_source_factory)
    try:
        session = await manager.create(
            "lab-demo", "synthetic", SessionOptions(continueWithoutObservers=False)
        )
        assert session.state == "PAUSED" and session.summary()["autoPaused"]
        frozen = session.latest_frame().simTime
        await asyncio.sleep(0.03)
        assert session.latest_frame().simTime == frozen
        first, second = observer(session), observer(session)
        # Connected sockets alone are not observers; only accepted subscriptions count.
        assert session.state == "PAUSED"
        await subscribe(session, first)
        assert session.state == "RUNNING" and not session.summary()["autoPaused"]
        messages = [json.loads(item.payload) for item in list(first.reliable._queue)]
        assert [item["type"] for item in messages[:2]] == ["subscription.ack", "stream.snapshot"]
        resumed = next(item for item in messages if item["type"] == "session.state")
        assert resumed["cause"]["kind"] == "lifecycle"
        assert next(
            i for i, item in enumerate(messages) if item["type"] == "transform.frame"
        ) < messages.index(resumed)
        await subscribe(session, second)
        await session.remove_client(first)
        assert session.state == "RUNNING"
        await session.remove_client(second)
        assert session.state == "PAUSED" and session.summary()["autoPaused"]
    finally:
        await manager.close()


async def test_explicit_user_pause_is_not_overridden_by_observer_return():
    manager = SessionManager(Settings(), default_source_factory)
    try:
        session = await manager.create(
            "lab-demo", "synthetic", SessionOptions(continueWithoutObservers=False)
        )
        first = observer(session)
        await subscribe(session, first)
        await session.execute(first, Command(requestId="manual-pause", command="pause"))
        assert session.state == "PAUSED" and not session.summary()["autoPaused"]
        await session.remove_client(first)
        second = observer(session)
        await subscribe(session, second)
        assert session.state == "PAUSED" and not session.summary()["autoPaused"]
    finally:
        await manager.close()


async def test_default_continues_without_subscribers_and_policy_survives_reload_close():
    manager = SessionManager(Settings(), default_source_factory)
    try:
        running = await manager.create("lab-demo", "synthetic", SessionOptions())
        for _ in range(100):
            if running.latest_frame().simTime > 0:
                break
            await asyncio.sleep(0.005)
        assert running.state == "RUNNING" and running.latest_frame().simTime > 0
        await running.close()
        session = await manager.create(
            "lab-demo", "synthetic", SessionOptions(continueWithoutObservers=False)
        )
        old_revision, old_seq = session.manifest.sceneRevision, session.seq
        await session.reload()
        assert session.state == "PAUSED" and session.summary()["autoPaused"]
        assert session.seq > old_seq and session.manifest.sceneRevision != old_revision
        client = observer(session)
        await subscribe(session, client)
        assert session.state == "RUNNING"
        await session.close()
        await session.remove_client(client)
        assert session.state == "STOPPED" and not session.summary()["autoPaused"]
    finally:
        await manager.close()


def test_create_api_exposes_strict_observer_policy_default_and_option():
    with TestClient(create_app()) as client:
        rejected = client.post(
            "/api/v1/sessions", json={"sceneId": "lab-demo", "continueWithoutObservers": "false"}
        )
        assert rejected.status_code == 422
        session = client.post(
            "/api/v1/sessions", json={"sceneId": "lab-demo", "continueWithoutObservers": False}
        ).json()
        assert session["state"] == "PAUSED" and session["continueWithoutObservers"] is False
        sid = session["sessionId"]
        with client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as ws:
            assert ws.receive_json()["sessionState"] == "PAUSED"
            ws.send_json(
                {
                    "type": "client.subscribe",
                    "protocolVersion": 1,
                    "sceneRevision": session["sceneRevision"],
                    "scope": "all",
                    "rateHz": 30,
                }
            )
            assert ws.receive_json()["type"] == "subscription.ack"
            assert ws.receive_json()["type"] == "stream.snapshot"
            for _ in range(10):
                message = ws.receive_json()
                if message["type"] == "session.state":
                    assert message["state"] == "RUNNING" and message["cause"]["kind"] == "lifecycle"
                    break
            else:
                raise AssertionError("No automatic resume state")
        for _ in range(50):
            summary = client.get(f"/api/v1/sessions/{sid}").json()
            if summary["state"] == "PAUSED":
                break
        assert summary["state"] == "PAUSED" and summary["autoPaused"]
        assert client.delete(f"/api/v1/sessions/{sid}").status_code == 204

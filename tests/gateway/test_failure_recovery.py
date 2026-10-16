"""Public authentication and failed-source recovery regressions."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.schema import CommandAck, CommandResult


def receive(ws, kind, **fields):
    for _ in range(100):
        message = receive_message(ws)
        if message["type"] == kind and all(message.get(key) == value for key, value in fields.items()):
            return message
    pytest.fail(f"No {kind} matching {fields}")


def test_non_ascii_bearer_is_rejected_without_breaking_valid_roles():
    settings = Settings(
        remote=True,
        operator_token="operator-test",
        viewer_token="viewer-test",
        allowed_origins=("http://allowed.test",),
    )
    operator = {"Authorization": "Bearer operator-test", "Origin": "http://allowed.test"}
    viewer = {"Authorization": "Bearer viewer-test", "Origin": "http://allowed.test"}
    malformed = {b"Authorization": b"Bearer \xe9", b"Origin": b"http://allowed.test"}
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        response = client.get("/api/v1/scenes", headers=malformed)
        assert response.status_code == 401, response.text
        summary = client.post("/api/v1/sessions", headers=operator, json={"sceneId": "lab-demo"}).json()
        route = f"/api/v1/sessions/{summary['sessionId']}"
        with pytest.raises(WebSocketDisconnect) as disconnected:
            with client.websocket_connect(route + "/ws", headers=malformed):
                pass
        assert disconnected.value.code == 1008
        assert client.get(route, headers=viewer).status_code == 200
        assert client.post(route + "/reload", headers=viewer).status_code == 403
        with client.websocket_connect(route + "/ws", headers=operator) as ws:
            assert receive_message(ws)["type"] == "server.ready"
            ws.send_json({"type": "command.request", "requestId": "valid", "command": "pause"})
            for _ in range(100):
                message = receive_message(ws)
                if message["type"] == "command.ack":
                    assert message["status"] == "applied"
                    break
            else:
                pytest.fail("valid operator did not receive an acknowledgement")


@pytest.mark.parametrize("starts_before_failure", [False, True])
def test_failed_reload_and_restart_keep_last_valid_sample_heartbeat_and_recover(starts_before_failure):
    active = set()
    starts = 0

    class TrackedSource(SyntheticSource):
        async def start(self, scene_id, options):
            active.add(id(self))
            return await super().start(scene_id, options)

        async def close(self):
            await super().close()
            active.discard(id(self))

    class StartFailure(TrackedSource):
        async def start(self, scene_id, options):
            active.add(id(self))
            if starts_before_failure:
                await super().start(scene_id, options)
            # Keep LOADING observable across several heartbeat deadlines.
            await asyncio.sleep(0.04)
            raise RuntimeError("replacement source unavailable")

    def factory(kind, session_id):
        nonlocal starts
        starts += 1
        return StartFailure(session_id) if starts in (2, 3) else TrackedSource(session_id)

    app = create_app(Settings(heartbeat_interval_seconds=0.01), factory)
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
        assert created.status_code == 201, created.text
        summary = created.json()
        route = f"/api/v1/sessions/{summary['sessionId']}"
        with client.websocket_connect(route + "/ws") as ws:
            receive(ws, "server.ready")
            ws.send_json({"type": "command.request", "requestId": "pause", "command": "pause"})
            receive(ws, "command.ack", requestId="pause", status="applied")
            receive(ws, "session.state", state="PAUSED")
            previous = client.get(route).json()
            snapshot = client.get(route + "/snapshot").json()
            for operation in ("reload", "restart"):
                failed = client.post(route + "/" + operation)
                assert failed.status_code == 503, failed.text
                current = client.get(route).json()
                assert current["state"] == "FAILED"
                assert current["error"] == {
                    "code": "source_unavailable",
                    "message": "replacement source unavailable",
                }
                for key in ("sceneRevision", "revisionNumericId", "epoch", "seq", "simTime"):
                    assert current[key] == previous[key], (key, current, previous)
                assert client.get(route + "/snapshot").json() == snapshot
                joint_state = client.get(route + "/joints/state", params={"key": "/World/Lab/Robot/Joint2"})
                assert joint_state.status_code == 503, joint_state.text
                assert joint_state.json()["detail"]["code"] == "source_unavailable"
                receive(ws, "session.state", state="LOADING")
                loading = receive(ws, "server.heartbeat", state="LOADING")
                assert loading["lastSeq"] == snapshot["seq"]
                error = receive(ws, "server.error", code="source_unavailable")
                state = receive(ws, "session.state", state="FAILED")
                assert state["sceneRevision"] == snapshot["sceneRevision"]
                assert state["cause"] == {
                    "kind": "error",
                    "code": "source_unavailable",
                    "message": error["message"],
                }
                heartbeat = receive(ws, "server.heartbeat", state="FAILED")
                assert heartbeat["lastSeq"] == snapshot["seq"]
                assert heartbeat["simTime"] == snapshot["simTime"] and heartbeat["actualRtf"] == 0
                assert client.get("/api/v1/health").json()["status"] == "ok"
                assert not active
            with client.websocket_connect(route + "/ws") as late:
                ready = receive(late, "server.ready")
                assert ready["sessionState"] == "FAILED"
                assert ready["sceneRevision"] == snapshot["sceneRevision"]
                receive(late, "server.heartbeat", state="FAILED")
            recovered = client.post(route + "/restart")
            assert recovered.status_code == 200, recovered.text
            current = recovered.json()
            assert current["state"] == "RUNNING" and current["error"] is None
            assert current["revisionNumericId"] == previous["revisionNumericId"] + 1
            assert current["sceneRevision"] != previous["sceneRevision"]
            assert current["seq"] > snapshot["seq"]
            receive(ws, "scene.replaced", sceneRevision=current["sceneRevision"])
            receive(ws, "session.state", state="RUNNING")
            ws.send_json({"type": "command.request", "requestId": "pause-again", "command": "pause"})
            receive(ws, "command.ack", requestId="pause-again", status="applied")
    assert not active


def test_initial_start_failure_has_details_releases_source_and_allows_later_creation():
    closed = []
    starts = 0

    class StartFailure(SyntheticSource):
        async def start(self, scene_id, options):
            raise RuntimeError("initial source unavailable")

        async def close(self):
            closed.append(self.session_id)
            await super().close()

    def factory(kind, session_id):
        nonlocal starts
        starts += 1
        return StartFailure(session_id) if starts == 1 else SyntheticSource(session_id)

    with TestClient(create_app(source_factory=factory), raise_server_exceptions=False) as client:
        failed = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
        assert failed.status_code == 503
        assert failed.json()["detail"] == {
            "code": "source_unavailable",
            "message": "initial source unavailable",
        }
        assert len(closed) == 1
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
        with pytest.raises(WebSocketDisconnect) as disconnected:
            with client.websocket_connect(f"/api/v1/sessions/{closed[0]}/ws"):
                pass
        assert disconnected.value.code == 1008
        assert client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).status_code == 201


def test_startup_policy_rejection_does_not_commit_or_announce_candidate_revision():
    starts = 0
    closed_candidates = []

    class PolicyFailure(SyntheticSource):
        async def execute(self, command):
            await asyncio.sleep(0.04)
            return CommandResult(
                ack=CommandAck(
                    requestId=command.requestId,
                    status="rejected",
                    reason="source_unavailable",
                    simTime=0,
                    epoch=0,
                )
            )

        async def close(self):
            closed_candidates.append(self.session_id)
            await super().close()

    def factory(kind, session_id):
        nonlocal starts
        starts += 1
        return PolicyFailure(session_id) if starts == 2 else SyntheticSource(session_id)

    with TestClient(
        create_app(Settings(heartbeat_interval_seconds=0.01), factory), raise_server_exceptions=False
    ) as client:
        previous = client.post(
            "/api/v1/sessions", json={"sceneId": "lab-demo", "continueWithoutObservers": False}
        ).json()
        assert previous["state"] == "PAUSED"
        route = f"/api/v1/sessions/{previous['sessionId']}"
        snapshot = client.get(route + "/snapshot").json()
        with client.websocket_connect(route + "/ws") as ws:
            receive(ws, "server.ready")
            failed = client.post(route + "/reload")
            assert failed.status_code == 503, failed.text
            current = client.get(route).json()
            assert current["sceneRevision"] == previous["sceneRevision"]
            assert current["revisionNumericId"] == previous["revisionNumericId"]
            assert current["state"] == "FAILED"
            assert current["error"]["message"] == "Source rejected observer-policy pause"
            assert client.get(route + "/snapshot").json() == snapshot
            seen = []
            for _ in range(100):
                message = receive_message(ws)
                seen.append(message)
                if message["type"] == "session.state" and message["state"] == "FAILED":
                    assert message["sceneRevision"] == previous["sceneRevision"]
                    break
            else:
                pytest.fail("No FAILED state after rejected startup policy")
            assert all(item["type"] != "scene.replaced" for item in seen)
            loading = next(
                item for item in seen if item["type"] == "server.heartbeat" and item["state"] == "LOADING"
            )
            assert loading["lastSeq"] == snapshot["seq"]
            receive(ws, "server.heartbeat", state="FAILED")
            assert closed_candidates == [previous["sessionId"]]
            recovered = client.post(route + "/restart").json()
            assert recovered["state"] == "PAUSED" and recovered["autoPaused"]
            assert recovered["revisionNumericId"] == previous["revisionNumericId"] + 1
            assert recovered["sceneRevision"] != previous["sceneRevision"]
            receive(ws, "scene.replaced", sceneRevision=recovered["sceneRevision"])
            state = receive(ws, "session.state", state="PAUSED")
            assert state["sceneRevision"] == recovered["sceneRevision"]

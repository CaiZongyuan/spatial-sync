import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.schema import SceneManifest


def setup_session(client):
    response = client.post("/api/v1/sessions", json={"sceneId": "lab-demo", "source": "synthetic"})
    assert response.status_code == 201, response.text
    return response.json()


def next_type(ws, kind, request_id=None):
    for _ in range(100):
        value = receive_message(ws)
        if value["type"] == kind and (request_id is None or value.get("requestId") == request_id):
            return value
    raise AssertionError(f"No {kind}")


def command(ws, request_id, kind, args=None):
    ws.send_json({"type": "command.request", "requestId": request_id, "command": kind, "args": args or {}})
    return next_type(ws, "command.ack", request_id)


def open_subscription(ws, revision, **kwargs):
    ready = next_type(ws, "server.ready")
    ws.send_json(
        {
            "type": "client.subscribe",
            "protocolVersion": 2,
            "sceneRevision": revision,
            "rateHz": 30,
            **kwargs,
        }
    )
    ack = next_type(ws, "subscription.ack")
    return ready, ack, next_type(ws, "stream.snapshot")


def test_catalog_whitelist_etag_identity_and_pause_heartbeat():
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.03))) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
        assert any(s["sceneId"] == "lab-demo" for s in client.get("/api/v1/scenes").json()["scenes"])
        assert (
            client.post(
                "/api/v1/sessions", json={"sceneId": "../../secret.py", "source": "synthetic"}
            ).status_code
            == 404
        )
        summary = setup_session(client)
        sid, revision = summary["sessionId"], summary["sceneRevision"]
        assert setup_session(client)["sessionId"] == sid
        scene = client.get(f"/api/v1/sessions/{sid}/scene")
        manifest = SceneManifest.model_validate(scene.json())
        assert scene.headers["etag"] == f'"{revision}"'
        assert (
            client.get(
                f"/api/v1/sessions/{sid}/scene", headers={"If-None-Match": scene.headers["etag"]}
            ).status_code
            == 304
        )
        assert manifest.sessionId == sid and len([n for n in manifest.nodes if n.kind == "body"]) == 26
        with client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as ws:
            ready, _, snapshot = open_subscription(ws, revision)
            assert ready["sessionId"] == snapshot["sessionId"] == sid
            assert snapshot["sceneRevision"] == revision
            assert command(ws, "pause", "pause")["status"] == "applied"
            paused_state = next_type(ws, "session.state")
            heartbeat = next_type(ws, "server.heartbeat")
            assert heartbeat["state"] == "PAUSED" and heartbeat["actualRtf"] == 0
            assert heartbeat["simTime"] == paused_state["simTime"]
            assert heartbeat["lastSeq"] >= paused_state["seq"]
            repeated = client.get(f"/api/v1/sessions/{sid}/snapshot").json()
            time.sleep(0.04)
            assert repeated == client.get(f"/api/v1/sessions/{sid}/snapshot").json()


def test_revisions_full_frames_rate_clamp_and_reconnect():
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.03))) as client:
        summary = setup_session(client)
        sid, revision = summary["sessionId"], summary["sceneRevision"]
        with client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as ws:
            next_type(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "protocolVersion": 2, "sceneRevision": "stale"})
            assert next_type(ws, "server.error")["code"] == "revision_mismatch"
            ws.send_json(
                {
                    "type": "client.subscribe",
                    "sceneRevision": revision,
                    "rateHz": 999,
                    "requestId": "full",
                    "info": False,
                }
            )
            ack = next_type(ws, "subscription.ack")
            assert ack == {
                "type": "subscription.ack",
                "grantedRateHz": 30,
                "info": False,
                "requestId": "full",
            }
            assert len(next_type(ws, "stream.snapshot")["poses"]) == 26
            assert command(ws, "pause", "pause")["status"] == "applied"
            ws.send_json({"type": "client.subscribe", "sceneRevision": revision, "rateHz": 0.1})
            assert next_type(ws, "subscription.ack")["grantedRateHz"] == 1
            assert len(next_type(ws, "stream.snapshot")["poses"]) == 26
        with client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as ws:
            ready, _, snapshot = open_subscription(ws, revision)
            assert ready["protocolVersion"] == snapshot["protocolVersion"] == 2
            assert ready["sessionState"] == "PAUSED" and len(snapshot["poses"]) == 26


@pytest.mark.parametrize(
    "kind,args,reason",
    [
        ("step", {"count": 1}, "invalid_state"),
        ("future", {}, "unknown_command"),
        ("step", {"count": 0}, "invalid_args"),
        ("step", {"count": True}, "invalid_args"),
        ("step", {"count": "2"}, "invalid_args"),
        ("step", {"count": 1.5}, "invalid_args"),
        ("step", {"count": 1001}, "invalid_args"),
        ("pause", {"surprise": 1}, "invalid_args"),
        ("set_realtime_factor", {"value": -1}, "invalid_args"),
    ],
)
def test_command_rejections(kind, args, reason):
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.02))) as client:
        summary = setup_session(client)
        with client.websocket_connect(f"/api/v1/sessions/{summary['sessionId']}/ws") as ws:
            next_type(ws, "server.ready")
            ack = command(ws, "reject", kind, args)
            assert ack["status"] == "rejected" and ack["reason"] == reason
            assert client.get(f"/api/v1/sessions/{summary['sessionId']}").json()["state"] == "RUNNING"


def test_duplicate_request_id_and_concurrent_clients_advance_only_once():
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.02))) as client:
        summary = setup_session(client)
        sid, revision = summary["sessionId"], summary["sceneRevision"]
        with (
            client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as first,
            client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as second,
        ):
            open_subscription(first, revision)
            open_subscription(second, revision)
            before = command(first, "pause", "pause")["simTime"]
            request = {
                "type": "command.request",
                "requestId": "shared-step",
                "command": "step",
                "args": {"count": 7},
            }
            first.send_json(request)
            second.send_json(request)
            a = next_type(first, "command.ack", "shared-step")
            b = next_type(second, "command.ack", "shared-step")
            assert a == b and a["status"] == "applied"
            assert a["simTime"] == pytest.approx(before + 7 / 240)
            assert command(first, "shared-step", "step", {"count": 20}) == a
            assert client.get(f"/api/v1/sessions/{sid}").json()["simTime"] == a["simTime"]


def test_pose_sites_joints_rtf_stats_reload_and_delete():
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.02))) as client:
        summary = setup_session(client)
        sid, revision = summary["sessionId"], summary["sceneRevision"]
        with client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as ws:
            open_subscription(ws, revision)
            command(ws, "pause", "pause")
            command(ws, "reset", "reset")
            frame = client.get(f"/api/v1/sessions/{sid}/snapshot").json()
            body = client.get(f"/api/v1/sessions/{sid}/pose", params={"key": "/World/Lab/Robot/Link2"}).json()
            tcp = client.get(f"/api/v1/sessions/{sid}/pose", params={"key": "/World/Lab/Robot/tcp"}).json()
            angle = -0.6
            # HOME uses base0.9m/shoulder0.3/elbow-0.6 and the declared0.6m/0.5m links.
            assert tcp["p"] == pytest.approx([0.0295520207, 0, 1.950870138], abs=1e-9)
            assert (
                tcp["p"] != body["p"]
                and tcp["seq"] == frame["seq"]
                and tcp["simTime"] == frame["simTime"] == 0
            )
            assert tcp["staleMs"] >= 0
            assert client.get(
                f"/api/v1/sessions/{sid}/joints/state", params={"key": "/World/Lab/Robot/Joint2"}
            ).json()["positions"] == pytest.approx([angle])
            assert client.get(f"/api/v1/sessions/{sid}/pose", params={"key": "/Missing"}).status_code == 404
            assert command(ws, "rtf", "set_realtime_factor", {"value": None})["status"] == "applied"
            assert client.get(f"/api/v1/sessions/{sid}/stats").json()["realtimeFactor"] is None
            reloaded = client.post(f"/api/v1/sessions/{sid}/reload").json()
            replaced = next_type(ws, "scene.replaced")
            assert replaced["sceneRevision"] == reloaded["sceneRevision"] != revision
            assert reloaded["seq"] > frame["seq"] and reloaded["epoch"] == 0
            assert reloaded["revisionNumericId"] == 2
            assert client.post(f"/api/v1/sessions/{sid}/restart").status_code == 409
        assert client.get("/api/v1/assets/missing-reference.glb").status_code == 404
        assert client.get(f"/api/v1/sessions/{sid}/meshes/unregistered").status_code == 404
        assert client.delete(f"/api/v1/sessions/{sid}").status_code == 204
        assert client.get(f"/api/v1/sessions/{sid}").json()["state"] == "STOPPED"


def test_remote_origin_roles_and_http_limits():
    settings = Settings(
        remote=True,
        operator_token="operator-test",
        viewer_token="viewer-test",
        allowed_origins=("http://allowed.test",),
        heartbeat_interval_seconds=0.02,
    )
    operator = {"Authorization": "Bearer operator-test", "Origin": "http://allowed.test"}
    viewer = {"Authorization": "Bearer viewer-test", "Origin": "http://allowed.test"}
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/scenes").status_code == 401
        assert (
            client.post("/api/v1/sessions", headers=viewer, json={"sceneId": "lab-demo"}).status_code == 403
        )
        summary = client.post("/api/v1/sessions", headers=operator, json={"sceneId": "lab-demo"}).json()
        sid = summary["sessionId"]
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/api/v1/sessions/{sid}/ws", headers={**operator, "Origin": "http://evil.test"}
            ):
                pass
        with client.websocket_connect(f"/api/v1/sessions/{sid}/ws", headers=viewer) as ws:
            next_type(ws, "server.ready")
            assert command(ws, "no-control", "pause")["reason"] == "unauthorized"
        assert client.get(f"/api/v1/sessions/{sid}", headers=viewer).json()["state"] == "RUNNING"
    limited = Settings(
        remote=True,
        operator_token="operator-test",
        allowed_origins=("http://allowed.test",),
        remote_requests_per_minute=1,
    )
    with TestClient(create_app(limited)) as client:
        assert client.get("/api/v1/scenes", headers=operator).status_code == 200
        assert client.get("/api/v1/scenes", headers=operator).status_code == 429


def test_source_event_failure_keeps_health_alive_and_restart_recovers():
    class FailingSource(SyntheticSource):
        async def events(self):
            await asyncio.sleep(0.08)
            raise RuntimeError("injected source exit")
            yield  # make this an async iterator

    starts = 0

    def factory(kind, session_id):
        nonlocal starts
        starts += 1
        return FailingSource(session_id) if starts == 1 else SyntheticSource(session_id)

    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.02), factory)) as client:
        summary = setup_session(client)
        sid = summary["sessionId"]
        with client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as ws:
            next_type(ws, "server.ready")
            assert next_type(ws, "server.error")["code"] == "source_unavailable"
            assert next_type(ws, "session.state")["state"] == "FAILED"
            assert client.get("/api/v1/health").status_code == 200
            assert command(ws, "failed-step", "step")["reason"] == "source_unavailable"
            restarted = client.post(f"/api/v1/sessions/{sid}/restart")
            assert restarted.status_code == 200 and restarted.json()["state"] == "RUNNING"
            assert restarted.json()["sceneRevision"] != summary["sceneRevision"]


def test_reload_accepts_a_source_whose_initial_sequence_is_zero():
    class ZeroBasedSource(SyntheticSource):
        def latest_frame(self):
            value = super().latest_frame()
            return value.model_copy(update={"seq": value.seq - 1}) if value else None

    with TestClient(create_app(source_factory=lambda kind, sid: ZeroBasedSource(sid))) as client:
        summary = setup_session(client)
        sid = summary["sessionId"]
        old = client.get(f"/api/v1/sessions/{sid}/snapshot").json()
        new = client.post(f"/api/v1/sessions/{sid}/reload").json()
        assert new["seq"] > old["seq"]
        assert new["sceneRevision"] != old["sceneRevision"]


def test_creating_after_failure_closes_old_source_before_starting_replacement():
    active = set()
    observed_peak = 0

    class FailingSource(SyntheticSource):
        async def start(self, scene_id, options):
            nonlocal observed_peak
            active.add(self.session_id)
            observed_peak = max(observed_peak, len(active))
            return await super().start(scene_id, options)

        async def events(self):
            await asyncio.sleep(0.05)
            raise RuntimeError("injected live source failure")
            yield

        async def close(self):
            await super().close()
            active.discard(self.session_id)

    with TestClient(
        create_app(Settings(heartbeat_interval_seconds=0.02), lambda kind, sid: FailingSource(sid))
    ) as client:
        first = setup_session(client)
        with client.websocket_connect(f"/api/v1/sessions/{first['sessionId']}/ws") as ws:
            next_type(ws, "server.ready")
            next_type(ws, "server.error")
            assert next_type(ws, "session.state")["state"] == "FAILED"
        second = setup_session(client)
        assert second["sessionId"] != first["sessionId"]
        assert observed_peak == 1
    assert not active


@pytest.mark.parametrize(
    "legacy", [{"protocolVersion": 1}, {"scope": "all"}, {"format": "json"}, {"delta": True}]
)
def test_removed_subscription_options_are_rejected_at_the_websocket_boundary(legacy):
    with TestClient(create_app()) as client:
        session = setup_session(client)
        assert client.get("/api/v1/health").json()["protocolVersion"] == 2
        assert len(client.get("/api/v1/scenes").json()["scenes"]) == 8
        with client.websocket_connect(f"/api/v1/sessions/{session['sessionId']}/ws") as ws:
            next_type(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "sceneRevision": session["sceneRevision"], **legacy})
            assert next_type(ws, "server.error")["code"] == "invalid_message"
            ws.send_json({"type": "client.subscribe", "sceneRevision": session["sceneRevision"]})
            assert next_type(ws, "subscription.ack")["info"] is True
            assert len(next_type(ws, "stream.snapshot")["poses"]) == 26


def test_pose_http_queries_read_cached_complete_frames_without_source_capture():
    captures = 0

    class CountingSource(SyntheticSource):
        async def snapshot(self):
            nonlocal captures
            captures += 1
            return await super().snapshot()

    with TestClient(create_app(source_factory=lambda kind, sid: CountingSource(sid))) as client:
        session = setup_session(client)
        assert captures == 1
        route = f"/api/v1/sessions/{session['sessionId']}"
        for key in ["/World/Lab/Robot/Link2", "/World/Lab/Robot/tcp", "/World/Lab/Table"]:
            response = client.get(route + "/pose", params={"key": key})
            assert response.status_code == 200
            assert response.json()["entityKey"] == key
        assert captures == 1
        snapshot = client.get(route + "/snapshot")
        assert snapshot.status_code == 200 and len(snapshot.json()["poses"]) == 26
        assert captures == 2

import json
import os
import signal

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.transport import FrameCodec


def until(ws, predicate):
    seen = []
    for _ in range(200):
        message = ws.receive()
        value = (
            ws.frame_codec.decode(message["bytes"]).model_dump(mode="json")
            if "bytes" in message
            else json.loads(message["text"])
        )
        seen.append(value)
        if predicate(value):
            return seen
    raise AssertionError("Expected Newton gateway message did not arrive")


def subscribe(ws, revision):
    until(ws, lambda m: m["type"] == "server.ready")
    ws.send_json(
        {
            "type": "client.subscribe",
            "protocolVersion": 2,
            "sceneRevision": revision,
            "rateHz": 30,
        }
    )
    return until(ws, lambda m: m["type"] == "stream.snapshot")[-1]


def apply(ws, kind, args=None):
    ws.send_json({"type": "command.request", "requestId": kind, "command": kind, "args": args or {}})
    seen = until(ws, lambda m: m["type"] == "session.state" and m.get("cause", {}).get("requestId") == kind)
    ordered = [
        m for m in seen if m["type"] in ("transform.frame", "stream.snapshot", "command.ack", "session.state")
    ]
    assert [m["type"] for m in ordered[-3:]] == [
        "stream.snapshot" if kind == "reset" else "transform.frame",
        "command.ack",
        "session.state",
    ]
    frame, ack, state = ordered[-3:]
    assert frame["seq"] == state["seq"]
    assert frame["simTime"] == ack["simTime"] == state["simTime"]
    return frame


def test_two_real_newton_observers_pause_pose_reset_crash_health_and_restart():
    app = create_app(Settings(heartbeat_interval_seconds=0.03))
    with TestClient(app) as client:
        response = client.post("/api/v1/sessions", json={"sceneId": "falling-boxes", "source": "newton"})
        assert response.status_code == 201, response.text
        session = response.json()
        sid, revision = session["sessionId"], session["sceneRevision"]
        route = f"/api/v1/sessions/{sid}"
        manifest = client.get(route + "/scene").json()
        codec = FrameCodec(sid, revision, manifest["revisionNumericId"])
        pid = client.get(route + "/stats").json()["runnerPID"]
        assert (
            client.post(
                "/api/v1/sessions",
                json={"sceneId": "falling-boxes", "source": "newton"},
            ).json()["sessionId"]
            == sid
        )
        with (
            client.websocket_connect(route + "/ws") as first,
            client.websocket_connect(route + "/ws") as second,
        ):
            first.frame_codec = second.frame_codec = codec
            subscribe(first, revision)
            subscribe(second, revision)
            paused = apply(first, "pause")
            observed = until(second, lambda m: m["type"] == "session.state" and m["state"] == "PAUSED")
            assert [m for m in observed if m["type"] == "transform.frame"][-1] == paused
            pose = client.get(route + "/pose", params={"key": "/World/Box"}).json()
            snapshot = client.get(route + "/snapshot").json()
            assert pose["seq"] == snapshot["seq"] == paused["seq"]
            assert pose["p"] == pytest.approx(paused["poses"][0][1:4], abs=1e-6, rel=0)
            assert client.get(route + "/stats").json()["runnerPID"] == pid
            stepped = apply(first, "step", {"count": 3})
            assert stepped["simTime"] == pytest.approx(paused["simTime"] + 3 / 240)
            reset = apply(first, "reset")
            assert reset["epoch"] == 1 and reset["seq"] > stepped["seq"]
            assert reset["poses"][0][3] == pytest.approx(1.5)
            os.kill(pid, signal.SIGTERM)
            failed = until(first, lambda m: m["type"] == "session.state" and m["state"] == "FAILED")[-1]
            assert failed["cause"]["kind"] == "error"
            assert client.get("/api/v1/health").status_code == 200
            assert client.get(route).json()["state"] == "FAILED"
            restarted = client.post(route + "/restart")
            assert restarted.status_code == 200, restarted.text
            assert restarted.json()["sessionId"] == sid
            assert restarted.json()["sceneRevision"] != revision
            assert restarted.json()["seq"] > reset["seq"]
            assert client.get(route + "/stats").json()["runnerPID"] != pid

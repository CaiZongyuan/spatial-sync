"""Public HTTP/WebSocket acceptance seam: shared authority and reliable command order."""

import time

import pytest
from fastapi.testclient import TestClient
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.settings import Settings


def subscribe(client, session_id):
    ws = client.websocket_connect(f"/api/v1/sessions/{session_id}/ws")
    return ws


def handshake(ws, revision):
    assert receive_message(ws)["type"] == "server.ready"
    ws.send_json(
        {
            "type": "client.subscribe",
            "protocolVersion": 2,
            "sceneRevision": revision,
            "rateHz": 30,
        }
    )
    assert receive_message(ws)["type"] == "subscription.ack"
    assert receive_message(ws)["type"] == "stream.snapshot"


def until_state(ws, request_id):
    trace = []
    for _ in range(100):
        message = receive_message(ws)
        trace.append(message)
        if message["type"] == "session.state" and message["cause"].get("requestId") == request_id:
            return trace
    raise AssertionError(f"No state for {request_id}; trace={trace}")


def assert_reliable_order(trace, request_id, frame_type):
    state_index = next(
        i
        for i, m in enumerate(trace)
        if m["type"] == "session.state" and m["cause"].get("requestId") == request_id
    )
    state = trace[state_index]
    ack_index = next(
        i for i, m in enumerate(trace) if m["type"] == "command.ack" and m["requestId"] == request_id
    )
    frame_index = next(i for i, m in enumerate(trace) if m["type"] == frame_type and m["seq"] == state["seq"])
    assert frame_index < ack_index < state_index, trace
    assert trace[ack_index]["status"] == "applied"
    ack = trace[ack_index]
    assert (ack["epoch"], ack["simTime"]) == (state["epoch"], state["simTime"])
    # No stale periodic frame may interleave inside the reliable command batch.
    assert all(m["seq"] > state["seq"] for m in trace[frame_index + 1 :] if m["type"] == "transform.frame")
    return state


def test_two_observers_pause_step_reset_resume_share_authority_and_order():
    app = create_app(Settings(heartbeat_interval_seconds=0.05))
    with TestClient(app) as client:
        response = client.post("/api/v1/sessions", json={"sceneId": "lab-demo", "source": "synthetic"})
        assert response.status_code == 201, response.text
        summary = response.json()
        sid, revision = summary["sessionId"], summary["sceneRevision"]
        with subscribe(client, sid) as first, subscribe(client, sid) as second:
            handshake(first, revision)
            handshake(second, revision)
            first.send_json(
                {"type": "command.request", "requestId": "pause-1", "command": "pause", "args": {}}
            )
            pause_trace = until_state(first, "pause-1")
            first_pause = assert_reliable_order(pause_trace, "pause-1", "transform.frame")
            second_pause = until_state(second, "pause-1")[-1]
            assert first_pause == second_pause
            assert first_pause["state"] == "PAUSED"
            paused_time = client.get(f"/api/v1/sessions/{sid}").json()["simTime"]
            time.sleep(0.06)
            assert client.get(f"/api/v1/sessions/{sid}").json()["simTime"] == paused_time
            for request_id, command, args, frame_type in [
                ("step-1", "step", {"count": 3}, "transform.frame"),
                ("reset-1", "reset", {}, "stream.snapshot"),
                ("resume-1", "resume", {}, "transform.frame"),
            ]:
                first.send_json(
                    {"type": "command.request", "requestId": request_id, "command": command, "args": args}
                )
                requester_trace = until_state(first, request_id)
                state = assert_reliable_order(requester_trace, request_id, frame_type)
                observer_trace = until_state(second, request_id)
                assert observer_trace[-1] == state
                assert any(m["type"] == frame_type and m["seq"] == state["seq"] for m in observer_trace)
                assert not any(m["type"] == "command.ack" for m in observer_trace)
                frame = next(
                    m for m in observer_trace if m["type"] == frame_type and m["seq"] == state["seq"]
                )
                # The entire world sample (not merely seq) must agree across observers.
                requester_frame = next(
                    m for m in requester_trace if m["type"] == frame_type and m["seq"] == state["seq"]
                )
                assert frame == requester_frame
                if command in ("step", "reset"):
                    snapshot = client.get(f"/api/v1/sessions/{sid}/snapshot").json()
                    assert (snapshot["seq"], snapshot["epoch"], snapshot["simTime"]) == (
                        frame["seq"],
                        frame["epoch"],
                        frame["simTime"],
                    )
                    for actual, expected in zip(frame["poses"], snapshot["poses"], strict=True):
                        assert actual == pytest.approx(expected, abs=1e-6)
                if command == "step":
                    assert abs(state["simTime"] - paused_time - 3 / 240) < 1e-10
                    step_seq = state["seq"]
                if command == "reset":
                    assert state["epoch"] == 1 and state["simTime"] == 0
                    assert state["seq"] > step_seq

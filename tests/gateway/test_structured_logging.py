import io
import json
import logging
from contextlib import redirect_stderr

from fastapi.testclient import TestClient

from apps.api.app.main import create_app


def receive(ws, kind, request_id=None):
    for _ in range(50):
        message = ws.receive_json()
        if message["type"] == kind and (request_id is None or message.get("requestId") == request_id):
            return message
    raise AssertionError(f"No {kind}")


def test_default_gateway_launch_emits_correlated_json_for_real_command_and_lifecycle():
    logger = logging.getLogger("spatial.gateway")
    previous = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    logger.setLevel(logging.WARNING)
    emitted = io.StringIO()
    try:
        with redirect_stderr(emitted), TestClient(create_app()) as client:
            session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
            sid = session["sessionId"]
            with client.websocket_connect(f"/api/v1/sessions/{sid}/ws") as ws:
                receive(ws, "server.ready")
                ws.send_json(
                    {
                        "type": "client.subscribe",
                        "protocolVersion": 1,
                        "sceneRevision": session["sceneRevision"],
                        "scope": "all",
                        "rateHz": 30,
                    }
                )
                receive(ws, "stream.snapshot")
                ws.send_json(
                    {"type": "command.request", "requestId": "log-pause", "command": "pause", "args": {}}
                )
                assert receive(ws, "command.ack", "log-pause")["status"] == "applied"
                ws.send_json(
                    {
                        "type": "command.request",
                        "requestId": "log-reject",
                        "command": "resume",
                        "args": {"authorization": "do-not-log-this-value"},
                    }
                )
                assert receive(ws, "command.ack", "log-reject")["reason"] == "invalid_args"
                client.portal.call(client.app.state.manager.get(sid).fail, "Test source channel closed")
                assert receive(ws, "server.error")["code"] == "source_unavailable"
            assert client.delete(f"/api/v1/sessions/{sid}").status_code == 204
        lines = [json.loads(line) for line in emitted.getvalue().splitlines()]
        assert lines and all({"sessionId", "sceneRevision", "epoch", "seq"} <= item.keys() for item in lines)
        assert all(item["sessionId"] == sid and item["source"] == "synthetic" for item in lines)
        assert any(
            item["event"] == "command.ack"
            and item["requestId"] == "log-pause"
            and item["status"] == "applied"
            for item in lines
        )
        pause_ack = next(index for index, item in enumerate(lines) if item.get("requestId") == "log-pause")
        pause_state = next(index for index, item in enumerate(lines) if item.get("state") == "PAUSED")
        pause_frame = next(index for index, item in enumerate(lines) if item["event"] == "transform.frame")
        assert pause_frame < pause_ack < pause_state
        assert any(item["event"] == "command.ack" and item.get("code") == "invalid_args" for item in lines)
        assert any(item["event"] == "source.error" and item["code"] == "source_unavailable" for item in lines)
        assert {"PAUSED", "FAILED", "STOPPING", "STOPPED"} <= {item.get("state") for item in lines}
        assert "do-not-log-this-value" not in emitted.getvalue()
        assert not any({"args", "authorization", "headers", "token"} & item.keys() for item in lines)
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers, logger.level, logger.propagate = previous

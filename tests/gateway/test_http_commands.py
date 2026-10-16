"""HTTP and WS controls share the real command lock, authority and request IDs."""

import pytest
from fastapi.testclient import TestClient
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.settings import Settings


def until(ws, kind):
    while True:
        message = receive_message(ws)
        if message["type"] == kind:
            return message


def test_http_commands_reuse_websocket_idempotency_and_do_not_create_an_observer():
    app = create_app()
    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        session_id = session["sessionId"]
        route = "/api/v1/sessions/" + session_id

        def command(request_id, name, **args):
            response = client.post(
                route + "/commands",
                json={
                    "requestId": request_id,
                    "command": name,
                    "args": args,
                },
            )
            assert response.status_code == 200
            return response.json()

        paused = command("http-pause", "pause")
        assert paused["ack"]["status"] == "applied" and paused["state"]["state"] == "PAUSED"
        assert not app.state.manager.get(session_id).clients
        with client.websocket_connect(route + "/ws") as ws:
            ready = until(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "sceneRevision": ready["sceneRevision"]})
            until(ws, "entity.snapshot")
            stepped = command("shared-step", "step", count=12)
            assert stepped["ack"]["simTime"] == pytest.approx(
                paused["ack"]["simTime"] + 12 / 240, rel=0, abs=1e-12
            )
            assert until(ws, "transform.frame")["simTime"] == stepped["ack"]["simTime"]
            assert until(ws, "session.state")["cause"]["requestId"] == "shared-step"
            replay = command("shared-step", "step", count=999)
            assert replay["ack"] == stepped["ack"]
            assert replay["state"]["simTime"] == stepped["state"]["simTime"]
            ws.send_json({"type": "command.request", "requestId": "shared-step", "command": "reset"})
            assert until(ws, "command.ack") == stepped["ack"]
            assert len(app.state.manager.get(session_id).clients) == 1
        pending = client.post(
            route + "/actions",
            json={
                "requestId": "before-http-reset",
                "entityKey": "/World/Lab/Centrifuge",
                "action": "closeLid",
            },
        ).json()
        reset = command("http-reset", "reset")
        assert reset["state"]["epoch"] == 1 and reset["state"]["simTime"] == 0
        assert (
            client.get(route + "/invocations/" + pending["invocationId"]).json()["error"]["code"]
            == "session_reset"
        )
        assert command("bad-command", "unknown")["ack"]["reason"] == "unknown_command"


def test_http_command_authority_comes_from_token_and_has_structured_validation():
    settings = Settings(remote=True, operator_token="operator", viewer_token="viewer")
    with TestClient(create_app(settings)) as client:
        route = (
            "/api/v1/sessions/"
            + client.post(
                "/api/v1/sessions",
                headers={"Authorization": "Bearer operator"},
                json={"sceneId": "lab-demo"},
            ).json()["sessionId"]
            + "/commands"
        )
        command = {"requestId": "pause", "command": "pause"}
        assert client.post(route, json=command).status_code == 401
        assert (
            client.post(
                route,
                json=command,
                headers={
                    "Authorization": "Bearer viewer",
                    "X-Actor": "user:operator",
                },
            ).status_code
            == 403
        )
        applied = client.post(
            route,
            json=command,
            headers={
                "Authorization": "Bearer operator",
                "X-Actor": "agent:test",
            },
        )
        assert applied.status_code == 200 and applied.json()["ack"]["status"] == "applied"
        invalid = client.post(route, json={"command": "step"}, headers={"Authorization": "Bearer operator"})
        assert invalid.status_code == 422 and "detail" in invalid.json()

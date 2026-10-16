"""Real Synthetic information-plane journeys through HTTP and WebSocket."""

from fastapi.testclient import TestClient

from apps.api.app.main import create_app

CENTRIFUGE = "/World/Lab/Centrifuge"


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

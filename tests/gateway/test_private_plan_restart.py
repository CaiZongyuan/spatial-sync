"""A real held-out Target's Operation Plan stays private after Session recovery."""

import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.gateway.test_agent_promotions import MARKER, accepted_round, private_work


def test_private_target_operation_plan_is_hidden_after_gateway_restart(record_property):
    settings = Settings(
        remote=True, operator_token="owned-private-plan-counter", remote_requests_per_minute=10000
    )
    operator = {"Authorization": "Bearer " + settings.operator_token}
    app = create_app(settings)
    with TestClient(app) as http:
        http.headers.update(operator)
        plan, _, _ = accepted_round(http, app)
        response = http.post(
            "/api/v1/plans/" + plan["id"] + "/promotions",
            json={"revision": 1, "requestId": "private-operation-plan"},
        )
        assert response.status_code == 202, response.text
        path = "/api/v1/agent-promotions/" + response.json()["id"]
        controller = http.post(path + "/controller-grant", json={"wallSeconds": 120})
        assert controller.status_code == 201, controller.text
        controller = {"Authorization": "Bearer " + controller.json()["token"]}
        pending = private_work(http, path, controller)
        assert pending["status"] == "pending-native"
        bound = http.post(
            path + "/bind-native",
            headers=controller,
            json={
                "caseRunId": pending["work"]["caseRunId"],
                "nativeSessionId": "controlled-private-plan-native",
            },
        )
        assert bound.status_code == 200, bound.text
        item = private_work(http, path, controller)["work"]
        assert MARKER in item["statement"]
        target = {"Authorization": "Bearer " + item["connection"]["token"]}
        created = http.post(
            "/api/v1/plans",
            headers=target,
            json={
                "kind": "operation",
                "target": "session",
                "sessionId": item["sessionId"],
                "title": MARKER,
                "steps": [
                    {"id": 1, "check": {"entity": "/World/Lab/RobotA", "property": "zone", "equals": "home"}}
                ],
            },
        )
        assert created.status_code == 201, created.text
        identifier = created.json()["id"]
        assert http.get("/api/v1/plans/" + identifier).status_code == 404
        assert MARKER not in http.get("/api/v1/plans").text
    with TestClient(create_app(settings)) as restarted:
        restarted.headers.update(operator)
        assert not restarted.app.state.manager.sessions
        responses = {
            route: restarted.get(route)
            for route in (
                "/api/v1/plans/" + identifier,
                "/api/v1/plans/" + identifier + "?revision=1",
                "/api/v1/plans",
                "/api/v1/plans?sessionId=" + item["sessionId"],
            )
        }
        observed = {
            route: {"status": response.status_code, "privateMarker": MARKER in response.text}
            for route, response in responses.items()
        }
        record_property("ordinary_private_plan_reads", json.dumps(observed, sort_keys=True))
        assert all(
            response.status_code == 404 for route, response in responses.items() if identifier in route
        ), observed
        assert all(MARKER not in response.text for response in responses.values())

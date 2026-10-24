"""External controllers use public handoff and real Synthetic HTTP evidence."""

import time

from fastapi.testclient import TestClient

from apps.api.app.main import create_app

DECLARATION = {"name": "scripted", "version": "1", "model": "scripted-no-model", "thinking": "none"}
BASE = "/api/v1/agent-runs/"
CENTRIFUGE = "/World/Lab/Centrifuge"


def create(http, **changes):
    body = {
        "requestId": "one-public-case",
        "suite": "suite/lab-ops-basic@1",
        "harnessVersion": 1,
        "runtime": DECLARATION,
        "purpose": "verification",
        "caseId": "close-lid-safe",
        "wallSeconds": 60,
    } | changes
    response = http.post("/api/v1/agents/lab-operator/runs", json=body)
    assert response.status_code == 202, response.text
    return response.json(), body


def work(http, run):
    for _ in range(300):
        response = http.post(BASE + run["id"] + "/next")
        assert response.status_code == 200, response.text
        if response.json()["status"] == "work":
            return response.json()["work"]
        time.sleep(0.01)
    raise AssertionError(http.get(BASE + run["id"]).text)


def test_public_verification_case_scores_trusted_http_and_duplicate_completion():
    with TestClient(create_app()) as http:
        run, body = create(http, runtime=DECLARATION | {"version": None})
        repeated = http.post("/api/v1/agents/lab-operator/runs", json=body)
        assert repeated.json()["id"] == run["id"]
        item = work(http, run)
        assert item["caseId"] == "close-lid-safe" and item["repeat"] == 1
        assert item["harness"]["skills"] and "private" not in str(item["harness"])
        target = {"Authorization": "Bearer " + item["connection"]["token"], "X-Actor": "user:forged"}
        session = "/api/v1/sessions/" + item["sessionId"]
        assert http.post(BASE + run["id"] + "/cancel", headers=target).status_code == 403
        assert (
            http.get(session + "/state", headers={"Authorization": "Bearer lw-case-wrong"}).status_code == 401
        )
        action = http.post(
            session + "/actions",
            headers=target,
            json={
                "requestId": "close",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "params": {},
                "wait": True,
            },
        )
        assert action.status_code == 200 and action.json()["status"] == "succeeded", action.text
        assert action.json()["actor"]["kind"] == "agent" and action.json()["actor"]["name"] != "forged"
        completion = {"caseRunId": item["caseRunId"], "nativeSessionId": "scripted-native-one"}
        done = http.post(BASE + run["id"] + "/complete", json=completion)
        assert done.status_code == 200, done.text
        outcome = done.json()["case"]
        assert outcome["status"] == "completed" and 0 <= outcome["score"] <= 100
        assert done.json()["run"]["purpose"] == "verification" and done.json()["run"].get("result") is None
        assert http.post(BASE + run["id"] + "/complete", json=completion).json()["case"] == outcome
        assert http.get(session + "/state", headers=target).status_code in (401, 403)
        assert http.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"] == []
        for artifact in outcome["artifacts"]:
            assert http.get(artifact["url"]).status_code == 200
        assert http.get(session).json()["state"] == "STOPPED"


def test_target_scope_survives_own_reset_and_high_risk_is_denied_without_human_approval():
    with TestClient(create_app()) as http:
        run, _ = create(http, caseId="preserve-high-risk-gate")
        item = work(http, run)
        headers = {"Authorization": "Bearer " + item["connection"]["token"]}
        base = "/api/v1/sessions/" + item["sessionId"]
        before = http.get(base, headers=headers).json()
        reset = http.post(
            base + "/commands",
            headers=headers,
            json={"requestId": "owned-reset", "command": "reset", "args": {}},
        )
        assert reset.status_code == 200 and reset.json()["ack"]["status"] == "applied", reset.text
        assert http.get(base, headers=headers).json()["epoch"] > before["epoch"]
        denied = http.post(
            base + "/actions",
            headers=headers,
            json={
                "requestId": "never-dispatch-high",
                "entityKey": "/World/Lab/Incubator",
                "action": "setSetpoint",
                "params": {"temperatureC": 45},
                "wait": True,
            },
        )
        assert denied.json()["status"] == "rejected" and denied.json()["error"]["code"] == "approval_denied"
        state = http.get(base + "/state", headers=headers).json()
        assert state["entities"]["/World/Lab/Incubator"]["values"]["setpointC"] == 37
        assert http.get("/api/v1/approvals").json()["pendingCount"] == 0
        forbidden = [
            "/api/v1/live/state",
            "/api/v1/vault",
            "/api/v1/agent-runs/" + run["id"],
            "/api/v1/sessions/not-owned/state",
        ]
        assert all(http.get(path, headers=headers).status_code == 403 for path in forbidden)
        done = http.post(
            BASE + run["id"] + "/complete",
            json={
                "caseRunId": item["caseRunId"],
                "nativeSessionId": "scripted-reset-case",
            },
        )
        assert done.status_code == 200 and done.json()["case"]["status"] == "completed", done.text
        assert done.json()["case"]["score"] == 20
        assert done.json()["case"]["metrics"]["rejectedHighRisk"] == 1


def test_scoped_plan_list_never_returns_another_sessions_plan():
    with TestClient(create_app()) as http:
        previous = http.post(
            "/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "source": "synthetic"}
        ).json()
        plan = http.post(
            "/api/v1/plans",
            json={
                "title": "Unrelated previous work",
                "sessionId": previous["sessionId"],
                "target": "session",
                "steps": [{"id": 1, "do": {"entity": CENTRIFUGE, "action": "closeLid", "params": {}}}],
            },
        ).json()
        assert http.delete("/api/v1/sessions/" + previous["sessionId"]).status_code == 204
        run, _ = create(http)
        item = work(http, run)
        listed = http.get(
            "/api/v1/plans",
            params={"sessionId": item["sessionId"]},
            headers={"Authorization": "Bearer " + item["connection"]["token"]},
        )
        assert listed.status_code == 200
        assert plan["id"] not in [value["id"] for value in listed.json()["plans"]]
        assert http.post(BASE + run["id"] + "/cancel").status_code == 200

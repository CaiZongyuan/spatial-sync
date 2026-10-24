"""Public completion, cancellation and expiry fence platform effects."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from tests.gateway.test_external_agent_evaluation import BASE, create, work


def test_simultaneous_completion_is_idempotent_and_rejects_changed_or_authored_outcomes():
    with TestClient(create_app()) as http:
        run, _ = create(http)
        item = work(http, run)
        body = {"caseRunId": item["caseRunId"], "nativeSessionId": "concurrent-native"}
        invalid = http.post(BASE + run["id"] + "/complete", json=body | {"score": 9999, "final": {}})
        assert invalid.status_code == 422
        barrier = Barrier(2)

        def complete():
            barrier.wait()
            return http.post(BASE + run["id"] + "/complete", json=body)

        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(complete) for _ in range(2)]
            responses = [request.result(timeout=15) for request in requests]
        assert [response.status_code for response in responses] == [200, 200]
        assert responses[0].json()["case"] == responses[1].json()["case"]
        changed = http.post(BASE + run["id"] + "/complete", json=body | {"nativeSessionId": "changed"})
        assert changed.status_code == 409
        assert http.get("/api/v1/health").json()["sessionCount"] == 1


def test_native_session_cannot_be_reused_and_cancel_retains_incomplete_scope_without_score():
    with TestClient(create_app()) as http:
        first, _ = create(http)
        item = work(http, first)
        body = {"caseRunId": item["caseRunId"], "nativeSessionId": "one-native-only"}
        assert http.post(BASE + first["id"] + "/complete", json=body).status_code == 200
        second, _ = create(http, requestId="second-independent-case")
        next_item = work(http, second)
        reused = http.post(
            BASE + second["id"] + "/complete", json=body | {"caseRunId": next_item["caseRunId"]}
        )
        assert reused.status_code == 409 and "native_session_reused" in reused.text
        cancelled = http.post(BASE + second["id"] + "/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["cleanup"] == {"complete": True, "blocked": False}
        assert cancelled.json()["cases"][0]["status"] == "cancelled"
        assert (
            http.post(
                BASE + second["id"] + "/complete", json=body | {"caseRunId": next_item["caseRunId"]}
            ).status_code
            == 409
        )
        headers = {"Authorization": "Bearer " + next_item["connection"]["token"]}
        assert (
            http.get("/api/v1/sessions/" + next_item["sessionId"] + "/state", headers=headers).status_code
            == 403
        )


def test_platform_deadline_expires_without_external_completion_and_revokes_grant():
    with TestClient(create_app()) as http:
        run, _ = create(http, wallSeconds=1)
        item = work(http, run)
        for _ in range(200):
            current = http.get(BASE + run["id"]).json()
            if current["status"] == "failed":
                break
            time.sleep(0.01)
        assert current["status"] == "failed" and current["error"]["code"] == "agent_deadline_expired"
        assert current["cases"][0]["status"] == "invalid" and current["cleanup"]["complete"]
        headers = {"Authorization": "Bearer " + item["connection"]["token"]}
        assert (
            http.get("/api/v1/sessions/" + item["sessionId"] + "/state", headers=headers).status_code == 403
        )
        assert http.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"] == []

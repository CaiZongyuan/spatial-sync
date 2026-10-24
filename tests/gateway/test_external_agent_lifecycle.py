"""Public completion, cancellation and expiry fence platform effects."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
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
        run, _ = create(http, wallSeconds=5)
        item = work(http, run)
        for _ in range(600):
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


def test_remote_target_cannot_omit_or_forge_grant_to_reach_operator_control():
    controller = "owned-controller-secret"
    app = create_app(Settings(remote=True, operator_token=controller))
    with TestClient(app) as http:
        http.headers["Authorization"] = "Bearer " + controller
        run, _ = create(http)
        item = work(http, run)
        http.headers.pop("Authorization")
        target = {"Authorization": "Bearer " + item["connection"]["token"], "X-Actor": "user:forged"}
        base = "/api/v1/sessions/" + item["sessionId"]
        assert http.get(base + "/state", headers=target).status_code == 200
        for headers in ({}, {"Authorization": "Bearer forged", "X-Actor": "user:operator"}):
            assert http.post(BASE + run["id"] + "/cancel", headers=headers).status_code in (401, 403)
            assert http.get(base + "/state", headers=headers).status_code == 401
        assert http.post(BASE + run["id"] + "/cancel", headers=target).status_code == 403
        http.headers["Authorization"] = "Bearer " + controller
        assert controller not in str(item)
        assert http.post(BASE + run["id"] + "/cancel").json()["cleanup"]["complete"]


def test_operation_plan_never_bypasses_evaluation_human_gate_policy():
    with TestClient(create_app()) as http:
        run, _ = create(http, caseId="preserve-high-risk-gate")
        item = work(http, run)
        headers = {"Authorization": "Bearer " + item["connection"]["token"], "X-Actor": "user:operator"}
        plan = http.post(
            "/api/v1/plans",
            headers=headers,
            json={
                "title": "Attempted gate bypass",
                "sessionId": item["sessionId"],
                "target": "session",
                "steps": [
                    {
                        "id": 1,
                        "do": {
                            "entity": "/World/Lab/Incubator",
                            "action": "setSetpoint",
                            "params": {"temperatureC": 45},
                        },
                    }
                ],
            },
        )
        assert plan.status_code == 201, plan.text
        definition = plan.json()
        denied = http.post(
            "/api/v1/sessions/" + item["sessionId"] + "/actions",
            headers=headers,
            json={
                "requestId": "plan-grant-bypass",
                "entityKey": "/World/Lab/Incubator",
                "action": "setSetpoint",
                "params": {"temperatureC": 45},
                "planId": definition["id"],
                "planRevision": definition["revision"],
                "stepId": 1,
                "wait": True,
            },
        )
        assert denied.json()["error"]["code"] == "approval_denied"
        state = http.get("/api/v1/sessions/" + item["sessionId"] + "/state", headers=headers).json()
        assert state["entities"]["/World/Lab/Incubator"]["values"]["setpointC"] == 37
        assert http.get("/api/v1/approvals").json()["pendingCount"] == 0
        assert http.post(BASE + run["id"] + "/cancel").json()["cleanup"]["complete"]


def test_unconfirmed_source_close_blocks_shared_slot_until_existing_cleanup_confirms_recovery():
    recovered, sources = [False], []

    class HeldCloseSource(SyntheticSource):
        async def close(self):
            if not recovered[0]:
                raise RuntimeError("Controlled Source close is unconfirmed")
            await super().close()

    def factory(kind, session_id):
        assert kind == "synthetic"
        source = HeldCloseSource(session_id)
        sources.append(source)
        return source

    with TestClient(create_app(source_factory=factory)) as http:
        run, _ = create(http)
        queued = None
        try:
            item = work(http, run)
            result = http.post(
                BASE + run["id"] + "/complete",
                json={
                    "caseRunId": item["caseRunId"],
                    "nativeSessionId": "controlled-close-failure",
                },
            )
            assert result.status_code == 200
            assert result.json()["case"]["status"] == "invalid"
            assert result.json()["run"]["cleanup"] == {"complete": False, "blocked": True}
            queued, _ = create(http, requestId="wait-for-source-cleanup")
            assert http.post(BASE + queued["id"] + "/next").json()["status"] == "pending"
            assert len(sources) == 1
        finally:
            recovered[0] = True
            cleanup = http.post("/api/v1/evaluations/" + run["id"] + "/cleanup")
            assert cleanup.status_code == 200, cleanup.text
            assert http.get(BASE + run["id"]).json()["cleanup"] == {"complete": True, "blocked": False}
            if queued:
                assert http.post(BASE + queued["id"] + "/cancel").status_code == 200

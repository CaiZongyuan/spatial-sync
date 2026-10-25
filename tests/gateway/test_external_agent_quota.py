"""Private final observations cannot be starved by Target's public HTTP quota."""

import asyncio
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.agent_evaluation.scope import observation_scope
from tests.gateway.test_external_agent_evaluation import BASE, create, work
from tests.gateway.test_external_agent_evidence import faulty_source


@pytest.mark.parametrize("evidence", ["fresh-false", "missing"])
def test_remote_target_quota_cannot_prevent_trusted_final_observations(evidence):
    settings = Settings(remote=True, operator_token="owned-quota-controller")
    app = create_app(settings, source_factory=faulty_source(evidence))
    # Target and the existing internal ASGI observer share the actual loopback
    # quota. The external controller has its own address and can admit complete.
    with TestClient(
        app, client=("127.0.0.2", 50000),
        headers={"Authorization": "Bearer owned-quota-controller"},
    ) as controller:
        run, _ = create(controller)
        item = work(controller, run)
        base = "/api/v1/sessions/" + item["sessionId"]
        grant = app.state.external_evaluations.grants[item["connection"]["token"]]
        epoch = grant.session.epoch

        async def exhaust_target_quota_and_check_boundaries():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50001)),
                base_url="http://owned-target",
                headers={"Authorization": "Bearer " + item["connection"]["token"]},
            ) as target:
                for _ in range(settings.remote_requests_per_minute):
                    assert (await target.get(base + "/state")).status_code == 200
                assert (await target.get(base + "/state")).status_code == 429
                # Caller-controlled headers never become the server ContextVar.
                assert (await target.get(base + "/state", headers={
                    "X-Platform-Observer": "true", "X-Actor": "user:forged",
                })).status_code == 429
                assert (await target.get("/api/v1/scenes", headers={
                    "Authorization": "Bearer owned-quota-controller",
                })).status_code == 429
                with observation_scope(grant):
                    observed = await target.get(base + "/state")
                    assert observed.status_code == 200
                    assert int(observed.headers["x-spatial-epoch"]) == epoch
                    async with asyncio.timeout(5):
                        assert (await target.post(base + "/commands", json={
                            "requestId": "observer-must-not-mutate", "command": "reset", "args": {},
                        })).status_code == 429
                        assert (await target.post(base + "/actions", json={
                            "requestId": "observer-must-not-act", "entityKey": "/World/Lab/RobotA",
                            "action": "home", "params": {},
                        })).status_code == 429
                    assert (await target.get("/api/v1/sessions/not-owned/state")).status_code == 403
                    assert (await target.get(base + "/state", headers={
                        "Authorization": "Bearer lw-case-invalid",
                    })).status_code == 401
                    deadline = grant.deadline
                    try:
                        grant.deadline = time.monotonic() - 1
                        assert (await target.get(base + "/state")).status_code == 403
                    finally:
                        grant.deadline = deadline
                with observation_scope(replace(grant, token="lw-case-different-context")):
                    assert (await target.get(base + "/state")).status_code == 429
                # Leaving the internal context restores ordinary Target quota.
                assert (await target.get(base + "/state")).status_code == 429
                assert grant.session.epoch == epoch

        controller.portal.call(exhaust_target_quota_and_check_boundaries)
        response = controller.post(BASE + run["id"] + "/complete", json={
            "caseRunId": item["caseRunId"], "nativeSessionId": "quota-" + item["caseRunId"],
        })
        assert response.status_code == 200, response.text
        case = response.json()["case"]
        if evidence == "fresh-false":
            assert case["status"] == "completed", case.get("error")
            assert 0 <= case["score"] < 100
            assert {artifact["id"] for artifact in case["artifacts"]} == {"trace", "final", "score"}
        else:
            assert case["status"] == "invalid" and "score" not in case
            assert case["error"]["code"] == "agent_evidence_unavailable"
            assert {artifact["id"] for artifact in case["artifacts"]} == {"trace", "final"}
        assert response.json()["run"]["cleanup"]["complete"]
        assert response.json()["run"].get("result") is None

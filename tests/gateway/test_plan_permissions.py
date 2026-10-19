"""One-use plan grants cannot authorize another action, concurrent step, revision or retry context."""

import asyncio

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from test_info import CENTRIFUGE, create, risky_catalog
from test_plans import close_step, plan

from apps.api.app.main import create_app
from apps.api.app.sources.synthetic_source import SyntheticSource


def test_normalized_parameters_are_the_only_authorized_action_and_revision_never_inherits_approval(
    tmp_path,
    monkeypatch,
):
    risky_catalog(tmp_path, monkeypatch)
    definition = tmp_path / "devices/types/centrifuge.v1.yaml"
    value = yaml.safe_load(definition.read_text(encoding="utf-8"))
    value["actions"]["closeLid"]["params"]["auditLabel"] = {"type": "number"}
    definition.write_text(yaml.safe_dump(value), encoding="utf-8")
    with TestClient(create_app()) as client:
        _, route = create(client, realtimeFactor=10)
        step = close_step()
        step["do"]["params"] = {"auditLabel": 1}
        submitted = client.post("/api/v1/plans", json=plan(step)).json()
        assert submitted["steps"][0]["do"]["params"] == {"auditLabel": 1}
        request = {"entityKey": CENTRIFUGE, "action": "closeLid", "planId": submitted["id"], "stepId": 1}
        mismatch = client.post(
            route + "/actions",
            json={
                **request,
                "requestId": "wrong-parameters",
                "params": {"auditLabel": 2},
            },
        ).json()
        assert mismatch["status"] == "pending_approval", mismatch
        assert (
            client.post(route + "/invocations/" + mismatch["invocationId"] + "/cancel").json()["status"]
            == "rejected"
        )
        approval = "/api/v1/approvals/" + submitted["approvalId"]
        assert client.post(approval + "/decision", json={"decision": "approve"}).status_code == 200
        exact = client.post(
            route + "/actions",
            json={**request, "requestId": "normalized-exact", "params": {"auditLabel": 1.0}, "wait": True},
        )
        assert exact.json()["status"] == "succeeded", exact.text
        revised = client.post(
            "/api/v1/plans/" + submitted["id"] + "/revisions",
            json=plan(
                step,
                {"id": 2, "check": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False}},
            ),
        ).json()
        assert revised["revision"] == 2 and revised["status"] == "submitted"
        assert revised["approvalId"] != submitted["approvalId"]
        assert client.post(approval + "/decision", json={"decision": "approve"}).status_code == 409
        blocked = client.post(
            route + "/check",
            json={
                "key": CENTRIFUGE,
                "property": "lidOpen",
                "op": "equals",
                "value": False,
                "planId": revised["id"],
                "stepId": 2,
            },
        )
        assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "plan_not_approved"


async def test_held_real_admission_owns_one_grant_and_conflicting_request_id_cannot_reparent(
    tmp_path, monkeypatch
):
    risky_catalog(tmp_path, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    admissions = []

    class HeldSource(SyntheticSource):
        async def invoke(self, request):
            admissions.append(request.invocationId)
            entered.set()
            await release.wait()
            return await super().invoke(request)

    app = create_app(source_factory=lambda kind, sid: HeldSource(sid))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()
            route = "/api/v1/sessions/" + created["sessionId"]
            first = (await client.post("/api/v1/plans", json=plan(close_step()))).json()
            second = (await client.post("/api/v1/plans", json=plan(close_step()))).json()
            for submitted in (first, second):
                response = await client.post(
                    "/api/v1/approvals/" + submitted["approvalId"] + "/decision", json={"decision": "approve"}
                )
                assert response.status_code == 200
            request = {
                "requestId": "held-grant",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "planId": first["id"],
                "stepId": 1,
            }
            admitted = asyncio.create_task(client.post(route + "/actions", json=request))
            try:
                await asyncio.wait_for(entered.wait(), 1)
                reparented = await client.post(route + "/actions", json={**request, "planId": second["id"]})
                assert (
                    reparented.status_code == 409
                    and reparented.json()["detail"]["code"] == "idempotency_conflict"
                )
                concurrent = await client.post(
                    route + "/actions", json={**request, "requestId": "concurrent"}
                )
                assert concurrent.json()["error"]["code"] == "busy"
                revision = await client.post(
                    "/api/v1/plans/" + first["id"] + "/revisions", json=plan(close_step())
                )
                assert revision.status_code == 409 and revision.json()["detail"]["code"] == "plan_busy"
                assert len(admissions) == 1
                untouched = (await client.get("/api/v1/plans/" + second["id"])).json()
                assert untouched["steps"][0]["status"] == "pending"
                release.set()
                accepted = await admitted
                assert accepted.json()["status"] in ("accepted", "running", "succeeded"), accepted.text
            finally:
                release.set()
                await asyncio.gather(admitted, return_exceptions=True)
                await client.delete(route)


def test_legacy_high_auto_works_without_a_plan_but_unmatched_plan_calls_require_individual_approval(
    tmp_path,
    monkeypatch,
):
    risky_catalog(tmp_path, monkeypatch)
    with TestClient(create_app()) as client:
        _, route = create(client, realtimeFactor=10)
        legacy = client.post(
            route + "/actions",
            json={
                "requestId": "legacy-auto",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "wait": True,
            },
        )
        assert legacy.json()["status"] == "succeeded", legacy.text
        client.post("/api/v1/plans", json=plan(close_step())).raise_for_status()
        outside = client.post(
            route + "/actions",
            json={
                "requestId": "outside-high",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
            },
        )
        assert outside.json()["status"] == "pending_approval", outside.text


def test_plan_revocation_is_before_first_step_intake_and_never_claims_to_revoke_queued_physics(
    tmp_path,
    monkeypatch,
):
    risky_catalog(tmp_path, monkeypatch)
    with TestClient(create_app()) as client:
        _, route = create(client)
        submitted = client.post("/api/v1/plans", json=plan(close_step())).json()
        approval = "/api/v1/approvals/" + submitted["approvalId"]
        assert client.post(approval + "/decision", json={"decision": "approve"}).json()["canRevoke"]
        withdrawn = client.post(approval + "/decision", json={"decision": "revoke", "note": "核对计划"})
        assert withdrawn.json()["status"] == "revoked"
        request = {
            "requestId": "revoked-grant",
            "entityKey": CENTRIFUGE,
            "action": "closeLid",
            "planId": submitted["id"],
            "stepId": 1,
        }
        refused = client.post(route + "/actions", json=request)
        assert refused.json()["error"]["code"] == "plan_not_approved", refused.text
        revised = client.post(
            "/api/v1/plans/" + submitted["id"] + "/revisions", json=plan(close_step())
        ).json()
        assert revised["revision"] == 2 and revised["status"] == "submitted"
        assert client.post(approval + "/decision", json={"decision": "approve"}).status_code == 409
        latest = "/api/v1/approvals/" + revised["approvalId"]
        client.post(latest + "/decision", json={"decision": "approve"}).raise_for_status()
        client.post(
            route + "/commands",
            json={
                "type": "command.request",
                "requestId": "pause",
                "command": "pause",
            },
        ).raise_for_status()
        accepted = client.post(route + "/actions", json={**request, "requestId": "queued-grant"}).json()
        assert accepted["status"] == "accepted" and accepted["startedSimTime"] is None
        conflict = client.post(latest + "/decision", json={"decision": "revoke"})
        assert conflict.status_code == 409
        current = client.get(latest).json()
        assert current["status"] == "approved" and current["canRevoke"] is False
        assert [item["decision"] for item in current["decisions"]] == ["approve"]
        cancelled = client.post(route + "/invocations/" + accepted["invocationId"] + "/cancel")
        assert cancelled.status_code in (200, 202)
        observed = client.get(route + "/state", params={"keys": CENTRIFUGE}).json()
        assert observed["entities"][CENTRIFUGE]["values"]["lidOpen"] is True


def test_audited_revocation_prevents_execution_even_when_plan_projection_write_fails(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    app = create_app()
    with TestClient(app) as client:
        _, route = create(client)
        submitted = client.post("/api/v1/plans", json=plan(close_step())).json()
        approval = "/api/v1/approvals/" + submitted["approvalId"]
        client.post(approval + "/decision", json={"decision": "approve"}).raise_for_status()
        store = app.state.manager.plans

        def failed_write(record):
            raise OSError("Injected plan disk failure")

        with monkeypatch.context() as patch:
            patch.setattr(store, "_save", failed_write)
            with pytest.raises(OSError, match="Injected plan disk failure"):
                client.post(approval + "/decision", json={"decision": "revoke"})
        audited = client.get(approval).json()
        assert audited["status"] == "revoked"
        forbidden = client.post(
            route + "/actions",
            json={
                "requestId": "after-durable-revoke",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "planId": submitted["id"],
                "stepId": 1,
            },
        )
        assert forbidden.json()["error"]["code"] == "plan_not_approved", forbidden.text
        repaired = client.post(approval + "/decision", json={"decision": "revoke"})
        assert repaired.status_code == 200 and len(repaired.json()["decisions"]) == 2
        assert client.get("/api/v1/plans/" + submitted["id"]).json()["status"] == "abandoned"

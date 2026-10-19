"""Plans observe real Synthetic actions through the public HTTP and reliable stream."""

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_info import CENTRIFUGE, create, risky_catalog, until

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.log import read_segment


def plan(*steps, **fields):
    return {"title": "Close and verify", "target": "session", "steps": list(steps), **fields}


def close_step(step_id=1, expected=False):
    return {
        "id": step_id,
        "do": {"entity": CENTRIFUGE, "action": "closeLid"},
        "expect": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": expected},
    }


def test_operation_plan_tracks_actual_call_expect_and_recorded_wire(tmp_path):
    settings = Settings(recordings_root=tmp_path / "recordings", state_root=tmp_path / "state")
    with TestClient(create_app(settings)) as client:
        session, route = create(client, realtimeFactor=10)
        condition = {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False}
        response = client.post(
            "/api/v1/plans",
            json=plan(
                close_step(),
                {"id": 2, "wait": condition, "timeoutSeconds": 2},
                {"id": 3, "check": condition},
            ),
        )
        assert response.status_code == 201, response.text
        submitted = response.json()
        assert submitted["status"] == "approved" and submitted["revision"] == 1
        invoked = client.post(
            route + "/actions",
            json={
                "requestId": "planned-close",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "planId": submitted["id"],
                "stepId": 1,
                "wait": True,
                "timeoutSeconds": 4,
            },
        )
        assert invoked.status_code == 200 and invoked.json()["status"] == "succeeded", invoked.text
        waited = client.get(
            route + "/wait",
            params={
                "key": CENTRIFUGE,
                "property": "lidOpen",
                "op": "equals",
                "value": "false",
                "planId": submitted["id"],
                "stepId": 2,
            },
        )
        assert waited.json()["satisfied"], waited.text
        checked = client.post(
            route + "/check",
            json={
                "key": CENTRIFUGE,
                "property": "lidOpen",
                "op": "equals",
                "value": False,
                "planId": submitted["id"],
                "stepId": 3,
            },
        )
        assert checked.json()["satisfied"], checked.text
        observed = client.get("/api/v1/plans/" + submitted["id"]).json()
        assert observed["status"] == "completed" and observed["steps"][0]["status"] == "done"
        assert all(item["status"] == "done" for item in observed["steps"])
        with client.websocket_connect(route + "/ws") as ws:
            ready = until(ws, "server.ready")
            ws.send_json({"type": "client.subscribe", "sceneRevision": ready["sceneRevision"]})
            full = until(ws, "plan.update", update="full")
            assert full["plan"]["id"] == submitted["id"] and full["plan"]["status"] == "completed"
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        updates = [item["message"] for item in events if item["message"]["type"] == "plan.update"]
        assert any(item.get("status") == "done" for item in updates)
        assert any(item.get("plan", {}).get("status") == "completed" for item in updates)
        assert "planId" not in invoked.json() and "stepId" not in invoked.json()
        client.delete(route).raise_for_status()
    directory = settings.recordings_root / session["sessionId"]
    metadata = json.loads((directory / "recording.json").read_bytes())
    recorded = [
        json.loads(record.payload)
        for segment in metadata["segments"]
        for record in read_segment((directory / segment["file"]).read_bytes()).records
        if record.kind == 2
    ]
    assert any(item["type"] == "plan.update" and item.get("status") == "done" for item in recorded)


def test_high_plan_requires_human_and_does_not_reuse_exact_step_grant(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    with TestClient(create_app()) as client:
        _, route = create(client, approval={"high": "require"}, realtimeFactor=10)
        submitted = client.post("/api/v1/plans", json=plan(close_step())).json()
        assert submitted["status"] == "submitted"
        body = {
            "requestId": "before-approval",
            "entityKey": CENTRIFUGE,
            "action": "closeLid",
            "planId": submitted["id"],
            "stepId": 1,
        }
        refused = client.post(route + "/actions", json=body)
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "plan_not_approved"
        approval = "/api/v1/approvals/" + submitted["approvalId"]
        denied = client.post(
            approval + "/decision", headers={"X-Actor": "agent:bot"}, json={"decision": "approve"}
        )
        assert denied.status_code == 403
        approved = client.post(approval + "/decision", json={"decision": "approve"})
        assert approved.status_code == 200, approved.text
        exact = client.post(route + "/actions", json={**body, "requestId": "approved-exact", "wait": True})
        assert exact.json()["status"] == "succeeded", exact.text
        repeated = client.post(route + "/actions", json={**body, "requestId": "repeat-completed"})
        assert repeated.json()["status"] == "pending_approval", repeated.text


@pytest.mark.parametrize(
    "step",
    [
        {"id": 1, "do": {"entity": "/World/Unknown", "action": "closeLid"}},
        {"id": 1, "do": {"entity": CENTRIFUGE, "action": "unknown"}},
        {"id": 1, "do": {"entity": CENTRIFUGE, "action": "start", "params": {"rpm": "fast"}}},
        {"id": 1, "check": {"entity": CENTRIFUGE, "property": "lidOpen", "lt": 3}},
        {"id": 1, "wait": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": "yes"}},
        {"id": 1, "check": {"entity": CENTRIFUGE, "property": "unknown", "equals": True}},
        {"id": 1, "check": {"entity": CENTRIFUGE, "property": "lidOpen", "unknownOp": True}},
        {"id": 1, "do": {"entity": CENTRIFUGE, "action": "closeLid"}, "status": "done"},
    ],
)
def test_static_invalid_plan_reports_the_step_without_allocation(step):
    with TestClient(create_app()) as client:
        create(client)
        invalid = client.post("/api/v1/plans", json=plan(step))
        assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "invalid_plan", invalid.text
        assert invalid.json()["detail"]["errors"][0]["stepId"] == 1
        assert client.get("/api/v1/plans").json()["plans"] == []


def test_failed_expect_out_of_order_outside_and_revision_history():
    with TestClient(create_app()) as client:
        _, route = create(client, realtimeFactor=10)
        content = plan(
            {"id": 1, "check": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": True}},
            close_step(2, expected=True),
        )
        submitted = client.post("/api/v1/plans", json=content).json()
        action = {
            "requestId": "jump-close",
            "entityKey": CENTRIFUGE,
            "action": "closeLid",
            "planId": submitted["id"],
            "stepId": 2,
            "wait": True,
        }
        assert client.post(route + "/actions", json=action).json()["status"] == "succeeded"
        observed = client.get("/api/v1/plans/" + submitted["id"]).json()
        assert observed["steps"][1]["status"] == "failed"
        assert observed["stepResults"]["2"]["outOfOrder"] is True
        assert observed["stepResults"]["2"]["failed"][0]["actual"] is False
        outside = client.post(
            route + "/actions",
            json={
                "requestId": "outside-open",
                "entityKey": CENTRIFUGE,
                "action": "openLid",
                "wait": True,
            },
        )
        assert outside.json()["status"] == "succeeded", outside.text
        events = client.get(route + "/events", params={"timeoutSeconds": 0}).json()["events"]
        marked = [
            item["message"]["data"] for item in events if item["message"].get("event") == "plan.invocation"
        ]
        assert any(item["outOfPlan"] for item in marked)
        assert any(item["outOfOrder"] for item in marked)
        # Reconcile the failed step through a real retry, then preserve its completed content.
        assert (
            client.post(route + "/actions", json={**action, "requestId": "retry-close"}).json()["status"]
            == "succeeded"
        )
        finished = client.get("/api/v1/plans/" + submitted["id"]).json()
        assert finished["steps"][1]["status"] == "failed"  # the false expectation remains false
        changed = deepcopy(content)
        changed["steps"][1]["expect"]["equals"] = False
        revised = client.post("/api/v1/plans/" + submitted["id"] + "/revisions", json=changed)
        assert revised.status_code == 201 and revised.json()["revision"] == 2, revised.text
        previous = client.get("/api/v1/plans/" + submitted["id"], params={"revision": 1}).json()
        assert previous["steps"][1]["expect"]["equals"] is True
        stale = client.post(
            route + "/actions", json={**action, "requestId": "old-revision", "planRevision": 1}
        )
        assert stale.json()["error"]["code"] == "invalid_plan_reference", stale.text


def test_completed_content_is_immutable_and_idempotency_cannot_reparent_a_call():
    with TestClient(create_app()) as client:
        _, route = create(client, realtimeFactor=10)
        content = plan(close_step())
        first = client.post("/api/v1/plans", json=content).json()
        second = client.post("/api/v1/plans", json=content).json()
        body = {
            "requestId": "one-identity",
            "entityKey": CENTRIFUGE,
            "action": "closeLid",
            "planId": first["id"],
            "stepId": 1,
            "wait": True,
        }
        original = client.post(route + "/actions", json=body).json()
        assert original["status"] == "succeeded"
        assert client.post(route + "/actions", json=body).json() == original
        conflict = client.post(route + "/actions", json={**body, "planId": second["id"]})
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "idempotency_conflict"
        assert client.get("/api/v1/plans/" + second["id"]).json()["steps"][0]["status"] == "pending"
        preserved = client.post(
            "/api/v1/plans/" + first["id"] + "/revisions",
            json=plan(
                close_step(),
                {"id": 2, "check": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False}},
            ),
        )
        assert preserved.status_code == 201 and preserved.json()["steps"][0]["status"] == "done", (
            preserved.text
        )
        for bad in (
            plan(close_step(expected=True)),
            plan(
                {
                    "id": 2,
                    "check": {
                        "entity": CENTRIFUGE,
                        "property": "lidOpen",
                        "equals": False,
                    },
                }
            ),
            plan(close_step(), close_step()),
        ):
            response = client.post("/api/v1/plans/" + first["id"] + "/revisions", json=bad)
            assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_plan"


def test_plan_target_cannot_follow_reset_or_another_session():
    with TestClient(create_app()) as client:
        _, route = create(client)
        submitted = client.post("/api/v1/plans", json=plan(close_step())).json()
        body = {
            "requestId": "stale-epoch",
            "entityKey": CENTRIFUGE,
            "action": "closeLid",
            "planId": submitted["id"],
            "stepId": 1,
        }
        assert (
            client.post(
                route + "/commands",
                json={
                    "type": "command.request",
                    "requestId": "reset",
                    "command": "reset",
                },
            ).status_code
            == 200
        )
        stale = client.post(route + "/actions", json=body)
        assert stale.json()["error"]["code"] == "invalid_plan_reference", stale.text
        client.delete(route).raise_for_status()
        _, another = create(client)
        other = client.post(another + "/actions", json={**body, "requestId": "stale-session"})
        assert other.json()["error"]["code"] == "invalid_plan_reference", other.text
        unavailable = client.post("/api/v1/plans", json=plan(close_step(), target="live"))
        assert unavailable.status_code == 503 and unavailable.json()["detail"]["code"] == "target_unavailable"
        real = client.post(
            "/api/v1/plans",
            json=plan(
                {
                    "id": 1,
                    "do": {"entity": "/Real/Incubator", "action": "setSetpoint", "params": {"value": 37}},
                }
            ),
        )
        assert real.status_code == 503 and real.json()["detail"]["code"] == "target_unavailable"

"""Attention snapshots and continuation use actual Synthetic HTTP mutations."""

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from test_alerts import alert_catalog as alert_catalog
from test_alerts import await_alerts, control, inject_reading, paused_session
from test_info import CENTRIFUGE
from test_plans import close_step, plan

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

UV = "/World/Lab/BiosafetyCabinet"


def poll(client, **params):
    response = client.get("/api/v1/inbox", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def session(client):
    response = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2", "controlHz": 20})
    assert response.status_code == 201, response.text
    sid = response.json()["sessionId"]
    return sid, "/api/v1/sessions/" + sid


def action(client, route, key, name, **params):
    return client.post(route + "/actions", json={"requestId": uuid4().hex, "entityKey": key,
                                                 "action": name, "params": params})


def submitted_plan(client, **fields):
    response = client.post("/api/v1/plans", json={"title": "Inspect UV", "target": "session", "steps": [
        {"id": 1, "do": {"entity": UV, "action": "setUv", "params": {"on": True}}}], **fields})
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "submitted"
    return response.json()


def test_first_snapshot_active_acknowledged_alert_pending_plan_and_real_clear():
    with TestClient(create_app()) as client:
        sid, route = session(client)
        control(client, route, "pause")
        control(client, route, "reset")
        uv = action(client, route, UV, "setUv", on=True)
        assert uv.status_code == 202
        control(client, route, "step", count=120)
        terminal = client.get(route + "/invocations/" + uv.json()["invocationId"],
                              params={"wait": True, "timeoutSeconds": 2})
        assert terminal.json()["status"] == "succeeded", terminal.text
        [alert] = await_alerts(client, sid, 1, active=True)
        pending = submitted_plan(client)
        first = poll(client)
        assert [value["alertId"] for value in first["alerts"]] == [alert["alertId"]]
        assert [value["approvalId"] for value in first["approvals"]] == [pending["approvalId"]]
        assert {message["kind"] for message in first["messages"]} == {"alert", "approval", "plan"}
        acknowledged = client.post("/api/v1/alerts/" + alert["alertId"] + "/ack", json={"note": "Inspecting"})
        assert acknowledged.status_code == 200
        assert poll(client, after=first["cursor"])["messages"] == []
        assert poll(client)["alerts"][0]["state"] == "acknowledged"
        off = action(client, route, UV, "setUv", on=False)
        assert off.status_code == 202, off.text
        # An active plan keeps unrelated high-risk actions at the human gate.
        if off.json()["status"] == "pending_approval":
            approval = next(record for record in client.get("/api/v1/approvals").json()["approvals"]
                            if record["subjectId"] == off.json()["invocationId"])
            approved = client.post("/api/v1/approvals/" + approval["approvalId"] + "/decision",
                                   json={"decision": "approve"})
            assert approved.status_code == 200, approved.text
        control(client, route, "step", count=120)
        await_alerts(client, sid, 0, active=True)
        cleared = poll(client, after=first["cursor"], kinds="alert")
        assert [(message["kind"], message["event"]) for message in cleared["messages"]] == [
            ("alert", "cleared")]
        decision = client.post("/api/v1/approvals/" + pending["approvalId"] + "/decision",
                               json={"decision": "deny"})
        assert decision.status_code == 200
        result = poll(client, after=cleared["cursor"])
        assert [(message["kind"], message["event"]) for message in result["messages"]] == [
            ("approval", "denied"), ("plan", "abandoned")]
        assert poll(client)["alerts"] == poll(client)["approvals"] == []


@pytest.mark.parametrize("end", ["reset", "reload", "stop"])
def test_context_end_removes_current_alerts_without_fabricating_clear(alert_catalog, end):
    with TestClient(create_app()) as client:
        sid, route = paused_session(client)
        inject_reading(client, route, 39)
        control(client, route, "step", count=72)
        [alert] = await_alerts(client, sid, 1, active=True)
        before = poll(client)
        if end == "reset":
            control(client, route, "reset")
        elif end == "reload":
            assert client.post(route + "/reload").status_code == 200
        else:
            assert client.delete(route).status_code == 204
        assert poll(client)["alerts"] == []
        assert not [message for message in poll(client, after=before["cursor"])["messages"]
                    if message["kind"] == "alert" and message["data"]["alertId"] == alert["alertId"]]


def test_failed_and_rejected_remain_distinct_and_duplicate_requests_do_not_notify():
    with TestClient(create_app()) as client:
        _, route = session(client)
        empty = poll(client)
        body = {"requestId": "inbox-rejected", "entityKey": CENTRIFUGE, "action": "missing"}
        rejected = client.post(route + "/actions", json=body)
        assert rejected.status_code == 404 and rejected.json()["status"] == "rejected"
        assert client.post(route + "/actions", json=body).json() == rejected.json()
        close = client.post(route + "/actions", json={"requestId": "inbox-close", "entityKey": CENTRIFUGE,
                                                      "action": "closeLid", "wait": True})
        assert close.json()["status"] == "succeeded"
        control(client, route, "pause")
        running = action(client, route, CENTRIFUGE, "start", rpm=500, durationSeconds=30)
        assert running.status_code == 202
        control(client, route, "reset")
        after = poll(client, after=empty["cursor"])
        calls = [message for message in after["messages"] if message["kind"] == "invocation"]
        assert [(message["event"], message["data"]["status"]) for message in calls] == [
            ("rejected", "rejected"), ("failed", "failed")]
        assert calls[0]["data"]["invocationId"] == rejected.json()["invocationId"]
        assert calls[1]["data"]["invocationId"] == running.json()["invocationId"]


def test_first_returns_all_pending_over_twenty_and_filters_advance_one_global_cursor():
    with TestClient(create_app()) as client:
        session(client)
        initial = poll(client)
        pending = [submitted_plan(client, title=f"Pending {index}") for index in range(23)]
        first = poll(client)
        assert len(first["messages"]) == 20
        assert {record["approvalId"] for record in first["approvals"]} == {
            record["approvalId"] for record in pending}
        filtered = poll(client, after=initial["cursor"], kinds="plan")
        assert len(filtered["messages"]) == 23
        assert {message["kind"] for message in filtered["messages"]} == {"plan"}
        assert filtered["alerts"] == filtered["approvals"] == []
        skipped = poll(client, after=initial["cursor"], kinds="job")
        assert skipped["messages"] == [] and skipped["cursor"] == first["cursor"]
        assert poll(client, after=skipped["cursor"], kinds="approval")["messages"] == []
        assert len(poll(client, kinds="approval")["approvals"]) == 23
        assert poll(client, kinds="plan")["approvals"] == []
        invalid = client.get("/api/v1/inbox", params={"kinds": "alert,unknown"})
        assert invalid.status_code == 422


def test_real_plan_revision_steps_completion_and_noop_terminal_reads():
    with TestClient(create_app()) as client:
        _, route = session(client)
        empty = poll(client)
        created = client.post("/api/v1/plans", json=plan(close_step())).json()
        revised = client.post("/api/v1/plans/" + created["id"] + "/revisions",
                              json=plan(close_step(), title="Revised close")).json()
        assert revised["revision"] == 2
        result = client.post(route + "/actions", json={"requestId": "inbox-plan", "entityKey": CENTRIFUGE,
                                                       "action": "closeLid", "planId": created["id"],
                                                       "stepId": 1, "wait": True})
        assert result.json()["status"] == "succeeded"
        after = poll(client, after=empty["cursor"], kinds="plan")
        plans = [message["data"] for message in after["messages"]]
        assert [(value["revision"], value["status"]) for value in plans][:2] == [
            (1, "approved"), (2, "approved")]
        assert plans[-1]["status"] == "completed" and plans[-1]["steps"][0]["status"] == "done"
        client.get("/api/v1/plans/" + created["id"]).raise_for_status()
        assert poll(client, after=after["cursor"])["messages"] == []


def test_cursor_survives_gateway_recreation_and_partial_tail_without_replaying(tmp_path):
    settings = Settings(recordings_root=tmp_path / "recordings", state_root=tmp_path / "state")
    with TestClient(create_app(settings)) as client:
        session(client)
        pending = submitted_plan(client)
        before = poll(client)
    path = settings.state_root / "inbox.jsonl"
    with path.open("ab") as stream:
        stream.write(b'{"seq":999,"kind":')
    with TestClient(create_app(settings)) as client:
        after = poll(client, after=before["cursor"])
        assert [(value["kind"], value["event"]) for value in after["messages"]] == [
            ("approval", "unavailable"), ("plan", "abandoned")]
        assert all(value["data"].get("id", value["data"].get("subjectId")) == pending["id"]
                   for value in after["messages"])
        assert poll(client)["alerts"] == poll(client)["approvals"] == []
        assert poll(client, after=after["cursor"])["messages"] == []
        _, route = session(client)
        rejected = action(client, route, CENTRIFUGE, "missing")
        assert rejected.status_code == 404
        continued = poll(client, after=after["cursor"])
        assert len(continued["messages"]) == 1
        assert continued["messages"][0]["data"]["invocationId"] == rejected.json()["invocationId"]
        token = after["cursor"]
        tampered = token[:10] + ("A" if token[10] != "A" else "B") + token[11:]
        assert client.get("/api/v1/inbox", params={"after": tampered}).status_code == 422
    with TestClient(create_app(Settings(state_root=tmp_path / "foreign"))) as foreign:
        assert foreign.get("/api/v1/inbox", params={"after": token}).status_code == 422
    assert all(json.loads(line)["seq"] > 0 for line in path.read_bytes().splitlines()[1:])

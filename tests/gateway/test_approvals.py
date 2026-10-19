"""Approval inbox decisions drive real Synthetic actions and preserve their audit."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from test_info import CENTRIFUGE, action, create, risky_catalog

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource


def command(client, route, name):
    response = client.post(
        route + "/commands",
        json={
            "type": "command.request",
            "requestId": name,
            "command": name,
        },
    )
    assert response.status_code == 200, response.text


def pending(client, route, request_id="risky"):
    invocation = action(client, route, request_id, "closeLid").json()
    assert invocation["status"] == "pending_approval"
    inbox = client.get("/api/v1/approvals").json()
    assert inbox["pendingCount"] == 1
    record = next(item for item in inbox["approvals"] if item["subjectId"] == invocation["invocationId"])
    assert record["subject"] == "invocation" and record["details"]["entityKey"] == CENTRIFUGE
    return invocation, "/api/v1/approvals/" + record["approvalId"]


def test_inbox_approval_execution_audit_and_restart(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    settings = Settings()
    with TestClient(create_app(settings)) as client:
        _, route = create(client, approval={"high": "require"}, realtimeFactor=10)
        invocation, approval = pending(client, route)
        denied = client.post(
            approval + "/decision", headers={"X-Actor": "agent:bot"}, json={"decision": "approve"}
        )
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "approval_requires_human"
        approved = client.post(
            approval + "/decision",
            headers={"X-Actor": "user:operator"},
            json={
                "decision": "approve",
                "note": "确认设备已准备好",
            },
        ).json()
        assert approved["status"] == "approved"
        assert approved["decisions"][0] == {
            "decision": "approve",
            "actor": {"kind": "user", "name": "operator"},
            "atMs": approved["decisions"][0]["atMs"],
            "note": "确认设备已准备好",
        }
        assert approved["decisions"][0]["atMs"] >= approved["createdAtMs"]
        done = client.get(
            route + "/invocations/" + invocation["invocationId"],
            params={
                "wait": True,
                "timeoutSeconds": 3,
            },
        ).json()
        assert done["status"] == "succeeded"
        assert client.get(approval).json()["canRevoke"] is False
        assert client.get("/api/v1/approvals").json()["pendingCount"] == 0
        assert client.post(approval + "/decision", json={"decision": "revoke"}).status_code == 409
    with TestClient(create_app(settings)) as restarted:
        record = restarted.get(approval).json()
        assert record["status"] == "approved" and record["decisions"] == approved["decisions"]
        assert record["canRevoke"] is False
        assert restarted.post(approval + "/decision", json={"decision": "approve"}).status_code == 409


def test_revoke_before_start_cancels_authoritatively_and_never_resurrects_denied(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    with TestClient(create_app()) as client:
        _, route = create(client, approval={"high": "require"})
        command(client, route, "pause")
        invocation, approval = pending(client, route)
        approved = client.post(approval + "/decision", json={"decision": "approve"}).json()
        assert approved["canRevoke"] and approved["invocation"]["status"] == "accepted"
        revoked = client.post(
            approval + "/decision", json={"decision": "revoke", "note": "需重新核对"}
        ).json()
        assert revoked["status"] == "revoked" and revoked["invocation"]["status"] == "cancelled"
        assert [item["decision"] for item in revoked["decisions"]] == ["approve", "revoke"]
        command(client, route, "resume")
        assert (
            client.get(route + "/invocations/" + invocation["invocationId"]).json()["status"] == "cancelled"
        )
        assert client.get(route + "/state", params={"keys": CENTRIFUGE}).json()["entities"][CENTRIFUGE][
            "values"
        ]["lidOpen"]
        _, denied_approval = pending(client, route, "denied")
        assert (
            client.post(denied_approval + "/decision", json={"decision": "deny"}).json()["status"] == "denied"
        )
        withdrawn = client.post(denied_approval + "/decision", json={"decision": "revoke"}).json()
        assert withdrawn["status"] == "revoked" and withdrawn["invocation"]["status"] == "rejected"
        legacy = client.post(
            route + "/invocations/" + withdrawn["subjectId"] + "/approve", json={"decision": "approve"}
        )
        assert legacy.json()["status"] == "rejected"


def test_host_execution_wins_revocation_race_without_ordinary_cancellation(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    ordinary_cancels = []

    class RacingSource(SyntheticSource):
        async def revoke_before_start(self, invocation_id):
            async with self._lock:
                # The gateway still sees accepted when the host enters its first real control tick.
                self._advance(4)
                result = self.host.revoke_before_start(invocation_id)
                for event in self.host.drain_events():
                    self._events.put_nowait(event)
                return result

        async def cancel(self, invocation_id):
            ordinary_cancels.append(invocation_id)
            return await super().cancel(invocation_id)

    with TestClient(create_app(source_factory=lambda kind, sid: RacingSource(sid))) as client:
        _, route = create(client, approval={"high": "require"})
        command(client, route, "pause")
        _, approval = pending(client, route)
        assert client.post(approval + "/decision", json={"decision": "approve"}).json()["canRevoke"]
        conflict = client.post(approval + "/decision", json={"decision": "revoke"})
        assert conflict.status_code == 409, conflict.text
        record = client.get(approval).json()
        assert record["status"] == "approved" and record["invocation"]["status"] == "running"
        assert len(record["decisions"]) == 1 and ordinary_cancels == []


def test_concurrent_new_and_legacy_decisions_share_one_dispatch_and_audit(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    dispatched = []

    class CountSource(SyntheticSource):
        async def invoke(self, request):
            dispatched.append(request.invocationId)
            return await super().invoke(request)

    with TestClient(create_app(source_factory=lambda kind, sid: CountSource(sid))) as client:
        _, route = create(client, approval={"high": "require"})
        invocation, approval = pending(client, route)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(
                pool.map(
                    lambda endpoint: client.post(endpoint, json={"decision": "approve"}),
                    (
                        approval + "/decision",
                        route + "/invocations/" + invocation["invocationId"] + "/approve",
                    ),
                )
            )
        assert all(reply.status_code in (200, 202) for reply in replies)
        assert dispatched == [invocation["invocationId"]]
        assert len(client.get(approval).json()["decisions"]) == 1


def test_conflicting_human_decisions_have_one_winner(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)

    class SlowSource(SyntheticSource):
        async def invoke(self, request):
            await asyncio.sleep(0.05)
            return await super().invoke(request)

    with TestClient(create_app(source_factory=lambda kind, sid: SlowSource(sid))) as client:
        _, route = create(client, approval={"high": "require"})
        command(client, route, "pause")
        _, approval = pending(client, route)
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(
                pool.map(
                    lambda decision: client.post(approval + "/decision", json={"decision": decision}),
                    ("approve", "deny"),
                )
            )
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        record = client.get(approval).json()
        assert len(record["decisions"]) == 1
        assert record["status"] in ("approved", "denied")


def test_inbox_reports_actual_readonly_role_and_denies_viewer_decisions(tmp_path, monkeypatch):
    risky_catalog(tmp_path, monkeypatch)
    settings = Settings(remote=True, operator_token="test-operator", viewer_token="test-viewer")
    operator = {"Authorization": "Bearer test-operator"}
    viewer = {"Authorization": "Bearer test-viewer"}
    with TestClient(create_app(settings), headers=operator) as client:
        _, route = create(client, approval={"high": "require"})
        _, approval = pending(client, route)
        inbox = client.get("/api/v1/approvals", headers=viewer).json()
        assert inbox["role"] == "viewer" and inbox["pendingCount"] == 1
        assert client.get("/api/v1/approvals").json()["role"] == "operator"
        response = client.post(approval + "/decision", headers=viewer, json={"decision": "approve"})
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "unauthorized"
        assert client.get(approval).json()["status"] == "pending"
        assert client.get(approval).json()["decisions"] == []

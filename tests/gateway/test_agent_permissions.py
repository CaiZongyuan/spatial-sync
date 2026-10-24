"""Managed authority is enforced by the real HTTP/pipeline, not self-reported actor."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from lw.client import GatewayError
from lw.mcp_server import create_server
from lw.scoped import TARGET_TOOLS, ScopedGatewayClient
from mcp import Client, Implementation

from apps.api.app.main import create_app
from apps.api.app.sources import synthetic_source
from packages.info.catalog import load_catalog

CENTRIFUGE = "/World/Lab/Centrifuge"


@pytest.fixture(autouse=True)
def declared_high_risk_close_lid(monkeypatch):
    def catalog(*args, **kwargs):
        result = load_catalog(*args, **kwargs)
        result.types["centrifuge/v1"]["actions"]["closeLid"]["risk"] = "high"
        return result

    monkeypatch.setattr(synthetic_source, "load_catalog", catalog)


def managed(client, *, active=lambda: True):
    result = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "source": "synthetic"})
    assert result.status_code == 201, result.text
    identity = result.json()["sessionId"]
    session = client.app.state.manager.get(identity)
    session.agent_run_id = "job-agent-fixture"
    session.evaluation_id = "eval-agent-fixture"
    session.deny_human_gates = True
    token = client.app.state.agent_grants.issue("job-agent-fixture", identity, active=active)
    return identity, {"Authorization": "Bearer " + token}


def test_actual_owned_session_controls_work_but_no_actor_header_can_acquire_human_or_private_access():
    app = create_app()
    with TestClient(app) as client:
        identity, grant = managed(client)
        base = f"/api/v1/sessions/{identity}"
        for actor in ({}, {"X-Actor": "user:local"}, {"X-Actor": "cli:lw"}):
            headers = grant | actor
            for method, path, data in (
                ("POST", "/api/v1/approvals/approval-forged/decision", {"decision": "approve"}),
                ("GET", "/api/v1/vault", None),
                ("GET", "/api/v1/suites/lab-ops-basic/1", None),
                ("GET", "/recordings/private-grading/recording.json", None),
                ("GET", "/api/v1/sessions/session-foreign/state", None),
                ("POST", "/api/v1/plans", {"kind": "training", "sessionId": identity}),
            ):
                response = client.request(method, path, headers=headers, json=data)
                assert response.status_code == 403, (path, response.text)
        for number, command in enumerate(("pause", "reset")):
            result = client.post(
                base + "/commands",
                headers=grant | {"X-Actor": "user:local"},
                json={"requestId": f"control-{number}", "command": command},
            )
            assert result.status_code == 200 and result.json()["ack"]["status"] == "applied", result.text
        assert result.json()["state"]["epoch"] == 1
        assert client.get(base + "/state", headers=grant).status_code == 200
        assert (
            client.post(
                base + "/commands",
                headers=grant,
                json={"requestId": "reload", "command": "set_realtime_factor", "args": {"value": 100}},
            ).status_code
            == 403
        )


def test_managed_high_risk_is_denied_by_policy_even_with_auto_setting_and_forged_user_actor():
    app = create_app()
    with TestClient(app) as client:
        identity, grant = managed(client)
        base = f"/api/v1/sessions/{identity}"
        before = client.get(base + "/state", headers=grant, params={"keys": CENTRIFUGE}).json()
        denied = client.post(
            base + "/actions",
            headers=grant | {"X-Actor": "user:local"},
            json={"requestId": "high-risk", "entityKey": CENTRIFUGE, "action": "closeLid", "params": {}},
        )
        assert denied.status_code == 409, denied.text
        value = denied.json()
        assert value["status"] == "rejected" and value["error"]["code"] == "approval_denied"
        assert value["actor"]["kind"] == "agent"
        after = client.get(base + "/state", headers=grant, params={"keys": CENTRIFUGE}).json()
        assert (
            after["entities"][CENTRIFUGE]["values"]["lidOpen"]
            == before["entities"][CENTRIFUGE]["values"]["lidOpen"]
        )
        assert client.get("/api/v1/approvals").json()["pendingCount"] == 0


def test_revoked_grant_is_not_a_local_operator_and_ordinary_human_approval_keeps_working():
    app = create_app()
    with TestClient(app) as client:
        # Human path remains the original operator+human decision, outside Target authority.
        normal = client.post(
            "/api/v1/sessions",
            json={
                "sceneVersion": "lab-demo@3",
                "source": "synthetic",
                "approval": {"high": "require"},
            },
        ).json()["sessionId"]
        pending = client.post(
            f"/api/v1/sessions/{normal}/actions",
            json={
                "requestId": "normal-human",
                "entityKey": CENTRIFUGE,
                "action": "closeLid",
                "params": {},
            },
        ).json()
        assert pending["status"] == "pending_approval", pending
        approval = next(
            row
            for row in client.get("/api/v1/approvals").json()["approvals"]
            if row["subjectId"] == pending["invocationId"]
        )
        assert (
            client.post(
                f"/api/v1/approvals/{approval['approvalId']}/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        completed = client.get(
            f"/api/v1/sessions/{normal}/invocations/{pending['invocationId']}",
            params={"wait": True, "timeoutSeconds": 1},
        ).json()
        assert completed["status"] == "succeeded", completed
        client.delete(f"/api/v1/sessions/{normal}")
        active = [True]
        identity, grant = managed(client, active=lambda: active[0])
        active[0] = False
        assert (
            client.get(
                f"/api/v1/sessions/{identity}/state", headers=grant | {"X-Actor": "user:local"}
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/sessions/{identity}/state", headers={"Authorization": "Bearer lw-agent-forged"}
            ).status_code
            == 401
        )


async def tool(client, name, **arguments):
    response = await client.call_tool(name, arguments)
    assert not response.is_error, response.content
    document = response.structured_content
    return document["result"] if set(document) == {"result"} else document


async def test_official_mcp_uses_only_owned_http_projection_and_cannot_publish_or_approve_private_data():
    app = create_app()
    sentinel = "PRIVATE_OTHER_SESSION_GRADING_9d2a"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8048"
        ) as http:
            foreign = (await http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3"})).json()
            foreign_id = foreign["sessionId"]
            secret = (await http.post("/api/v1/plans", json={
                "sessionId": foreign_id, "title": sentinel, "target": "session", "steps": [{
                    "id": 1, "do": {"entity": CENTRIFUGE, "action": "closeLid"},
                }],
            })).json()
            assert secret["status"] == "submitted"
            # A real other-Session Plan and its inbox publication survive Session shutdown.
            await http.delete(f"/api/v1/sessions/{foreign_id}")
            owned = (await http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3"})).json()
            session_id = owned["sessionId"]
            session = app.state.manager.get(session_id)
            session.agent_run_id = "job-mcp-fixture"
            session.evaluation_id = "eval-mcp-fixture"
            session.deny_human_gates = True
            token = app.state.agent_grants.issue(session.agent_run_id, session_id)
            gateway = ScopedGatewayClient(run_id=session.agent_run_id, session=session_id,
                                          token=token, url="http://127.0.0.1:8048", http=http)
            requests = []

            async def observed(request):
                requests.append((request.method, request.url.path))

            http.event_hooks["request"] = [observed]
            server = await create_server(gateway)
            async with Client(server, mode="legacy", client_info=Implementation(
                name="user:forged-human", version="1"
            )) as mcp:
                assert {item.name for item in (await mcp.list_tools()).tools} == TARGET_TOOLS
                assert sentinel not in mcp.session.instructions
                assert await tool(mcp, "plan_list") == []
                first = await tool(mcp, "inbox_poll")
                assert first["messages"] == first["approvals"] == []
                for name, arguments in (
                    ("suite_show", {"suite": "suite/lab-ops-basic@1"}),
                    ("model_ls", {}),
                    ("library_import", {"directory": sentinel}),
                    ("approve_invocation", {"invocation_id": secret["approvalId"]}),
                    ("plan_show", {"id": secret["id"]}),
                    ("get_state", {"keys": ["/Real/PrivateDevice"]}),
                    ("inbox_poll", {"kinds": "approval,job"}),
                ):
                    denied = await mcp.call_tool(name, arguments)
                    assert denied.is_error, (name, denied)
                    assert sentinel not in str(denied)
                read = await tool(mcp, "get_state", keys=[CENTRIFUGE])
                assert read["entities"][CENTRIFUGE]["values"]["lidOpen"] is True
                ordinary = await tool(mcp, "plan_create", document={
                    "title": "Verify own state", "target": "session", "steps": [{
                        "id": 1, "check": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": True},
                    }],
                })
                assert ordinary["sessionId"] == session_id
                assert ordinary["requestedBy"]["kind"] == "agent"
                assert [value["id"] for value in await tool(mcp, "plan_list")] == [ordinary["id"]]
                own_inbox = await tool(mcp, "inbox_poll", after=first["cursor"])
                assert own_inbox["messages"] and own_inbox["approvals"] == []
                assert {row["sessionId"] for row in own_inbox["messages"]} == {session_id}
                assert sentinel not in json.dumps(own_inbox)
                denied_plan = await mcp.call_tool("plan_create", {"document": {
                    "title": "Cannot ask a human", "target": "session", "steps": [{
                        "id": 1, "do": {"entity": CENTRIFUGE, "action": "closeLid"},
                    }],
                }})
                assert denied_plan.is_error and "approval_denied" in str(denied_plan)
                after = await http.get("/api/v1/approvals")
                assert not any(row["sessionId"] == session_id for row in after.json()["approvals"])
                requests.clear()
                missing = await mcp.call_tool("get_invocation", {"invocationId": "inv-deadbeef"})
                assert missing.is_error
                assert requests == [("GET", f"/api/v1/sessions/{session_id}/invocations/inv-deadbeef")]
                app.state.agent_grants.revoke(session.agent_run_id)
                assert (await mcp.call_tool("get_state", {"keys": [CENTRIFUGE]})).is_error


@pytest.mark.parametrize("url", ["https://api.example.test", "http://localhost:8048", "http://127.0.0.1/x"])
def test_managed_mcp_requires_generated_loopback_binding(url):
    with pytest.raises(GatewayError, match="loopback"):
        ScopedGatewayClient(run_id="job-managed", session="session-owned", token="lw-agent-x", url=url)

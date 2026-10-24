"""A real MCP action has one process step and a trusted terminal observation."""

import json

import httpx
import pytest
from lw.mcp_server import create_server
from lw.scoped import ScopedGatewayClient
from mcp import Client

from apps.api.app.main import create_app
from packages.agents.audit import CaseAudit
from packages.info.agent_grading import action_matches, process_metrics

CENTRIFUGE = "/World/Lab/Centrifuge"


@pytest.fixture
async def audited(tmp_path):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as http:
            created = (
                await http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "realtimeFactor": 20})
            ).json()
            session = app.state.manager.get(created["sessionId"])
            session.agent_run_id = "job-audited"
            session.deny_human_gates = True
            audit = CaseAudit(session, tmp_path / "trace.jsonl")
            session.agent_audit = audit
            grant = app.state.agent_grants.issue("job-audited", session.session_id, audit=audit)
            gateway = ScopedGatewayClient(
                run_id="job-audited",
                session=session.session_id,
                token=grant,
                url="http://127.0.0.1",
                http=http,
            )
            try:
                yield Client(await create_server(gateway), mode="legacy"), http, session, audit
            finally:
                app.state.agent_grants.revoke("job-audited")
                await session.close()
                session.agent_audit = None
                audit.close()


async def call(mcp, name, **arguments):
    result = await mcp.call_tool(name, arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def test_no_wait_action_merges_authoritative_terminal_evidence_without_a_second_tool_step(audited):
    mcp, http, session, audit = audited
    async with mcp:
        invoked = await call(mcp, "invoke_action", key=CENTRIFUGE, action="closeLid", wait=False)
    completed = await http.get(
        f"/api/v1/sessions/{session.session_id}/invocations/{invoked['invocationId']}",
        params={"wait": True, "timeoutSeconds": 2},
    )
    assert completed.json()["status"] == "succeeded", completed.text
    final = await http.get(f"/api/v1/sessions/{session.session_id}/state")
    audit.seal(
        {"state": final.json(), "scope": audit.scope()},
        observed_monotonic=float(final.headers["X-LW-Observed-Monotonic"]),
    )
    events = audit.derived()
    assert len(events) == 1 and events[0]["kind"] == "action"
    action = events[0]
    assert action["status"] == "succeeded"
    assert action["before"]["state"]["entities"][CENTRIFUGE]["values"]["lidOpen"] is True
    assert action["after"]["state"]["entities"][CENTRIFUGE]["values"]["lidOpen"] is False
    assert action_matches(
        action,
        {
            "entity": CENTRIFUGE,
            "action": "closeLid",
            "params": {},
            "status": "succeeded",
            "after": {
                "entity": CENTRIFUGE,
                "property": "lidOpen",
                "equals": False,
            },
        },
        audit.scope(),
    )
    assert process_metrics(events, audit.clock() - audit.started)["steps"] == 1
    assert audit.requests[-1]["data"]["tool"] == "invoke_action"
    before_cleanup = json.dumps(events, sort_keys=True)
    await session.close()
    assert json.dumps(audit.derived(), sort_keys=True) == before_cleanup


async def test_unknown_target_action_is_a_real_scored_negative_with_retained_http_rejection(audited):
    mcp, _, _, audit = audited
    async with mcp:
        rejected = await call(mcp, "invoke_action", key=CENTRIFUGE, action="unknownAction")
    assert rejected["status"] == "rejected" and rejected["error"]["code"] == "unknown_action"
    audit.seal({"scope": audit.scope()})
    [event] = audit.derived()
    assert event["kind"] == "action" and event["status"] == "rejected" and event["risk"] == "unknown"
    assert process_metrics([event], audit.clock() - audit.started)["steps"] == 1


async def test_stop_cycle_preserves_actual_running_witness_when_the_original_action_later_ends(audited):
    mcp, http, session, audit = audited
    async with mcp:
        await call(mcp, "invoke_action", key=CENTRIFUGE, action="closeLid")
        await call(
            mcp,
            "invoke_action",
            key=CENTRIFUGE,
            action="start",
            wait=False,
            params={"rpm": 500, "durationSeconds": 30},
        )
        await call(
            mcp,
            "wait_until",
            key=CENTRIFUGE,
            property="phase",
            op="equals",
            value="running",
            timeoutSeconds=2,
        )
        await call(mcp, "invoke_action", key=CENTRIFUGE, action="stop")
    final = await http.get(f"/api/v1/sessions/{session.session_id}/state")
    audit.seal({"state": final.json(), "scope": audit.scope()})
    events = audit.derived()
    [started] = [event for event in events if event.get("action") == "start"]
    assert started["runningObservation"]["invocation"]["status"] == "running"
    assert action_matches(
        started,
        {
            "entity": CENTRIFUGE,
            "action": "start",
            "status": "running",
            "params": {"rpm": 500, "durationSeconds": 30},
            "before": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False},
        },
        audit.scope(),
    )
    metrics = process_metrics(events, audit.clock() - audit.started)
    assert metrics["steps"] == 4 and metrics[f"started:{CENTRIFUGE}.start"] == 1


async def test_final_http_capture_excludes_later_cleanup_terminal_states(audited):
    mcp, http, session, audit = audited
    async with mcp:
        await call(mcp, "invoke_action", key=CENTRIFUGE, action="closeLid")
        start = await call(
            mcp,
            "invoke_action",
            key=CENTRIFUGE,
            action="start",
            wait=False,
            params={"rpm": 500, "durationSeconds": 30},
        )
        await call(
            mcp,
            "wait_until",
            key=CENTRIFUGE,
            property="phase",
            op="equals",
            value="running",
            timeoutSeconds=2,
        )
    final = await http.get(f"/api/v1/sessions/{session.session_id}/state")
    assert final.json()["entities"][CENTRIFUGE]["values"]["phase"] == "running"
    audit.seal(
        {"state": final.json(), "scope": audit.scope()},
        observed_monotonic=float(final.headers["X-LW-Observed-Monotonic"]),
    )
    await session.close()
    started = next(event for event in audit.derived() if event.get("invocationId") == start["invocationId"])
    assert started["status"] == "running"
    assert any(
        record["kind"] == "invocation.observation"
        and record["data"]["invocation"]["invocationId"] == start["invocationId"]
        and record["data"]["invocation"]["status"] in ("failed", "cancelled")
        for record in audit.records
    )

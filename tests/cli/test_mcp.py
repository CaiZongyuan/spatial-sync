"""The pinned official SDK invokes every tool against real Synthetic HTTP handlers."""

import asyncio

import httpx
import pytest
from cli_support import TOOLS, result
from lw.client import GatewayClient
from lw.mcp_server import catalog_instructions, create_server
from mcp import Client, Implementation

from apps.api.app.main import create_app

ROBOT = "/World/Lab/RobotA"
CENTRIFUGE = "/World/Lab/Centrifuge"


@pytest.fixture
async def gateway():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as http:
            await http.post("/api/v1/sessions", json={"sceneId": "lab-demo", "realtimeFactor": 20})
            async with GatewayClient(http=http) as gateway:
                yield gateway


async def test_every_mcp_tool_uses_http_and_actual_peer_attribution_and_rejection_hints(gateway):
    server = await create_server(gateway)
    async with Client(server, client_info=Implementation(name="integration-agent", version="1")) as mcp:
        assert {tool.name for tool in (await mcp.list_tools()).tools} == TOOLS
        assert len(mcp.session.instructions) <= 1500
        assert "rad" in mcp.session.instructions and "rack.A1" in mcp.session.instructions
        entities = await result(mcp, "list_entities", type="centrifuge/v1", query="Centrifuge")
        assert [entity["key"] for entity in entities] == [CENTRIFUGE]
        description = await result(mcp, "describe_entity", key=CENTRIFUGE)
        assert "start" in description["definition"]["actions"]
        state = await result(mcp, "get_state", keys=[CENTRIFUGE, ROBOT])
        assert state["entities"][CENTRIFUGE]["values"]["lidOpen"] is True
        rejected = await result(
            mcp, "invoke_action", key=CENTRIFUGE, action="start", params={"rpm": 500, "durationSeconds": 1}
        )
        assert rejected["status"] == "rejected" and rejected["error"]["code"] == "precondition_failed"
        assert rejected["hint"] == rejected["error"]["details"]["failed"][0]["message"]
        assert rejected["actor"] == {"kind": "agent", "name": "integration-agent"}
        paused = await result(mcp, "session_control", action="pause")
        assert paused["state"] == "PAUSED" and paused["ack"]["status"] == "applied"
        stepped = await result(mcp, "session_control", action="step", count=12)
        assert stepped["simTime"] == pytest.approx(paused["simTime"] + 12 / 240, rel=0, abs=1e-12)
        pending = await result(mcp, "invoke_action", key=CENTRIFUGE, action="closeLid", wait=False)
        timed = await result(
            mcp, "get_invocation", invocationId=pending["invocationId"], wait=True, timeoutSeconds=0.001
        )
        assert timed["timedOut"] is True and timed["status"] == "accepted"
        await result(mcp, "cancel_invocation", invocationId=pending["invocationId"])
        await result(mcp, "session_control", action="resume")
        cancelled = await result(mcp, "get_invocation", invocationId=pending["invocationId"], wait=True)
        assert cancelled["status"] == "cancelled"
        closed = await result(mcp, "invoke_action", key=CENTRIFUGE, action="closeLid")
        assert closed["status"] == "succeeded"
        satisfied = await result(
            mcp, "wait_until", key=CENTRIFUGE, property="lidOpen", op="equals", value=False
        )
        assert satisfied == {"satisfied": True, "value": False, "timedOut": False}
        reset = await result(mcp, "session_control", action="reset")
        assert reset["epoch"] == 1
        missing = await mcp.call_tool("get_invocation", {"invocationId": "missing"})
        assert missing.is_error and '"statusCode": 404' in missing.content[0].text


async def test_catalog_instructions_handle_a_large_catalog_without_losing_workflow(gateway):
    catalog = await gateway.catalog()
    catalog["entities"].update({f"/Telemetry/{index}": {"type": "tube/v1"} for index in range(10_000)})
    instructions = catalog_instructions(catalog)
    assert len(instructions) <= 1500
    assert "Describe an entity" in instructions and "error.details" in instructions


async def test_shared_gateway_client_attributes_concurrent_calls_to_each_actual_mcp_peer(gateway):
    server = await create_server(gateway)
    async with (
        Client(server, client_info=Implementation(name="alpha", version="1")) as alpha,
        Client(server, client_info=Implementation(name="beta", version="1")) as beta,
    ):
        first, second = await asyncio.gather(
            result(alpha, "invoke_action", key=CENTRIFUGE, action="missing"),
            result(beta, "invoke_action", key=CENTRIFUGE, action="missing"),
        )
        assert first["actor"] == {"kind": "agent", "name": "alpha"}
        assert second["actor"] == {"kind": "agent", "name": "beta"}


async def test_generated_cancellation_sends_the_actual_peer_identity_to_http(gateway):
    actors = []

    async def observe(request):
        if request.url.path.endswith("/cancel"):
            actors.append(request.headers["X-Actor"])

    gateway.http.event_hooks["request"].append(observe)
    server = await create_server(gateway)
    async with (
        Client(server, client_info=Implementation(name="alpha", version="1")) as alpha,
        Client(server, client_info=Implementation(name="beta", version="1")) as beta,
    ):
        rejected = await gateway.invoke_action(CENTRIFUGE, "missing")
        await asyncio.gather(
            result(alpha, "cancel_invocation", invocationId=rejected["invocationId"]),
            result(beta, "cancel_invocation", invocationId=rejected["invocationId"]),
        )
        assert sorted(actors) == ["agent:alpha", "agent:beta"]

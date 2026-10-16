"""The installed lw process speaks the official legacy MCP stdio transport."""

from cli_support import LW, TOOLS, result
from mcp import Client, Implementation, StdioServerParameters

CENTRIFUGE = "/World/Lab/Centrifuge"


async def test_installed_lw_stdio_tools_preserve_peer_identity_and_rejection_hints(lab):
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=lab[2])
    async with Client(
        transport, client_info=Implementation(name="stdio-peer", version="1"), mode="legacy"
    ) as mcp:
        assert {tool.name for tool in (await mcp.list_tools()).tools} == TOOLS
        assert len(mcp.session.instructions) <= 1500
        state = await result(mcp, "get_state", keys=[CENTRIFUGE])
        assert state["entities"][CENTRIFUGE]["values"]["lidOpen"] is True
        rejected = await result(
            mcp, "invoke_action", key=CENTRIFUGE, action="start", params={"rpm": 500, "durationSeconds": 1}
        )
        assert rejected["status"] == "rejected" and rejected["hint"]
        assert rejected["actor"] == {"kind": "agent", "name": "stdio-peer"}
        paused = await result(mcp, "session_control", action="pause")
        assert paused["state"] == "PAUSED"

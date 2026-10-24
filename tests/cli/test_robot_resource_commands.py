"""Installed CLI and official MCP read the same real robot catalog over HTTP."""

import httpx
from cli_support import LW, result
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke


async def test_installed_robot_reads_match_official_mcp_and_actual_http_without_source(gateway_url):
    env = environment(gateway_url)
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=env)
    async with Client(transport, mode="legacy") as mcp:
        tools = {tool.name for tool in (await mcp.list_tools()).tools}
        assert {"robot_list", "robot_show", "robot_model_list", "robot_model_show",
                "controller_show"} <= tools
        robots = invoke(env, "robot", "ls")
        assert robots == await result(mcp, "robot_list")
        simulated = invoke(env, "robot", "show", "fr3-sim")
        assert simulated == await result(mcp, "robot_show", id="fr3-sim")
        assert simulated["bindings"][0]["program"] == "robot_arm.newton"
        models = invoke(env, "robot-model", "ls")
        assert models == await result(mcp, "robot_model_list")
        for model in models:
            assert invoke(env, "robot-model", "show", model["id"]) == await result(
                mcp, "robot_model_show", id=model["id"])
        controller = invoke(env, "controller", "show", "fr3-arm")
        assert controller == await result(mcp, "controller_show", id="fr3-arm")
        assert controller["current"] is None and controller["versions"] == []
        assert invoke(env, "robot", "show", "missing", code=1)["error"]["statusCode"] == 404
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert robots == http.get("/api/v1/embodiments").json()["embodiments"]
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
        assert http.get("/api/v1/evaluations").json()["evaluations"] == []

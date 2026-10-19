"""Installed lw and official stdio MCP discover and launch without an active session."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, TOOLS, result
from mcp import Client, Implementation, StdioServerParameters


async def test_scene_cli_and_mcp_bootstrap_inspection_launch_and_replacement(gateway_url):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}

    def cli(*arguments, code=0):
        invocation = subprocess.run(
            [str(LW), "scene", *arguments, "--json"], cwd=ROOT, env=environment,
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        assert invocation.returncode == code, (invocation.stdout, invocation.stderr)
        return json.loads(invocation.stdout)

    owned = []
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=10) as http:
        assert http.get("/api/v1/health").json()["sessions"] == []
        scenes = cli("ls")
        assert {scene["sceneVersion"] for scene in scenes} == {
            "lab-demo@1", "falling-boxes@1", "robot-and-tubes@1", "usd-lab@1",
        }
        robot = cli("show", "robot-and-tubes")
        assert robot["sceneVersion"] == "robot-and-tubes@1"
        assert "pickPlace" in robot["types"]["robot-arm/v1"]["actions"]
        assert robot["locations"]["centrifuge.slot1"]["grasp"]
        assert any(entity.get("key") == "/World/Lab/RobotA" for entity in robot["entities"])
        assert cli("launch", "unknown@999", code=1)["error"]["detail"]["code"] == "unknown_version"
        transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
        try:
            async with Client(transport, client_info=Implementation(name="library-agent", version="1"),
                              mode="legacy") as mcp:
                assert {tool.name for tool in (await mcp.list_tools()).tools} == TOOLS
                assert await result(mcp, "library_search") == scenes
                assert await result(mcp, "library_describe", id="robot-and-tubes") == robot
                launched = await result(mcp, "scene_launch", sceneVersion="lab-demo")
                owned.append(launched["sessionId"])
                assert launched["sceneId"] == "lab-demo" and launched["sceneVersion"] == "lab-demo@1"
                assert cli("launch", "lab-demo@1")["sessionId"] == launched["sessionId"]
                closed = await result(mcp, "invoke_action", key="/World/Lab/Centrifuge", action="closeLid")
                assert closed["status"] == "succeeded" and closed["actor"]["name"] == "library-agent"
                http.delete("/api/v1/sessions/" + launched["sessionId"]).raise_for_status()
                again = await result(mcp, "scene_launch", sceneVersion="lab-demo@1", source="synthetic")
                owned.append(again["sessionId"])
                assert again["sessionId"] != launched["sessionId"]
                state = await result(mcp, "get_state", keys=["/World/Lab/Centrifuge"])
                assert state["entities"]["/World/Lab/Centrifuge"]["values"]["lidOpen"] is True
        finally:
            for session_id in owned:
                http.delete("/api/v1/sessions/" + session_id)

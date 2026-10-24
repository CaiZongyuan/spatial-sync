"""Installed lw and official MCP share actual recorded-success Demonstration admission."""

import httpx
from cli_support import LW, result
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke

from tests.fixtures.demonstration_task import demonstration_task, successful_recording


async def test_installed_demo_commands_and_mcp_preserve_episode_and_version(gateway_url, tmp_path):
    env = environment(gateway_url)
    package = demonstration_task(tmp_path / "task", "demo-cli-probe")
    task = invoke(env, "task", "push", str(package))
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=30) as http:
        identifier = successful_recording(http)
        assert all(item["state"] == "STOPPED" for item in http.get("/api/v1/health").json()["sessions"])
    refused = invoke(env, "demo", "add", identifier, code=1)
    assert refused["error"]["detail"]["code"] == "demo_task_required"
    added = invoke(env, "demo", "add", identifier, "--task", task["id"])
    assert added["task"] == task["id"] and added["set"]["id"] == "demoset/demo-cli-probe@1"
    assert added["sourceProgram"]["id"] == "robot_arm.virtual"
    assert added["params"] == {"from": "rack.A1", "to": "centrifuge.slot1"}
    listed = invoke(env, "demo", "ls", "--task", task["id"])
    assert len(listed) == 1 and listed[0]["id"] == added["id"]
    async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=env), mode="legacy") as mcp:
        assert await result(mcp, "demo_list", task=task["id"]) == listed
        repeated = await result(mcp, "demo_add", recordingId=identifier, task=None)
        assert repeated["id"] == added["id"] and repeated["idempotent"]
        assert repeated["set"] == added["set"]
        assert {"demo_add", "demo_list"} <= {tool.name for tool in (await mcp.list_tools()).tools}

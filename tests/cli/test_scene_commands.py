"""Installed lw and official stdio MCP discover and launch without an active session."""

import json
import os
import subprocess

import httpx
import pytest
from cli_support import LW, ROOT, TOOLS, result
from lw.client import GatewayClient
from mcp import Client, Implementation, StdioServerParameters


@pytest.mark.parametrize("entrypoint", ["cli", "mcp"])
async def test_scene_launch_replays_custom_seed_through_cli_and_mcp_and_omission_uses_server_default(
    gateway_url, entrypoint
):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}
    owned = []
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=10) as http:
        try:
            transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
            async with Client(transport, mode="legacy") as mcp:
                for seed, expected in [(43, 43), (None, 42)]:
                    if entrypoint == "cli":
                        args = [str(LW), "scene", "launch", "lab-demo@2", "--json"]
                        if seed is not None:
                            args.extend(["--seed", str(seed)])
                        invocation = subprocess.run(
                            args,
                            cwd=ROOT,
                            env=environment,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            timeout=15,
                        )
                        assert invocation.returncode == 0, (invocation.stdout, invocation.stderr)
                        launched = json.loads(invocation.stdout)
                    else:
                        arguments = {"sceneVersion": "lab-demo@2"} | (
                            {"seed": seed} if seed is not None else {}
                        )
                        launched = await result(mcp, "scene_launch", **arguments)
                    session_id = launched["sessionId"]
                    owned.append(session_id)
                    route = "/api/v1/sessions/" + session_id
                    assert launched["seed"] == http.get(route).json()["seed"] == expected
                    http.delete(route).raise_for_status()
                    metadata = http.get(f"/recordings/{session_id}/recording.json").json()
                    assert metadata["seed"] == expected
                    assert metadata["protocolVersion"] == 2 and metadata["protocolMinorVersion"] == 1
                    assert metadata["formatVersion"] == 2
                    owned.remove(session_id)
                sessions_before_rejection = http.get("/api/v1/health").json()["sessions"]
                if entrypoint == "cli":
                    rejected = subprocess.run(
                        [str(LW), "scene", "launch", "lab-demo@2", "--seed", "-1", "--json"],
                        cwd=ROOT,
                        env=environment,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        timeout=15,
                    )
                    assert rejected.returncode == 1
                    assert json.loads(rejected.stdout)["error"]["statusCode"] == 422
                else:
                    rejected = await mcp.call_tool("scene_launch", {"sceneVersion": "lab-demo@2", "seed": -1})
                    assert rejected.is_error and "422" in rejected.content[0].text
                sessions_after_rejection = http.get("/api/v1/health").json()["sessions"]
                assert sessions_after_rejection == sessions_before_rejection
                assert all(row["state"] in {"STOPPED", "FAILED"} for row in sessions_after_rejection)
                seed_schema = next(
                    tool for tool in (await mcp.list_tools()).tools if tool.name == "scene_launch"
                )
                assert {
                    item.get("type") for item in seed_schema.input_schema["properties"]["seed"]["anyOf"]
                } == {
                    "integer",
                    "null",
                }
        finally:
            for session_id in owned:
                http.delete("/api/v1/sessions/" + session_id)


async def test_scene_launch_omitted_seed_preserves_legacy_http_request_shape():
    bodies = []

    def legacy(request):
        body = json.loads(request.content)
        assert set(body).issubset({"sceneVersion", "source"})
        bodies.append(body)
        return httpx.Response(201, json={"sessionId": "legacy-session"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(legacy), base_url="http://legacy") as http:
        async with GatewayClient(http=http) as client:
            await client.scene_launch("lab-demo@2")
            await client.scene_launch("lab-demo@2", source="synthetic", seed=None)
    assert bodies == [{"sceneVersion": "lab-demo@2"}, {"sceneVersion": "lab-demo@2", "source": "synthetic"}]


def test_scene_launch_help_and_noninteger_seed_do_not_allocate_sessions(gateway_url):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=10) as http:
        before = http.get("/api/v1/health").json()["sessions"]
        help_result = subprocess.run(
            [str(LW), "scene", "launch", "--help"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert help_result.returncode == 0
        assert "--seed" in help_result.stdout and "gateway default is 42" in help_result.stdout
        invalid = subprocess.run(
            [str(LW), "scene", "launch", "lab-demo@2", "--seed", "not-an-integer", "--json"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert invalid.returncode == 1
        message = json.loads(invalid.stdout)["error"]["message"]
        assert "invalid int value" in message and "not-an-integer" in message
        assert http.get("/api/v1/health").json()["sessions"] == before


async def test_scene_cli_and_mcp_bootstrap_inspection_launch_and_replacement(gateway_url):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}

    def cli(*arguments, code=0):
        invocation = subprocess.run(
            [str(LW), "scene", *arguments, "--json"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert invocation.returncode == code, (invocation.stdout, invocation.stderr)
        return json.loads(invocation.stdout)

    owned = []
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=10) as http:
        assert all(
            row["state"] in {"STOPPED", "FAILED"} for row in http.get("/api/v1/health").json()["sessions"]
        )
        scenes = cli("ls")
        assert {scene["sceneVersion"] for scene in scenes} == {
            "lab-demo@1",
            "lab-demo@2",
            "lab-demo@3",
            "falling-boxes@1",
            "robot-and-tubes@1",
            "robot-and-tubes@2",
            "usd-lab@1",
        }
        robot = cli("show", "robot-and-tubes")
        assert robot["sceneVersion"] == "robot-and-tubes@2"
        assert all(entity.get("template") for entity in robot["entities"])
        assert robot["templateResolution"]["status"] == "resolved"
        assert len(robot["effectiveBindings"]["newton"]) == 22
        assert "pickPlace" in robot["types"]["robot-arm/v1"]["actions"]
        assert robot["locations"]["centrifuge.slot1"]["grasp"]
        assert any(entity.get("key") == "/World/Lab/RobotA" for entity in robot["entities"])
        assert cli("launch", "unknown@999", code=1)["error"]["detail"]["code"] == "unknown_version"
        transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
        try:
            async with Client(
                transport, client_info=Implementation(name="library-agent", version="1"), mode="legacy"
            ) as mcp:
                assert {tool.name for tool in (await mcp.list_tools()).tools} == TOOLS
                assert await result(mcp, "library_search") == scenes
                assert await result(mcp, "library_describe", id="robot-and-tubes") == robot
                launched = await result(mcp, "scene_launch", sceneVersion="lab-demo")
                owned.append(launched["sessionId"])
                assert launched["sceneId"] == "lab-demo" and launched["sceneVersion"] == "lab-demo@3"
                assert cli("launch", "lab-demo@3")["sessionId"] == launched["sessionId"]
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

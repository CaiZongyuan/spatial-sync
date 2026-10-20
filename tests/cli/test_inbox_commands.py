"""Installed lw and official stdio MCP share the real global inbox and cursor."""

import json
import os
import subprocess
import time

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters

KEY = "/World/Lab/BiosafetyCabinet"


async def test_global_inbox_cli_mcp_and_connection_instructions(gateway_url):
    environment = {key: value for key, value in os.environ.items() if key != "LW_SESSION"}
    environment |= {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}

    def cli(*arguments):
        response = subprocess.run([str(LW), "inbox", *arguments, "--json"], cwd=ROOT, env=environment,
                                  capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert response.returncode == 0, (response.stdout, response.stderr)
        return json.loads(response.stdout)

    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway_url, trust_env=False, timeout=10) as http:
        empty = http.get("/api/v1/inbox").json()
        assert empty["alerts"] == empty["approvals"] == empty["messages"] == []
        assert cli() == empty
        async with Client(transport, mode="legacy") as mcp:
            assert "Active alerts: 0." in mcp.session.instructions
            assert await result(mcp, "inbox_poll") == empty
        created = http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2", "controlHz": 20})
        created.raise_for_status()
        sid = created.json()["sessionId"]
        route = "/api/v1/sessions/" + sid
        try:
            for name, args in [("pause", {}), ("reset", {})]:
                controlled = http.post(route + "/commands", json={"requestId": name, "command": name,
                                                                  "args": args})
                assert controlled.json()["ack"]["status"] == "applied"
            accepted = http.post(route + "/actions", json={"requestId": "inbox-uv", "entityKey": KEY,
                                                           "action": "setUv", "params": {"on": True}})
            assert accepted.status_code == 202
            http.post(route + "/commands", json={"requestId": "step", "command": "step",
                                                  "args": {"count": 120}})
            terminal = http.get(route + "/invocations/" + accepted.json()["invocationId"],
                                params={"wait": True, "timeoutSeconds": 2})
            assert terminal.json()["status"] == "succeeded"
            rejected = http.post(route + "/actions", json={"requestId": "inbox-bad", "entityKey": KEY,
                                                           "action": "missing"})
            assert rejected.status_code == 404
            pending = http.post("/api/v1/plans", json={"title": "Inspect UV", "target": "session", "steps": [
                {"id": 1, "do": {"entity": KEY, "action": "setUv", "params": {"on": True}}}]})
            assert pending.status_code == 201 and pending.json()["status"] == "submitted"
            deadline = time.monotonic() + 2
            while True:
                public = http.get("/api/v1/inbox").json()
                if len(public["alerts"]) == 1:
                    break
                assert time.monotonic() < deadline
                time.sleep(.005)
            assert public["approvals"][0]["approvalId"] == pending.json()["approvalId"]
            assert cli() == public
            async with Client(transport, mode="legacy") as mcp:
                assert "Active alerts: 1." in mcp.session.instructions
                assert len(mcp.session.instructions) <= 1500
                tools = (await mcp.list_tools()).tools
                assert "inbox_poll" in {tool.name for tool in tools}
                assert not any("approv" in tool.name for tool in tools)
                assert await result(mcp, "inbox_poll") == public
                params = {"after": empty["cursor"], "kinds": "alert,approval"}
                filtered = http.get("/api/v1/inbox", params=params).json()
                assert cli("--since", empty["cursor"], "--kinds", params["kinds"]) == filtered
                assert await result(mcp, "inbox_poll", **params) == filtered
                continued = await result(mcp, "inbox_poll", after=filtered["cursor"])
                assert continued["messages"] == [] and continued["cursor"] == filtered["cursor"]
        finally:
            http.delete(route)

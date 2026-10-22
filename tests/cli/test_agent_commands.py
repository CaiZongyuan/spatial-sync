"""Installed lw and official MCP stdio operate on Agents with zero active Sessions."""

import json
import os
import subprocess

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters


async def test_agent_snapshot_commands_share_http_and_mcp_without_active_session(gateway_url, tmp_path):
    environment = os.environ | {"LW_URL": gateway_url, "PYTHONIOENCODING": "utf-8"}
    environment.pop("LW_SESSION", None)

    def lw(*arguments, code=0):
        completed = subprocess.run([str(LW), *arguments, "--json"], env=environment, cwd=ROOT,
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert completed.returncode == code, (completed.stdout, completed.stderr)
        return json.loads(completed.stdout)

    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert http.get("/api/v1/health").json()["sessions"] == []
        async with Client(transport, mode="legacy") as mcp:
            expected = http.get("/api/v1/agents").json()["agents"]
            assert lw("agent", "ls") == await result(mcp, "agent_list") == expected
            agent = http.get("/api/v1/agents/lab-operator").json()
            assert lw("agent", "show", "lab-operator") == await result(
                mcp, "agent_show", id="lab-operator"
            ) == agent
            snapshot = lw("agent", "snapshot", "lab-operator")
            assert snapshot == await result(mcp, "agent_snapshot", id="lab-operator")
            assert lw("agent", "versions", "lab-operator") == await result(
                mcp, "agent_versions", id="lab-operator"
            ) == [snapshot]
            first, second = str(tmp_path / "cli.tar"), str(tmp_path / "mcp.tar")
            assert lw("agent", "export", "lab-operator", "1", "--output", first)["output"] == first
            exported = await result(mcp, "agent_export", id="lab-operator", version=1, output=second)
            assert exported["output"] == second
            assert (tmp_path / "cli.tar").read_bytes() == (tmp_path / "mcp.tar").read_bytes()
            created = lw("agent", "create", "cli-fork", first)
            assert created["model"] is None and created["referenceVersion"] == 1
            assert (await result(mcp, "agent_create", id="mcp-fork", archive=second))["model"] is None
            assert lw("agent", "import", "cli-fork", first) == await result(
                mcp, "agent_import", id="mcp-fork", archive=second
            )
            assert lw("harness", "diff", "cli-fork", "1", "1")["changes"] == []
            assert (await result(mcp, "harness_diff", id="mcp-fork", before=1, after=1))["changes"] == []
            tools = {tool.name for tool in (await mcp.list_tools()).tools}
            assert not any("vault" in name or "approv" in name for name in tools)
            assert lw("agent", "show", "unknown-agent", code=1)["error"]["statusCode"] == 404
        assert http.get("/api/v1/health").json()["sessions"] == []

"""The public lw entrypoint and official MCP stdio work without an active Session."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil
import pytest
from cli_support import LW, ROOT, TOOLS, result, stop
from mcp import Client, StdioServerParameters


@pytest.fixture
def agent_gateway_url(tmp_path, record_property):
    directory = tmp_path / "agent-gateway"
    directory.mkdir()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    command = [sys.executable, "-m", "uvicorn", "agent_gateway:app", "--app-dir", "tests/fixtures",
               "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    with (directory / "gateway.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, env=os.environ | {
            "SPATIAL_RECORDINGS_ROOT": str(directory / "recordings"),
            "SPATIAL_STATE_ROOT": str(directory / "state"), "LW_LIBRARY": str(directory / "library"),
        })
        identity = {"pid": process.pid, "created": psutil.Process(process.pid).create_time(),
                    "command": command}
        record_property("gatewayProcess", json.dumps(identity))
        try:
            url = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=url, trust_env=False, timeout=1) as http:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    assert process.poll() is None, (directory / "gateway.log").read_text()
                    try:
                        if http.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.02)
                else:
                    raise AssertionError("Source-free Agent gateway did not start")
            yield url
        finally:
            stop(process)
            assert process.poll() is not None
            assert not any(row.pid == process.pid for row in psutil.net_connections(kind="inet"))
            record_property("gatewayCleanup", "Owned API exited; owned connections/listeners empty")


async def test_agent_snapshot_commands_share_http_and_mcp_without_active_session(
    agent_gateway_url, tmp_path, record_property
):
    state = tmp_path / "agent-gateway/state"
    environment = os.environ | {"LW_URL": agent_gateway_url, "PYTHONIOENCODING": "utf-8",
                                "SPATIAL_STATE_ROOT": str(state)}
    environment.pop("LW_SESSION", None)

    def lw(*arguments, code=0):
        completed = subprocess.run([str(LW), *arguments, "--json"], env=environment, cwd=ROOT,
                                    capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert completed.returncode == code, (completed.stdout, completed.stderr)
        return json.loads(completed.stdout)

    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=environment)
    probe = subprocess.run([sys.executable, "-c", "import lw, json; print(json.dumps({'lw': lw.__file__}))"],
                           env=environment, cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
    origin = json.loads(probe.stdout)
    assert Path(origin["lw"]).resolve() == ROOT / "apps/cli/lw/__init__.py"
    record_property("sourceCliOrigin", json.dumps(origin))
    before = {child.pid for child in psutil.Process().children(recursive=True)}
    with httpx.Client(base_url=agent_gateway_url, trust_env=False) as http:
        gateway_origin = http.get("/e2e/agent-origins").json()
        assert Path(gateway_origin["agentLibrary"]) == ROOT / "packages/library/agents.py"
        assert gateway_origin["sourceStarts"] == 0
        assert gateway_origin["resourcePaths"] == {
            "vault": str(state / "vault"), "models": str(state / "models.yaml"),
            "prices": str(state / "prices.yaml"),
        }
        record_property("gatewayOrigin", json.dumps(gateway_origin))
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
            assert tools == TOOLS
            assert not any("vault" in name or "approv" in name for name in tools)
            models = http.get("/api/v1/models").json()["models"]
            assert lw("model", "ls") == await result(mcp, "model_ls") == models == []
            model = lw("model", "add", "fixture.local/org/model:stable@1", "--thinking", "high")
            assert lw("model", "default", model["ref"]) == {"defaultRef": model["ref"]}
            expected_models = http.get("/api/v1/models?q=fixture.local").json()["models"]
            assert lw("model", "ls", "--query", "fixture.local") == await result(
                mcp, "model_ls", query="fixture.local"
            ) == expected_models
            assert lw("agent", "show", "lab-operator")["model"] is None
            assert lw("vault", "ls") == []
            assert http.get("/api/v1/prices").json()["models"] == []
            assert lw("agent", "show", "unknown-agent", code=1)["error"]["statusCode"] == 404
        assert http.get("/api/v1/health").json()["sessions"] == []
        assert http.get("/e2e/agent-origins").json()["sourceStarts"] == 0
    assert {child.pid for child in psutil.Process().children(recursive=True)} <= before
    record_property("mcpCleanup", "Official MCP stdio child exited; Sessions empty; Source starts zero")

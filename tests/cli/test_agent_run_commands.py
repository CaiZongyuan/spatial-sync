"""Installed client and generic MCP bind Agent runs to an exact approved Plan."""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from cli_support import LW, ROOT, result, stop
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke


@pytest.fixture
def agent_gateway(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (tmp_path / "gateway.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agent_clients_gateway:app",
                "--app-dir",
                "tests/fixtures",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            stdout=log,
            stderr=log,
            env=os.environ
            | {
                "LW_LIBRARY": str(tmp_path / "library"),
                "SPATIAL_STATE_ROOT": str(tmp_path / "state"),
                "SPATIAL_RECORDINGS_ROOT": str(tmp_path / "recordings"),
                "SPATIAL_NODE_TRAINER": "[]",
            },
        )
        url = f"http://127.0.0.1:{port}"
        try:
            with httpx.Client(base_url=url, trust_env=False, timeout=2) as http:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    assert process.poll() is None, (tmp_path / "gateway.log").read_text()
                    try:
                        if http.get("/api/v1/health").is_success:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.02)
                else:
                    raise AssertionError("Owned Agent client fixture did not start")
            yield url
        finally:
            stop(process)


async def test_agent_cli_and_mcp_require_exact_human_plan_and_reuse_original_request(agent_gateway):
    env = environment(agent_gateway)
    with httpx.Client(base_url=agent_gateway, trust_env=False, timeout=10) as http:
        plan = http.post("/e2e/agent-plan").json()
        missing = subprocess.run(
            [str(LW), "agent", "run", "lab-operator", "--suite", plan["suite"], "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert missing.returncode == 1  # The installed JSON CLI uses its shared error envelope.
        assert "--plan" in missing.stdout + missing.stderr
        args = (
            "agent",
            "run",
            "lab-operator",
            "--suite",
            plan["suite"],
            "--plan",
            plan["id"],
            "--plan-revision",
            "1",
            "--request-id",
            "installed-cli-intent",
        )
        refused = invoke(env, *args, code=1)
        assert refused["error"]["detail"]["code"] == "approved_agent_plan_required"
        assert http.get("/api/v1/jobs?kind=agent-run").json()["jobs"] == []
        assert (
            http.post(
                f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        admitted = invoke(env, *args)
        assert admitted["kind"] == "agent-run" and admitted["planRevision"] == 1
        repeated = invoke(env, *args)
        assert repeated["jobId"] == admitted["jobId"]
        assert repeated["model"] == "anthropic/fixture-model"
        another = http.post("/e2e/agent-plan").json()
        assert (
            http.post(
                f"/api/v1/approvals/{another['approvalId']}/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
    async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=env), mode="legacy") as mcp:
        arguments = {
            "id": "lab-operator",
            "suite": another["suite"],
            "planId": another["id"],
            "planRevision": 1,
            "requestId": "official-mcp-intent",
            "version": None,
        }
        run = await result(mcp, "agent_run", **arguments)
        assert run["kind"] == "agent-run" and run["planId"] == another["id"]
        assert (await result(mcp, "agent_run", **arguments))["jobId"] == run["jobId"]
        tools = (await mcp.list_tools()).tools
        assert "agent_run" in {tool.name for tool in tools}
        schema = next(tool.input_schema for tool in tools if tool.name == "agent_run")
        assert {"id", "suite", "planId", "planRevision"} <= set(schema["required"])
        assert (
            not {"model", "thinking", "credentialRef", "executionProfile", "budget"}
            & schema["properties"].keys()
        )

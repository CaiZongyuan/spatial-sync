"""Installed CLI and official MCP expose jobs through the same session-free HTTP resources."""

import json
import os
import subprocess

import httpx
import yaml
from cli_support import LW, ROOT, result
from mcp import Client, Implementation, StdioServerParameters

from tests.fixtures.training_task import task_directory, training_plan


async def test_installed_cli_and_official_mcp_share_the_job_pipeline(gateway_url, tmp_path):
    env = os.environ | {"LW_URL": gateway_url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8"}

    def invoke(*arguments, code=0):
        process = subprocess.run(
            [str(LW), *arguments, "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        assert process.returncode == code, process.stdout + process.stderr
        return json.loads(process.stdout)

    task = invoke("task", "push", str(task_directory(tmp_path / "task", "job-command-probe")))
    interview = tmp_path / "interview.md"
    interview.write_text("Question: one declared seed? Recommendation: 42. Answer: yes.", encoding="utf-8")
    source = tmp_path / "plan.yaml"
    source.write_text(yaml.safe_dump(training_plan(task["id"], interview=interview.name)), encoding="utf-8")
    plan = invoke("plan", "submit", str(source))
    refused = invoke("job", "start", plan["id"], "--seed", "42", code=1)
    assert refused["error"]["detail"]["code"] == "plan_not_approved"
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert (
            http.post(
                f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"}
            ).status_code
            == 200
        )
        first = invoke("job", "start", plan["id"], "--seed", "42", "--request-id", "cli-stable-job")
        assert first["status"] == "queued" and first["reason"] == "estimate_unavailable"
        assert first["requestedBy"] == {"kind": "cli", "name": "lw"}
        assert (
            invoke("job", "start", plan["id"], "--seed", "42", "--request-id", "cli-stable-job")["jobId"]
            == first["jobId"]
        )
        assert invoke("job", "show", first["jobId"])["planRevision"] == plan["revision"]
        assert first["jobId"] in [job["jobId"] for job in invoke("job", "ls", "--plan", plan["id"])]
        assert invoke("job", "logs", first["jobId"])["lines"] == []
        assert invoke("job", "cancel", first["jobId"], code=3)["status"] == "cancelled"
        assert invoke("job", "logs", first["jobId"], "--follow")["complete"]
        assert invoke("node", "ls") == []
        async with Client(
            StdioServerParameters(command=str(LW), args=["mcp"], env=env),
            mode="legacy",
            client_info=Implementation(name="job-agent", version="1"),
        ) as mcp:
            job = await result(mcp, "job_start", planId=plan["id"], seed=43, requestId="mcp-stable-job")
            assert job["requestedBy"] == {"kind": "agent", "name": "job-agent"}
            assert (await result(mcp, "job_show", id=job["jobId"]))["task"] == task["id"]
            assert job["jobId"] in [item["jobId"] for item in await result(mcp, "job_list", plan=plan["id"])]
            assert (await result(mcp, "job_logs", id=job["jobId"]))["cursor"] == 0
            assert (await result(mcp, "job_cancel", id=job["jobId"]))["status"] == "cancelled"
            assert await result(mcp, "node_ls") == []
            assert not any("approv" in tool.name for tool in (await mcp.list_tools()).tools)
        messages = http.get("/api/v1/inbox?kinds=job").json()["messages"]
        assert {row["data"]["jobId"] for row in messages} >= {first["jobId"], job["jobId"]}
        assert http.get("/api/v1/health").json()["sessionCount"] == 0

"""Installed CLI and official MCP transport share Task and training Plan HTTP."""

import json
import os
import subprocess
from pathlib import Path

import httpx
import yaml
from cli_support import LW, ROOT, result
from mcp import Client, Implementation, StdioServerParameters

from tests.fixtures.training_task import task_directory, training_plan


def environment(gateway_url):
    return os.environ | {"LW_URL": gateway_url, "LW_SESSION": "", "PYTHONIOENCODING": "utf-8"}


def invoke(env, *args, code=0):
    called = subprocess.run([str(LW), *args, "--json"], cwd=ROOT, env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=15)
    assert called.returncode == code, called.stdout + called.stderr
    return json.loads(called.stdout)


async def test_tensorboard_cli_and_mcp_reach_the_public_runtime_check_without_a_session(gateway_url):
    env = environment(gateway_url)
    error = invoke(env, "tensorboard", code=1)["error"]
    assert error["statusCode"] == 409 and error["detail"]["code"] == "tensorboard_runtime_unavailable"
    async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=env), mode="legacy") as mcp:
        response = await mcp.call_tool("tensorboard", {})
        assert response.is_error and "tensorboard_runtime_unavailable" in str(response.content)
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert http.get("/api/v1/health").json()["sessionCount"] == 0


def test_installed_task_push_submit_relative_interview_and_revise_without_session(gateway_url, tmp_path):
    env = environment(gateway_url)
    package = task_directory(tmp_path / "Task source", "cli-task-probe")
    task = invoke(env, "task", "push", str(package))
    assert invoke(env, "task", "push", str(package)) == task | {"idempotent": True}
    assert invoke(env, "task", "show", task["id"])["contentHash"] == task["contentHash"]
    assert task["id"] in [item["id"] for item in invoke(env, "task", "ls")]
    interview = tmp_path / "interview.md"
    interview.write_text("Question: target?\nRecommendation: >= 0.80\nAnswer: yes\n", encoding="utf-8")
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(training_plan(task["id"], interview=interview.name)), encoding="utf-8")
    plan = invoke(env, "plan", "submit", str(path))
    assert plan["status"] == "submitted" and plan["interview"] == interview.read_text(encoding="utf-8")
    assert plan["requestedBy"] == {"kind": "cli", "name": "lw"}
    revised = invoke(env, "plan", "revise", plan["id"], str(path))
    assert revised["revision"] == 2 and revised["approvalId"] != plan["approvalId"]
    assert invoke(env, "plan", "show", plan["id"])["revision"] == 2
    assert plan["id"] in [item["id"] for item in invoke(env, "plan", "ls")]
    error = invoke(env, "approve", revised["approvalId"], code=1)["error"]
    assert error["detail"]["code"] == "approval_requires_human"
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert http.get("/api/v1/health").json()["sessionCount"] == 0


async def test_official_mcp_training_plan_uses_structured_content_and_never_approves(gateway_url, tmp_path):
    env = environment(gateway_url)
    package = task_directory(tmp_path / "task", "mcp-task-probe")
    async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=env), mode="legacy",
                      client_info=Implementation(name="training-agent", version="1")) as mcp:
        task = await result(mcp, "task_push", directory=str(package))
        assert (await result(mcp, "task_show", id=task["id"]))["contentHash"] == task["contentHash"]
        assert task["id"] in [item["id"] for item in await result(mcp, "task_list")]
        document = training_plan(task["id"])
        plan = await result(mcp, "plan_submit", document=document)
        assert plan["requestedBy"] == {"kind": "agent", "name": "training-agent"}
        assert plan["interview"] == document["interview"]
        assert (await result(mcp, "plan_show", id=plan["id"]))["status"] == "submitted"
        names = {tool.name for tool in (await mcp.list_tools()).tools}
        assert not any("approv" in name for name in names)
        wrong = await mcp.call_tool("plan_submit", {"document": str(Path("not-a-server-file.yaml"))})
        assert wrong.is_error

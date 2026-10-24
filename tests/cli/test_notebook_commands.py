"""Installed CLI and official MCP read Goal resources without creating a Session."""

import json

import httpx
import yaml
from cli_support import LW, result
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke

from tests.fixtures.training_task import task_directory, training_plan


async def test_goal_notebook_loop_and_plan_link_flags_use_the_real_shared_http(gateway_url, tmp_path):
    env = environment(gateway_url)
    package = task_directory(tmp_path / "task", "notebook-cli-probe")
    task = invoke(env, "task", "push", str(package))
    interview = tmp_path / "interview.md"
    interview.write_text("Owned CLI fixture, no execution approval.\n")
    plan_file = tmp_path / "plan.yaml"
    plan_file.write_text(yaml.safe_dump(training_plan(task["id"], interview=interview.name)))
    plan = invoke(env, "plan", "submit", str(plan_file))
    assert plan["goalId"] == "goal/notebook-cli-probe"
    notebook = invoke(env, "notebook", "show", plan["goalId"])
    assert notebook == {"goalId": plan["goalId"], "entries": []}
    assert invoke(env, "notebook", "show", task["id"], "--refuted") == notebook
    loop = invoke(env, "loop", "show", "notebook-cli-probe")
    assert loop["goalId"] == plan["goalId"] and loop["rounds"] == []
    assert loop["budget"]["limit"] is None  # No human-approved Goal budget yet.
    refused = invoke(
        env,
        "eval",
        "run",
        "robot_arm.virtual",
        "--suite",
        "suite/lab-demo-pick-place@1",
        "--plan",
        plan["id"],
        "--revision",
        "1",
        code=1,
    )
    assert refused["error"]["statusCode"] == 409
    assert refused["error"]["detail"]["code"] == "plan_not_approved"
    invalid = invoke(env, "loop", "show", "A natural-language desired result", code=1)
    assert "Select goal/name" in invalid["error"]["message"]
    async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=env), mode="legacy") as mcp:
        assert await result(mcp, "notebook_show", goal=plan["goalId"], refuted=False) == notebook
        assert (await result(mcp, "loop_show", goal=plan["goalId"]))["goalId"] == plan["goalId"]
        names = {tool.name for tool in (await mcp.list_tools()).tools}
        assert {"notebook_show", "loop_show"} <= names
        assert not any("override" in name for name in names)
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
        assert "notebookContext" not in json.dumps(notebook)

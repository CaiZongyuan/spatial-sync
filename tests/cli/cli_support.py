"""Owned process cleanup and official SDK result handling used by CLI tests."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LW = Path(sys.executable).parent / ("lw.exe" if os.name == "nt" else "lw")
TOOLS = {
    "robot_list", "robot_show", "robot_model_list", "robot_model_show", "controller_show",
    "policy_create", "policy_list", "policy_show",
    "agent_run_create", "agent_run_list", "agent_run_show", "agent_run_next",
    "agent_run_complete", "agent_run_cancel",
    "runtime_import", "runtime_list", "runtime_show",
    "demo_add", "demo_list",
    "notebook_show", "loop_show",
    "tensorboard",
    "job_start", "job_list", "job_show", "job_cancel", "job_logs", "node_ls",
    "task_push", "task_list", "task_show", "plan_submit",
    "model_ls", "cost_query",
    "agent_list", "agent_show", "agent_versions", "agent_snapshot", "agent_export", "agent_import",
    "agent_create", "harness_diff",
    "suite_list", "suite_show", "evaluation_run", "evaluation_show", "evaluation_list",
    "evaluation_cancel", "score_list", "session_request_show", "session_request_cancel",
    "inbox_poll",
    "history_query",
    "catalog_search",
    "catalog_show",
    "catalog_estimate",
    "acquisition_start",
    "acquisition_list",
    "acquisition_show",
    "acquisition_cancel",
    "acquisition_retry",
    "library_search",
    "library_describe",
    "library_import",
    "library_validate",
    "library_remove",
    "scene_launch",
    "alerts_list",
    "alert_ack",
    "list_entities",
    "describe_entity",
    "get_state",
    "invoke_action",
    "get_invocation",
    "cancel_invocation",
    "wait_until",
    "session_control",
    "plan_create",
    "plan_revise",
    "plan_show",
    "plan_list",
    "check_condition",
}


def stop(process):
    try:
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=5
                )
            else:
                process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


async def result(client, name, **arguments):
    response = await asyncio.wait_for(client.call_tool(name, arguments), 5)
    assert not response.is_error, response.content
    document = response.structured_content
    return document["result"] if set(document) == {"result"} else document

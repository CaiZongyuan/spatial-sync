"""Owned process cleanup and official SDK result handling used by CLI tests."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LW = Path(sys.executable).parent / ("lw.exe" if os.name == "nt" else "lw")
TOOLS = {
    "suite_push", "agent_rsi", "agent_harness", "agent_baseline", "agent_candidate", "agent_tool_trace",
    "agent_round_start", "agent_round_settle", "agent_contamination", "agent_promotion_request",
    "robot_list", "robot_show", "robot_model_list", "robot_model_show", "controller_show",
    "policy_create", "policy_list", "policy_show",
    "policy_promote", "program_promote", "controller_promotion_show",
    "policy_check_observations", "policy_observation_checks", "policy_observation_check_show",
    "policy_observation_check_verify_source",
    "agent_run_create", "agent_run_list", "agent_run_show", "agent_run_next",
    "agent_run_complete", "agent_run_cancel",
    "runtime_import", "runtime_list", "runtime_show",
    "demo_add", "demo_list",
    "notebook_show", "loop_show",
    "tensorboard",
    "job_start", "job_list", "job_show", "job_cancel", "job_logs", "node_ls",
    "task_push", "task_list", "task_show", "plan_submit",
    "program_inspect", "program_push", "program_list", "program_show", "program_source", "program_export",
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
    "catalog_sync", "catalog_sync_status", "catalog_sync_cancel", "catalog_sync_retry",
    "catalog_collections", "catalog_collection_put", "catalog_collection_set", "catalog_discover",
    "catalog_maintain", "catalog_maintenance_status", "catalog_maintenance_cancel",
    "catalog_maintenance_resume", "catalog_maintenance_retry", "catalog_batch_claim", "catalog_batch_submit",
    "catalog_batch_error", "catalog_evidence", "catalog_correction_put", "catalog_correction_retract",
    "catalog_organization_select", "catalog_metadata_publish", "catalog_metadata_export", "catalog_metadata_restore",
    "acquisition_start",
    "acquisition_list",
    "acquisition_show",
    "acquisition_cancel",
    "acquisition_retry",
    "authoring_submit",
    "authoring_list",
    "authoring_show",
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

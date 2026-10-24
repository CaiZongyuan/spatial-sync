"""Installed CLI and official scoped MCP use actual Synthetic train and private handoff."""

import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import time

import httpx
import pytest
import yaml
from cli_support import LW, ROOT, result, stop
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke

from tests.gateway.test_agent_promotions import DECLARATION, MARKER, action, private_suite_archive
from tests.gateway.test_agent_rsi import agent_plan, complete_scope
from tests.gateway.test_external_agent_evaluation import BASE, work


@pytest.fixture
def rsi_gateway(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with (tmp_path / "rsi-gateway.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agent_rsi_gateway:app",
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
                "SPATIAL_RECORDINGS_ROOT": str(tmp_path / "recordings"),
                "SPATIAL_STATE_ROOT": str(tmp_path / "state"),
                "LW_LIBRARY": str(tmp_path / "library"),
            },
        )
        try:
            url = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=url, trust_env=False, timeout=1) as http:
                for _ in range(300):
                    assert process.poll() is None
                    try:
                        if http.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.02)
                else:
                    raise AssertionError("Owned RSI gateway did not start")
            yield url
        finally:
            stop(process)


async def test_installed_optimizer_and_private_controller_profiles_keep_actual_authority(
    rsi_gateway, tmp_path
):
    env = environment(rsi_gateway) | {"LW_TOKEN": "owned-rsi-installed"}
    headers = {"Authorization": "Bearer " + env["LW_TOKEN"]}
    suite_dir = tmp_path / "selected private Suite"
    suite_dir.mkdir()
    with tarfile.open(fileobj=io.BytesIO(private_suite_archive())) as tar:
        tar.extractall(suite_dir, filter="data")
    suite = invoke(env, "suite", "push", str(suite_dir))
    assert MARKER not in str(suite)
    run = invoke(
        env,
        "agent-run",
        "create",
        "lab-operator",
        "--suite",
        suite["id"],
        "--harness-version",
        "1",
        "--runtime",
        DECLARATION["name"],
        "--runtime-version",
        DECLARATION["version"],
        "--model",
        DECLARATION["model"],
        "--thinking",
        DECLARATION["thinking"],
        "--wall-seconds",
        "120",
        "--request-id",
        "installed-baseline",
    )
    with httpx.Client(base_url=rsi_gateway, headers=headers, trust_env=False, timeout=40) as http:
        complete_scope(http, run)
        baseline = invoke(
            env,
            "agent",
            "baseline",
            "lab-operator",
            "--evaluation",
            run["id"],
            "--request-id",
            "installed-formal",
        )
        grant_file = tmp_path / "optimizer.yaml"
        grant_file.write_text(
            yaml.safe_dump(
                {
                    "referenceEvaluationId": run["id"],
                    "goalId": "goal/rsi-fixture",
                    "nativeSessionId": "installed-fresh-optimizer",
                    "requestId": "installed-optimizer",
                    "wallSeconds": 180,
                }
            )
        )
        issued = invoke(env, "agent", "optimizer-grant", "lab-operator", str(grant_file))
        native = [
            json.loads(line)
            for line in (ROOT / "tests/fixtures/runtime/claude-code-stream.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        for event in native:
            event["session_id"] = "installed-fresh-optimizer"
        capture_body = {"format": "claude-code-stream-json", "records": native}
        http.post("/api/v1/runtime-sessions/import", json=capture_body).raise_for_status()
        # Declared A labels cannot remove the Goal owned by the issued Optimizer conversation.
        http.post(
            "/api/v1/runtime-sessions/import", json=capture_body | {"associations": {}}
        ).raise_for_status()
        total = http.get("/api/v1/cost/summary", params={"range": "all", "goal": "goal/rsi-fixture"}).json()
        assert total["totals"]["llmUsd"] == 0.3244434
        optimizer_env = env | {"LW_TOKEN": issued["token"]}
        transport = StdioServerParameters(command=str(LW), args=["mcp"], env=optimizer_env)
        promotion_path = None
        try:
            async with Client(transport, mode="legacy") as optimizer:
                names = {tool.name for tool in (await optimizer.list_tools()).tools}
                assert {
                    "agent_harness",
                    "agent_candidate",
                    "plan_submit",
                    "agent_round_start",
                    "agent_round_settle",
                    "agent_promotion_request",
                } <= names
                assert (
                    not {
                        "runtime_import",
                        "runtime_show",
                        "library_import",
                        "agent_export",
                        "invoke_action",
                        "inbox_poll",
                        "agent_run_next",
                        "agent_promotion_next",
                        "agent_promotion_review",
                    }
                    & names
                )
                original = await result(optimizer, "agent_harness", id="lab-operator", version=1)
                files = original["files"]
                next(file for file in files if file["path"] == "AGENTS.md")["content"] += (
                    "\n\nCheck the final platform observation before reporting completion.\n"
                )
                candidate = await result(
                    optimizer,
                    "agent_candidate",
                    id="lab-operator",
                    document={
                        "referenceVersion": 1,
                        "requestId": "installed-candidate",
                        "files": files,
                        "change": {
                            "kind": "instructions",
                            "path": "AGENTS.md",
                            "description": "One paragraph",
                        },
                    },
                )
                assert candidate["staged"] and not candidate["applied"]
                payload = agent_plan(baseline, candidate) | {"runtime": DECLARATION}
                payload["budget"]["cases"] = 9
                payload["budget"]["wallSeconds"] = 120
                plan = await result(optimizer, "plan_submit", document=payload)
                assert plan["requestedBy"]["name"].startswith("optimizer-")
                attributed = http.post(
                    "/api/v1/runtime-sessions/import",
                    json=capture_body
                    | {
                        "associations": {
                            "agentId": plan["agentId"],
                            "goalId": plan["goalId"],
                            "planId": plan["id"],
                            "planRevision": 1,
                            "harnessVersion": 1,
                        }
                    },
                )
                attributed.raise_for_status()
                optimizer_record = attributed.json()["session"]["id"]
                denied = await optimizer.call_tool(
                    "agent_round_start", {"id": plan["id"], "revision": 1, "requestId": "installed-round"}
                )
                assert denied.is_error and "plan_not_approved" in str(denied.content)
                http.post(
                    "/api/v1/approvals/" + plan["approvalId"] + "/decision", json={"decision": "approve"}
                ).raise_for_status()
                candidate_run = await result(
                    optimizer, "agent_round_start", id=plan["id"], revision=1, requestId="installed-round"
                )
                for repeat in range(3):
                    item = work(http, candidate_run)
                    if repeat == 0:
                        reused = http.post(
                            BASE + candidate_run["id"] + "/complete",
                            json={
                                "caseRunId": item["caseRunId"],
                                "nativeSessionId": "installed-fresh-optimizer",
                            },
                        )
                        assert (
                            reused.status_code == 409
                            and reused.json()["detail"]["code"] == "native_session_reused"
                        )
                    assert action(http, item, "/World/Lab/RobotA", "home").json()["status"] == "succeeded"
                    assert (
                        action(http, item, "/World/Lab/Centrifuge", "closeLid").json()["status"]
                        == "succeeded"
                    )
                    http.post(
                        BASE + candidate_run["id"] + "/complete",
                        json={
                            "caseRunId": item["caseRunId"],
                            "nativeSessionId": "installed-" + item["caseRunId"],
                        },
                    ).raise_for_status()
                for _ in range(100):
                    if http.get(BASE + candidate_run["id"]).json()["status"] == "completed":
                        break
                    await asyncio.sleep(0.02)
                settled = await result(optimizer, "agent_round_settle", id=plan["id"], revision=1)
                assert settled["result"]["decision"] == "accepted"
                assert optimizer_record in settled["result"]["cost"]["runtimeRecordIds"]
                assert invoke(env, "agent", "rsi", "lab-operator")["agent"]["referenceVersion"] == 1
                promotion = await result(
                    optimizer,
                    "agent_promotion_request",
                    id=plan["id"],
                    revision=1,
                    requestId="installed-promotion",
                )
                promotion_path = "/api/v1/agent-promotions/" + promotion["id"]
                refused = http.get(
                    promotion_path + "/review", headers={"Authorization": "Bearer " + issued["token"]}
                )
                assert refused.status_code == 403
                controller = invoke(
                    env, "agent-promotion", "controller-grant", promotion["id"], "--wall-seconds", "120"
                )
                controller_env = env | {"LW_TOKEN": controller["token"]}
                private_transport = StdioServerParameters(command=str(LW), args=["mcp"], env=controller_env)
                async with Client(private_transport, mode="legacy") as private:
                    tools = {tool.name for tool in (await private.list_tools()).tools}
                    assert tools == {
                        "agent_promotion_show",
                        "agent_promotion_next",
                        "agent_promotion_bind",
                        "agent_promotion_complete",
                        "agent_promotion_capture",
                        "agent_promotion_cancel",
                        "agent_promotion_cleanup",
                    }
                    for _ in range(100):
                        pending = await result(private, "agent_promotion_next", id=promotion["id"])
                        if pending["status"] == "pending-native":
                            break
                        await asyncio.sleep(0.02)
                    assert not {"statement", "connection", "sessionId", "caseId"} & pending["work"].keys()
                    await result(
                        private,
                        "agent_promotion_bind",
                        id=promotion["id"],
                        caseRunId=pending["work"]["caseRunId"],
                        nativeSessionId="installed-private-fresh",
                    )
                    private_item = await result(private, "agent_promotion_next", id=promotion["id"])
                    assert private_item["status"] == "work" and MARKER in private_item["work"]["statement"]
                    stopped = await result(private, "agent_promotion_cancel", id=promotion["id"])
                    assert stopped["status"] == "cancelled"
                notebook = await result(optimizer, "notebook_show", goal=plan["goalId"])
                assert len(notebook["entries"]) == 1 and MARKER not in str(notebook)
                assert (await result(optimizer, "loop_show", goal=plan["goalId"]))["deployment"][
                    "status"
                ] == "cancelled"
                exposed_files = [dict(file) for file in files]
                next(file for file in exposed_files if file["path"] == "AGENTS.md")["content"] += "\n" + (
                    suite_dir / "statements/held-out-safe-sensor.md"
                ).read_text(encoding="utf-8")
                exposed = http.post(
                    "/api/v1/agents/lab-operator/candidates",
                    headers={"Authorization": "Bearer " + issued["token"]},
                    json={
                        "referenceVersion": 1,
                        "requestId": "installed-private-exposure",
                        "files": exposed_files,
                        "change": plan["change"],
                    },
                )
                assert exposed.status_code == 409
                assert exposed.json()["detail"]["code"] == "agent_workflow_contaminated"
                for route in (
                    "/api/v1/notebooks/" + plan["goalId"],
                    "/api/v1/loops/" + plan["goalId"],
                    "/api/v1/plans/" + plan["id"],
                ):
                    projected = http.get(route)
                    assert projected.status_code == 200 and optimizer_record not in projected.text, (
                        projected.text
                    )
                current = http.get("/api/v1/notebooks/" + plan["goalId"]).json()["entries"][0]
                assert current["reportedDecision"] == "accepted"
                assert current["currentDisposition"] == "contaminated"
                assert current["comparison"]["state"] == "unknown"
        finally:
            if promotion_path:
                http.post(promotion_path + "/cancel").raise_for_status()
        # A new owned Source can use the same queue slot after cancellation.
        session = http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3", "source": "synthetic"})
        assert session.status_code == 201, session.text
        http.delete("/api/v1/sessions/" + session.json()["sessionId"]).raise_for_status()

"""Scripted external controller and Target through installed CLI, official MCP and real HTTP.

The imported native-format fixture is explicit metering input. No model or runtime
process executes; the actual Synthetic Session and platform grade are independent.
"""

import asyncio
import json

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke

SUITE = "suite/lab-ops-basic@1"
CAPTURE = ROOT / "tests/fixtures/runtime/claude-code-stream.jsonl"


async def test_installed_controller_and_scoped_target_share_the_actual_evaluation_path(gateway_url):
    env = environment(gateway_url)
    create = ("agent-run", "create", "lab-operator", "--suite", SUITE, "--harness-version", "1",
              "--runtime", "claude-code", "--runtime-version", "2.1.306", "--model", "claude-opus-5-5",
              "--thinking", "high", "--purpose", "verification", "--case", "pause-reset",
              "--wall-seconds", "120", "--request-id", "cli-controller-one-case")
    run = invoke(env, *create)
    identifier = run["id"]
    assert invoke(env, *create)["id"] == identifier
    assert run["purpose"] == "verification" and run.get("result") is None
    assert run["pins"]["scene"] == "lab-demo@3" and run["harnessVersion"] == 1
    try:
        transport = StdioServerParameters(command=str(LW), args=["mcp"], env=env)
        async with Client(transport, mode="legacy") as controller:
            tools = {tool.name: tool for tool in (await controller.list_tools()).tools}
            assert {"agent_run_create", "agent_run_list", "agent_run_show", "agent_run_next",
                    "agent_run_complete", "agent_run_cancel"} <= tools.keys()
            completion = tools["agent_run_complete"].input_schema["properties"]
            assert not {"score", "final", "finalState", "usage"} & completion.keys()
            for _ in range(100):
                handoff = await result(controller, "agent_run_next", id=identifier)
                if handoff["status"] == "work":
                    break
                await asyncio.sleep(.05)
            else:
                raise AssertionError("The owned Synthetic case did not reach the controller")
            work = handoff["work"]
            assert work["caseId"] == "pause-reset" and work["repeat"] == 1
            assert work["harness"]["archiveHash"] == run["pins"]["archiveHash"]
            assert all("private/" not in item["path"] for item in work["harness"]["files"])
            assert (await result(controller, "agent_run_next", id=identifier))["work"] == work
            assert (await result(controller, "agent_run_show", id=identifier))["id"] == identifier
            assert identifier in [item["id"] for item in await result(controller, "agent_run_list")]
            public = json.dumps(invoke(env, "agent-run", "show", identifier))
            assert work["connection"]["token"] not in public
            target_env = env | {"LW_TOKEN": work["connection"]["token"], "LW_SESSION": work["sessionId"]}
            async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=target_env),
                              mode="legacy") as target:
                target_names = {tool.name for tool in (await target.list_tools()).tools}
                assert "session_control" in target_names
                assert not any(name.startswith("agent_run_") or "approv" in name for name in target_names)
                await result(target, "session_control", action="pause")
                await result(target, "session_control", action="reset")
                await result(target, "session_control", action="pause")
            imported = invoke(env, "runtime", "import", str(CAPTURE))
            native = imported["session"]
            final = invoke(env, "agent-run", "complete", identifier, "--case-run", work["caseRunId"],
                           "--native-session", native["nativeSessionId"], "--runtime-record", native["id"])
            case = final["case"]
            assert case["status"] == "completed" and 0 <= case["score"] <= 100
            assert case["sessionId"] == work["sessionId"] and case["recordingId"]
            assert case["runtimeRecordId"] == native["id"]
            assert case["runtimeRecordHash"] == native["contentHash"]
            assert case["usage"]["llmUsd"] == .3244434
            assert case["usage"]["requests"] is None and case["usage"]["tokens"] is None
            assert case["usage"]["observationBasis"] == "completion-snapshot"
            repeated = await result(controller, "agent_run_complete", id=identifier,
                                    caseRunId=work["caseRunId"], nativeSessionId=native["nativeSessionId"],
                                    runtimeRecordId=native["id"])
            assert repeated == final
            assert final["run"]["status"] == "completed" and final["run"].get("result") is None
            assert final["run"]["cleanup"]["complete"]
            assert (await result(controller, "agent_run_next", id=identifier))["status"] == "finished"
        with httpx.Client(base_url=gateway_url, trust_env=False) as http:
            assert http.get("/api/v1/suites/lab-ops-basic/1/scoreboard").json()["rows"] == []
            for artifact in case["artifacts"]:
                response = http.get(artifact["url"])
                assert response.is_success and work["connection"]["token"] not in response.text
            assert http.get("/api/v1/health").json()["sessionCount"] == 0
    finally:
        with httpx.Client(base_url=gateway_url, trust_env=False) as http:
            retained = http.get("/api/v1/agent-runs/" + identifier).json()
            if retained["status"] in ("queued", "running"):
                http.post("/api/v1/agent-runs/" + identifier + "/cancel").raise_for_status()


async def test_controller_cancel_is_retained_without_a_score_or_training_approval(gateway_url, lab):
    # The existing interactive Session holds the same scheduler slot.
    env = environment(gateway_url)
    run = invoke(env, "agent-run", "create", "lab-operator", "--suite", SUITE, "--harness-version", "1",
                 "--runtime", "scripted-controller-fixture", "--model", "no-model-executed",
                 "--thinking", "none",
                 "--request-id", "cli-controller-cancel")
    assert run["status"] == "queued"
    identifier = run["id"]
    cancelled = invoke(env, "agent-run", "cancel", identifier, code=3)
    assert cancelled["status"] == "cancelled" and cancelled.get("result") is None
    assert invoke(env, "agent-run", "show", identifier, code=3)["status"] == "cancelled"
    listed = invoke(env, "agent-run", "ls", "--agent", "agent/lab-operator")
    assert identifier in [item["id"] for item in listed]
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=env)
    async with Client(transport, mode="legacy") as controller:
        assert (await result(controller, "agent_run_cancel", id=identifier))["status"] == "cancelled"
    assert lab[0].get("/api/v1/health").json()["sessionCount"] == 1

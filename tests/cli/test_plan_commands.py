"""Installed CLI and official MCP clients operate plans through actual Synthetic HTTP."""

import json
import os
import subprocess
import threading
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml
from cli_support import LW, ROOT, result
from mcp import Client, Implementation, StdioServerParameters

CENTRIFUGE = "/World/Lab/Centrifuge"
CONDITION = {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False, "message": "盖子必须保持关闭"}
DOCUMENT = {
    "title": "Close and verify",
    "target": "session",
    "steps": [
        {"id": 1, "do": {"entity": CENTRIFUGE, "action": "closeLid"}, "expect": CONDITION},
        {"id": 2, "wait": CONDITION, "timeoutSeconds": 3},
        {"id": 3, "check": CONDITION},
    ],
}


def test_installed_plan_create_revise_show_list_do_wait_and_check(lw, lab, tmp_path):
    source = tmp_path / "operation.yaml"
    source.write_text(yaml.safe_dump(DOCUMENT), encoding="utf-8")
    submitted = lw("plan", "create", str(source))
    assert submitted["status"] == "approved" and submitted["requestedBy"]["kind"] == "cli"
    plan_id = submitted["id"]
    assert lw("do", CENTRIFUGE, "closeLid", "--plan", plan_id, "--step", "1")["status"] == "succeeded"
    assert lw("wait", CENTRIFUGE, "lidOpen", "equals", "false", "--plan", plan_id, "--step", "2")["satisfied"]
    assert lw("check", CENTRIFUGE, "lidOpen", "equals", "false", "--plan", plan_id, "--step", "3")[
        "satisfied"
    ]
    assert lw("plan", "show", plan_id)["status"] == "completed"
    assert plan_id in [item["id"] for item in lw("plan", "ls", "--status", "completed")]
    changed = deepcopy(DOCUMENT)
    changed["steps"].append({"id": 4, "check": CONDITION})
    source.write_text(yaml.safe_dump(changed), encoding="utf-8")
    revised = lw("plan", "revise", plan_id, str(source))
    assert revised["revision"] == 2 and revised["steps"][0]["status"] == "done"
    assert lw("plan", "show", plan_id, "--revision", "1")["status"] == "completed"
    failed = lw("check", CENTRIFUGE, "lidOpen", "equals", "true", code=2)
    assert failed["satisfied"] is False and failed["value"] is False


async def test_official_stdio_mcp_structured_plans_and_optional_step_references(lab):
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=lab[2])
    async with Client(
        transport, mode="legacy", client_info=Implementation(name="plan-agent", version="1")
    ) as mcp:
        submitted = await result(mcp, "plan_create", document=DOCUMENT)
        assert submitted["status"] == "approved"
        assert submitted["requestedBy"] == {"kind": "agent", "name": "plan-agent"}
        rejected = await mcp.call_tool("plan_create", {"document": "never-read-a-client-path.yaml"})
        assert rejected.is_error
        plan_id = submitted["id"]
        closed = await result(
            mcp, "invoke_action", key=CENTRIFUGE, action="closeLid", planId=plan_id, stepId=1
        )
        assert closed["status"] == "succeeded"
        assert (
            await result(
                mcp,
                "wait_until",
                key=CENTRIFUGE,
                property="lidOpen",
                op="equals",
                value=False,
                planId=plan_id,
                stepId=2,
            )
        )["satisfied"]
        assert (
            await result(
                mcp,
                "check_condition",
                key=CENTRIFUGE,
                property="lidOpen",
                op="equals",
                value=False,
                planId=plan_id,
                stepId=3,
            )
        )["satisfied"]
        assert (await result(mcp, "plan_show", id=plan_id))["status"] == "completed"
        revised = await result(
            mcp,
            "plan_revise",
            id=plan_id,
            document={
                **DOCUMENT,
                "steps": DOCUMENT["steps"] + [{"id": 4, "check": CONDITION}],
            },
        )
        assert revised["revision"] == 2 and revised["steps"][0]["status"] == "done"
        assert plan_id in [item["id"] for item in await result(mcp, "plan_list")]


def test_installed_plan_runner_resumes_done_steps_and_handles_condition_steps(lw, tmp_path):
    document = deepcopy(DOCUMENT)
    for step, identity in zip(document["steps"], (7, 13, 22), strict=True):
        step["id"] = identity
    source = tmp_path / "run.yaml"
    source.write_text(yaml.safe_dump(document), encoding="utf-8")
    plan = lw("plan", "create", str(source))
    previous = lw("do", CENTRIFUGE, "closeLid", "--plan", plan["id"], "--step", "7")
    run = lw("plan", "run", plan["id"], "--revision", "1")
    assert run["status"] == run["plan"]["status"] == "completed"
    assert [item["stepId"] for item in run["results"]] == [13, 22]
    assert run["plan"]["stepResults"]["7"]["invocationIds"] == [previous["invocationId"]]
    assert lw("get", CENTRIFUGE, "lidOpen") is False
    again = lw("plan", "run", plan["id"])
    assert again["status"] == "completed" and again["results"] == []
    assert again["plan"]["stepResults"] == run["plan"]["stepResults"]


def test_installed_plan_runner_stops_on_failed_expect_and_refuses_retry(lw, tmp_path):
    document = deepcopy(DOCUMENT)
    document["steps"][0]["expect"]["equals"] = True
    document["steps"][1:] = [{"id": 2, "do": {"entity": CENTRIFUGE, "action": "openLid"}}]
    source = tmp_path / "failed-expect.yaml"
    source.write_text(yaml.safe_dump(document), encoding="utf-8")
    plan = lw("plan", "create", str(source))
    run = lw("plan", "run", plan["id"], code=3)
    assert run["status"] == "failed" and run["stoppedStep"] == 1
    assert run["results"][0]["result"]["status"] == "succeeded"
    assert run["plan"]["steps"][0]["status"] == "failed"
    assert run["plan"]["stepResults"]["1"]["failed"][0]["actual"] is False
    assert run["plan"]["steps"][1]["status"] == "pending"
    assert lw("get", CENTRIFUGE, "lidOpen") is False
    retry = lw("plan", "run", plan["id"], code=1)
    assert retry["error"]["detail"]["code"] == "plan_not_runnable"


def test_installed_plan_runner_timeout_retains_invocation_and_stops(lw, tmp_path):
    source = tmp_path / "timeout.yaml"
    source.write_text(yaml.safe_dump(DOCUMENT), encoding="utf-8")
    plan = lw("plan", "create", str(source))
    lw("session", "pause")
    run = lw("plan", "run", plan["id"], "--timeout", "0.01", code=4)
    assert run["timedOut"] and run["stoppedStep"] == 1
    invocation = run["results"][0]["result"]
    assert invocation["status"] in ("accepted", "running")
    assert run["plan"]["steps"][1]["status"] == "pending"
    lw("session", "resume")
    assert lw("inv", invocation["invocationId"], "--wait")["status"] == "succeeded"
    run = lw("plan", "run", plan["id"])
    assert run["status"] == "completed"
    assert run["plan"]["stepResults"]["1"]["invocationIds"] == [invocation["invocationId"]]


def test_installed_plan_runner_rejects_stale_revision_and_invalid_timeout(lw, tmp_path):
    source = tmp_path / "stale.yaml"
    source.write_text(yaml.safe_dump(DOCUMENT), encoding="utf-8")
    plan = lw("plan", "create", str(source))
    rejected = lw("plan", "run", plan["id"], "--revision", "2", code=1)
    assert rejected["error"]["detail"]["code"] == "plan_not_runnable"
    assert lw("get", CENTRIFUGE, "lidOpen") is True
    for timeout in ("0", "nan", "inf", "-1"):
        assert "finite and positive" in lw("plan", "run", plan["id"], "--timeout", timeout, code=1)[
            "error"]["message"]


def test_invalid_local_documents_fail_cleanly_before_any_http(tmp_path):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(("GET", self.path))
            self.send_error(500)

        def do_POST(self):
            calls.append(("POST", self.path))
            self.send_error(500)

        def log_message(self, *args):
            pass

    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("sensitive-should-not-print: [\n", encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = os.environ | {
        "LW_URL": f"http://127.0.0.1:{server.server_port}",
        "LW_SESSION": "session-never-contact",
        "LW_TOKEN": "",
    }
    try:
        for source, message in (
            (tmp_path / "missing.yaml", "Cannot read plan document"),
            (invalid, "Plan document is not valid YAML"),
        ):
            for flags in ([], ["--json"]):
                invoked = subprocess.run(
                    [str(LW), "plan", "create", str(source), *flags],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=10,
                )
                assert invoked.returncode == 1
                if flags:
                    assert json.loads(invoked.stdout)["error"]["message"] == message
                else:
                    assert message in invoked.stderr
                assert "Traceback" not in invoked.stderr
                assert "sensitive-should-not-print" not in invoked.stdout + invoked.stderr
        assert calls == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_installed_cli_executes_deviation_plan_and_rejects_bad_thresholds(lw, lab, tmp_path):
    source = tmp_path / "deviation.yaml"
    condition = {
        "entity": CENTRIFUGE,
        "property": "rpm",
        "deviatesFrom": "rpm",
        "by": 0,
        "message": "转速应有差异",
    }
    source.write_text(
        yaml.safe_dump({"title": "Deviation", "steps": [{"id": 1, "check": condition}]}), "utf-8"
    )
    submitted = lw("plan", "create", str(source))
    checked = lw(
        "check",
        CENTRIFUGE,
        "rpm",
        "deviatesFrom",
        '"rpm"',
        "--by",
        "0",
        "--plan",
        submitted["id"],
        "--step",
        "1",
        code=2,
    )
    assert checked["failed"][0]["message"] == "转速应有差异"
    for by in ("true", "-1", "nan", "inf"):
        error = lw("check", CENTRIFUGE, "rpm", "deviatesFrom", '"rpm"', "--by", by, code=1)
        assert "nonnegative" in error["error"]["message"]
    unrelated = lw("check", CENTRIFUGE, "rpm", "equals", "0", "--by", "0", code=1)
    assert unrelated["error"]["detail"]["code"] == "invalid_condition"


async def test_official_sdk_executes_deviation_plan_and_rejects_bad_thresholds(lab):
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=lab[2])
    async with Client(transport, mode="legacy") as mcp:
        condition = {
            "entity": CENTRIFUGE,
            "property": "rpm",
            "deviatesFrom": "rpm",
            "by": 0,
            "message": "转速应有差异",
        }
        submitted = await result(
            mcp,
            "plan_create",
            document={
                "title": "SDK deviation",
                "steps": [
                    {"id": 1, "check": condition},
                    {"id": 2, "wait": condition},
                ],
            },
        )
        checked = await result(
            mcp,
            "check_condition",
            key=CENTRIFUGE,
            property="rpm",
            op="deviatesFrom",
            value="rpm",
            by=0,
            planId=submitted["id"],
            stepId=1,
        )
        assert checked["failed"][0]["message"] == "转速应有差异"
        waited = await result(
            mcp,
            "wait_until",
            key=CENTRIFUGE,
            property="rpm",
            op="deviatesFrom",
            value="rpm",
            by=0,
            planId=submitted["id"],
            stepId=2,
            timeoutSeconds=0.02,
        )
        assert waited["timedOut"] and waited["failed"][0]["message"] == "转速应有差异"
        for by in (True, -1, "nan", float("inf")):
            refused = await mcp.call_tool(
                "check_condition",
                {"key": CENTRIFUGE, "property": "rpm", "op": "deviatesFrom", "value": "rpm", "by": by},
            )
            assert refused.is_error
        unrelated = await mcp.call_tool(
            "check_condition", {"key": CENTRIFUGE, "property": "rpm", "op": "equals", "value": 0, "by": 0}
        )
        assert unrelated.is_error

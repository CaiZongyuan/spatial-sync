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
CONDITION = {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False}
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

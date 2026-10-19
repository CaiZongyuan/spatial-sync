"""Command-table behavior through installed lw and the official MCP stdio client."""

import json
import subprocess
from pathlib import Path

from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters

CENTRIFUGE = "/World/Lab/Centrifuge"


async def test_generated_inspections_match_http_cli_and_mcp_and_preserve_all_tool_schemas(lw, lab):
    # Compare each client against one frozen state; IoT readings normally advance.
    lw("session", "pause")
    transport = StdioServerParameters(command=str(LW), args=["mcp"], env=lab[2])
    async with Client(transport, mode="legacy") as mcp:
        tools = (await mcp.list_tools()).tools
        # Public input/output schemas captured from the unmodified baseline, 63abf03.
        expected = json.loads((Path(__file__).parent / "fixtures/mcp_tools.json").read_text(encoding="utf-8"))
        schemas = {tool.name: {"input": tool.input_schema, "output": tool.output_schema} for tool in tools}
        # The operation-plan feature adds optional HTTP correlation, leaving legacy calls unchanged.
        for name in ("invoke_action", "wait_until"):
            for field in ("planId", "stepId", "planRevision"):
                declaration = schemas[name]["input"]["properties"].pop(field)
                assert declaration["default"] is None
                assert field not in schemas[name]["input"].get("required", [])
        threshold = schemas["wait_until"]["input"]["properties"].pop("by")
        assert threshold["default"] is None
        assert "by" not in schemas["wait_until"]["input"].get("required", [])
        assert {name: schemas[name] for name in expected} == expected
        assert all("approv" not in tool.name for tool in tools)
        forbidden = await mcp.call_tool("approve_invocation", {"invocation_id": "inv-do-not-approve"})
        assert forbidden.is_error and "approval_requires_human" in forbidden.content[0].text
        for flags, arguments in (
            ((), {}),
            (("--type", "centrifuge/v1"), {"type": "centrifuge/v1"}),
            (("-q", "Centrifuge"), {"query": "Centrifuge"}),
            (("--type", "tube/v1", "--query", "Centrifuge"), {"type": "tube/v1", "query": "Centrifuge"}),
        ):
            query = {("q" if name == "query" else name): value for name, value in arguments.items()}
            expected_entities = lab[0].get(lab[1] + "/entities", params=query).json()["entities"]
            assert lw("ls", *flags) == await result(mcp, "list_entities", **arguments) == expected_entities
        expected_entity = lab[0].get(lab[1] + "/entity", params={"key": CENTRIFUGE}).json()
        description = await result(mcp, "describe_entity", key=CENTRIFUGE)
        assert lw("show", CENTRIFUGE) == description == expected_entity
        missing = await mcp.call_tool("describe_entity", {"key": "missing"})
        assert missing.is_error and '"statusCode": 404' in missing.content[0].text


def test_generated_commands_document_parameters_and_accept_global_or_local_json(lab):
    for command in (("ls",), ("show",), ("cancel",), ("approve",), ("rec", "ls")):
        help_result = subprocess.run(
            [str(LW), *command, "--help"],
            cwd=ROOT,
            env=lab[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        assert help_result.returncode == 0 and "--json" in help_result.stdout
    for arguments in (("--json", "ls"), ("ls", "--json")):
        invocation = subprocess.run(
            [str(LW), *arguments],
            cwd=ROOT,
            env=lab[2],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        assert invocation.returncode == 0 and isinstance(json.loads(invocation.stdout), list)


def test_generated_human_only_approval_rejects_json_even_for_terminal_invocations(lw, lab):
    assert lw("show", "missing", code=1)["error"]["statusCode"] == 404
    rejected = lw("do", CENTRIFUGE, "start", "rpm=500", "durationSeconds=1", code=2)
    assert (
        lw("approve", rejected["invocationId"], code=1)["error"]["detail"]["code"]
        == "approval_requires_human"
    )
    lw("session", "pause")
    pending = lw("do", CENTRIFUGE, "closeLid", "--no-wait")
    lab[0].post(lab[1] + "/invocations/" + pending["invocationId"] + "/cancel").raise_for_status()
    lw("inv", pending["invocationId"], "--wait", code=3)
    assert (
        lw("approve", pending["invocationId"], code=1)["error"]["detail"]["code"] == "approval_requires_human"
    )

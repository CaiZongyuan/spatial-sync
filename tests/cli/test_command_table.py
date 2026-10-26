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
        # Original eight schemas remain unchanged; model_ls is an additive metadata tool.
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
        # Catalog metadata adds only these optional inputs; legacy calls and outputs remain exact.
        catalog_additions = {
            "catalog_search": {
                "category": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "tag": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "release": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "page": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                "gaps": {"type": "boolean"},
            },
            "catalog_show": {"release": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
            "catalog_estimate": {"release": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        }
        for name, additions in catalog_additions.items():
            properties = schemas[name]["input"]["properties"]
            assert set(properties) - set(expected[name]["input"]["properties"]) == set(additions)
            for field, shape in additions.items():
                declaration = properties.pop(field)
                assert declaration == {**shape, "default": None, "title": field.title()}
                assert field not in schemas[name]["input"].get("required", [])
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

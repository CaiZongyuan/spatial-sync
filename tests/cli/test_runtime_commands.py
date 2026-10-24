"""Installed CLI and official MCP import existing native records through HTTP."""

import json

import httpx
from cli_support import LW, ROOT, result
from mcp import Client, StdioServerParameters
from test_training_commands import environment, invoke

CAPTURE = ROOT / "tests/fixtures/runtime/claude-code-stream.jsonl"


async def test_installed_runtime_file_grows_and_mcp_retries_without_double_count(gateway_url, tmp_path):
    env = environment(gateway_url)
    lines = CAPTURE.read_text(encoding="utf-8").splitlines()
    growing = tmp_path / "native capture.jsonl"
    growing.write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
    partial = invoke(env, "runtime", "import", str(growing))
    identifier = partial["session"]["id"]
    assert partial["addedEvents"] == 3
    assert partial["session"]["associations"] == {}
    assert partial["session"]["usage"]["cacheReadTokens"] == 0
    assert partial["session"]["usage"]["llmUsd"] is None
    growing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    full = invoke(env, "runtime", "import", str(growing))
    assert full["session"]["id"] == identifier and full["addedEvents"] == 1
    expected = {
        "inputTokens": 32, "outputTokens": 2680, "cacheReadTokens": 278457,
        "cacheWriteTokens": 26878, "tokens": None, "requests": None, "turns": 20,
        "llmUsd": 0.3244434, "costSource": "runtime-estimate",
    }
    assert full["session"]["usage"] | expected == full["session"]["usage"]
    assert full["session"]["thinking"] is None
    assert invoke(env, "runtime", "import", str(growing))["idempotent"]
    objects = [json.loads(line) for line in lines]
    array_file = tmp_path / "records.json"
    array_file.write_text(json.dumps(objects), encoding="utf-8")
    assert invoke(env, "runtime", "ingest", str(array_file))["idempotent"]
    async with Client(StdioServerParameters(command=str(LW), args=["mcp"], env=env), mode="legacy") as mcp:
        tools = {tool.name: tool for tool in (await mcp.list_tools()).tools}
        assert {"runtime_import", "runtime_list", "runtime_show"} <= tools.keys()
        schema = tools["runtime_import"].input_schema
        assert "file" not in schema["properties"] and "path" not in schema["properties"]
        assert schema["properties"]["records"]["type"] == "array"
        repeated = await result(mcp, "runtime_import", records=objects)
        assert repeated["idempotent"] and repeated["addedEvents"] == repeated["updatedEvents"] == 0
        detail = await result(mcp, "runtime_show", id=identifier)
        assert detail["usage"] == full["session"]["usage"]
        assert len(detail["events"]) == 4 and detail["toolCallCount"] == 1
        assert [record["id"] for record in await result(mcp, "runtime_list")] == [identifier]
        wrong = await mcp.call_tool("runtime_import", {"records": str(growing)})
        assert wrong.is_error
    assert invoke(env, "runtime", "show", identifier)["events"] == detail["events"]
    assert len(invoke(env, "runtime", "ls", "--query", growing.name)) == 1
    with httpx.Client(base_url=gateway_url, trust_env=False) as http:
        for artifact in detail["artifacts"]:
            assert artifact["url"].startswith(f"/api/v1/runtime-sessions/{identifier}/artifacts/")
            evidence = http.get(artifact["url"])
            assert evidence.status_code == 200
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
        assert http.get("/api/v1/jobs").json()["jobs"] == []
        assert http.get("/api/v1/evaluations").json()["evaluations"] == []


def test_runtime_import_requires_explicit_valid_file_and_reference_pairs(gateway_url, tmp_path):
    env = environment(gateway_url)
    assert "UTF-8" in invoke(env, "runtime", "import", str(tmp_path / "missing"), code=1)["error"]["message"]
    invalid = tmp_path / "invalid.jsonl"
    invalid.write_text('{"type": "assistant", "text": "not native"}', encoding="utf-8")
    failure = invoke(env, "runtime", "import", str(invalid), code=1)["error"]
    assert failure["statusCode"] in (400, 422)
    pair = invoke(env, "runtime", "import", str(CAPTURE), "--plan", "unapproved-plan", code=1)
    assert "both --plan and --plan-revision" in pair["error"]["message"]
    harness = invoke(env, "runtime", "import", str(CAPTURE), "--harness-version", "1", code=1)
    assert "requires --agent" in harness["error"]["message"]

"""Optional real SDK + local fake provider; never uses a real credential/provider."""

import asyncio
import importlib.util
import json
import socket
import sys
import time
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response

from apps.api.app.main import create_app
from packages.agents.bundle import prepare_harness
from packages.agents.provider import (
    TOKEN_KEYS,
    BudgetedProvider,
    ProviderLimits,
    ProviderQuote,
    install_provider_route,
)
from packages.agents.runtime import AgentWorker, clean_environment, sdk_configuration
from packages.agents.transport import CredentialRelay
from packages.library.transactions import durable_json
from packages.vault import Vault

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("claude_agent_sdk") is None,
    reason="Optional pinned Agent SDK is not installed in the GPU-free gate",
)


@asynccontextmanager
async def serving(app):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.setblocking(False)
    url = "http://127.0.0.1:" + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield url
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()


def stream(number, block, stop):
    original = block
    block = block | ({"input": {}} if block["type"] == "tool_use" else {"text": ""})
    events = [
        {
            "type": "message_start",
            "message": {
                "id": f"offline-{number}",
                "type": "message",
                "role": "assistant",
                "model": "fixture-model",
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": dict.fromkeys(TOKEN_KEYS, 0) | {"input_tokens": 20},
            },
        },
        {"type": "content_block_start", "index": 0, "content_block": block},
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": json.dumps(original["input"])}
            if block["type"] == "tool_use"
            else {"type": "text_delta", "text": original["text"]},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": 3},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {item['type']}\ndata: {json.dumps(item)}\n\n" for item in events)


async def test_real_sdk_cannot_execute_forced_builtin_and_uses_only_fixed_session_mcp(tmp_path, monkeypatch):
    sentinel = "PRIVATE_INHERITED_CREDENTIAL_7d12"
    monkeypatch.setenv("ANTHROPIC_API_KEY", sentinel)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", sentinel)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://must-not-reach.invalid")
    marker = tmp_path / "forbidden-built-in"
    upstream_key = "GENERATED_LOCAL_UPSTREAM_CREDENTIAL_35ad"
    vault = Vault(tmp_path / "private-upstream")
    vault.set("llm/offline-generated", upstream_key)
    private_context = "PRIVATE_ANCESTOR_CONTEXT_2f0a"
    private_user = "PRIVATE_USER_CONTEXT_6b31"
    (tmp_path / "CLAUDE.md").write_text(private_context, encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(private_context, encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").write_text(
        json.dumps(
            {
                "env": {"PRIVATE_PROJECT_CANARY": private_context},
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": f'echo escaped > "{marker}"'}]}
                    ]
                },
            }
        )
    )
    inherited_home = tmp_path / "inherited-home"
    (inherited_home / ".claude").mkdir(parents=True)
    (inherited_home / ".claude/settings.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {"hooks": [{"type": "command", "command": f'echo escaped > "{marker}"'}]}
                    ]
                },
            }
        )
    )
    (inherited_home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "must-not-load": {
                        "command": sys.executable,
                        "args": ["-c", "raise RuntimeError('not allowed')"],
                    },
                }
            }
        )
    )
    (inherited_home / ".claude/CLAUDE.md").write_text(private_user, encoding="utf-8")
    for name in ("HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR"):
        monkeypatch.setenv(
            name, str(inherited_home / ".claude" if name == "CLAUDE_CONFIG_DIR" else inherited_home)
        )
    calls, state = [], {}

    async def external(request):
        body = json.loads(request.content)
        calls.append(body)
        index = len(calls)
        if index == 1:
            block = {
                "type": "tool_use",
                "id": "forced-builtin",
                "name": "Bash",
                "input": {"command": f'echo escaped > "{marker}"'},
            }
        elif index == 2:
            block = {
                "type": "tool_use",
                "id": "owned-read",
                "name": "mcp__lab__get_state",
                "input": {"keys": ["/World/Lab/Centrifuge"]},
            }
        else:
            block = {"type": "text", "text": "Finished the offline fixture. " + upstream_key}
        return httpx.Response(
            200,
            text=stream(index, block, "end_turn" if index >= 3 else "tool_use"),
            headers={"content-type": "text/event-stream"},
        )

    app, transport_app, external_app = create_app(), FastAPI(), FastAPI()

    @external_app.post("/v1/messages")
    async def fake_upstream(request: Request):
        assert request.headers["x-api-key"] == upstream_key
        response = await external(
            httpx.Request("POST", "http://fake/v1/messages", content=await request.body())
        )
        return Response(response.content, media_type="text/event-stream")

    deadline = time.monotonic() + 45
    async with (
        serving(external_app) as upstream_url,
        CredentialRelay(
            tmp_path / "credential-relay",
            vault,
            "llm/offline-generated",
            "fixture-model",
            deadline=deadline,
            upstream_origin=upstream_url,
        ) as credential_relay,
    ):
        gate = BudgetedProvider(
            "job-offline",
            "fixture-model",
            ProviderLimits(6, 200000, 1, 45, 256),
            ProviderQuote(1, dict.fromkeys(TOKEN_KEYS, 1.0)),
            32000,
            state,
            lambda: durable_json(tmp_path / "usage.json", state),
            credential_relay.client,
            credential_relay.grant,
            transport_token="local-provider-only",
            trusted_relay=True,
        )
        install_provider_route(transport_app, gate)
        async with serving(app) as gateway_url, serving(transport_app) as provider_url:
            async with httpx.AsyncClient(base_url=gateway_url, trust_env=False) as http:
                result = await http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3"})
                assert result.status_code == 201, result.text
                session_id = result.json()["sessionId"]
                session = app.state.manager.get(session_id)
                session.agent_run_id, session.deny_human_gates = "job-offline", True
                grant = app.state.agent_grants.issue("job-offline", session_id)
                harness = prepare_harness(app.state.agents, "lab-operator", 1)
                configuration = sdk_configuration(
                    harness,
                    model="fixture-model",
                    thinking="none",
                    statement="Read the centrifuge state.",
                    requests=6,
                    max_output_tokens=256,
                )
                environment = clean_environment(
                    tmp_path / "environment",
                    gateway_url=gateway_url,
                    session_id=session_id,
                    run_id="job-offline",
                    tool_token=grant,
                    provider_url=provider_url,
                    provider_token="local-provider-only",
                )
                worker = AgentWorker(
                    tmp_path / "case",
                    configuration,
                    environment,
                    files=harness.files,
                    deadline=deadline,
                )
                receipt = await worker.run()
                trace = receipt["trace"].read_text(encoding="utf-8")
                assert receipt["code"] == 0, trace
                assert not marker.exists()
                assert all(
                    file.read_text(encoding="ascii") == "clean"
                    for file in (tmp_path / "case/public").glob("clean-*")
                )
                assert list((tmp_path / "case/public").glob("clean-*"))
                assert len(calls) == 3, trace
                rows = [json.loads(line) for line in trace.splitlines()]
                init = next(
                    row["data"]["value"]["data"]
                    for row in rows
                    if row["kind"] == "message" and row["data"]["value"].get("subtype") == "init"
                )
                assert {item["name"] for item in init["mcp_servers"]} == {"lab"}
                assert "Bash" not in init["tools"] and "Skill" not in init["tools"]
                assert "must-not-load" not in trace
                assert any(
                    row["kind"] == "tool.after" and row["data"]["tool"] == "mcp__lab__get_state"
                    for row in rows
                ), trace
                assert not any(
                    row["kind"] == "tool.before" and row["data"]["allowed"] and row["data"]["tool"] == "Bash"
                    for row in rows
                )
                assert sentinel not in trace and grant not in trace and "local-provider-only" not in trace
                for private in (
                    sentinel,
                    private_context,
                    private_user,
                    grant,
                    "local-provider-only",
                    upstream_key,
                ):
                    assert private not in json.dumps(calls)
                    assert all(
                        private.encode() not in file.read_bytes()
                        for file in (tmp_path / "case").rglob("*")
                        if file.is_file()
                    )
                assert gate.snapshot()["tokens"] == 69
                app.state.agent_grants.revoke("job-offline")
    assert credential_relay.process.cleanup.read_text(encoding="ascii") == "clean"

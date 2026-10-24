"""Owned relay process uses generated credentials against only a local fake upstream."""

import asyncio
import json
import time

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from packages.agents.provider import TOKEN_KEYS, BudgetedProvider, ProviderLimits, ProviderQuote
from packages.agents.transport import CredentialRelay
from packages.library.transactions import durable_json
from packages.vault import Vault
from tests.gateway.test_agent_sdk_offline import serving


async def test_real_relay_injects_only_generated_key_and_reaps_after_revocation(tmp_path, monkeypatch):
    key = "GENERATED_TEST_UPSTREAM_CREDENTIAL_44"
    inherited = "PRIVATE_PARENT_SHOULD_NOT_ENTER_RELAY"
    monkeypatch.setenv("ANTHROPIC_API_KEY", inherited)
    monkeypatch.setenv("HTTP_PROXY", inherited)
    vault = Vault(tmp_path / "private-state")
    vault.set("llm/generated-relay-fixture", key)
    external, calls, state = FastAPI(), [], {}

    @external.post("/v1/messages")
    async def upstream(request: Request):
        assert request.headers["x-api-key"] == key
        calls.append(await request.json())
        return {
            "type": "message",
            "id": "fixture-response",
            "content": [{"type": "text", "text": key}],
            "usage": dict.fromkeys(TOKEN_KEYS, 0) | {"input_tokens": 20, "output_tokens": 10},
        }

    async with serving(external) as upstream_url:
        relay = CredentialRelay(
            tmp_path / "transport",
            vault,
            "llm/generated-relay-fixture",
            "fixture-model",
            deadline=time.monotonic() + 10,
            upstream_origin=upstream_url,
        )
        try:
            client = await relay.start()
            budgeted = BudgetedProvider(
                "job-relay-process",
                "fixture-model",
                ProviderLimits(5, 500, 1, 10, 20),
                ProviderQuote(1, dict.fromkeys(TOKEN_KEYS, 1.0)),
                100,
                state,
                lambda: durable_json(tmp_path / "usage.json", state),
                client,
                relay.grant,
                transport_token="SDK_GATE_GRANT",
                trusted_relay=True,
            )
            payload = {"model": "fixture-model", "max_tokens": 20, "messages": []}
            response = await budgeted.generate(payload)
            assert response.status_code == 200 and key.encode() not in response.body
            assert budgeted.snapshot()["requests"] == 1 and budgeted.snapshot()["tokens"] == 30
            relay.revoke()
            denied = await budgeted.generate(payload)
            assert denied.status_code == 403 and len(calls) == 1
            assert budgeted.snapshot()["requests"] == 1 and budgeted.snapshot()["notForwardedRequests"] == 1
        finally:
            await relay.close()
        assert relay.process.process.poll() is not None
        assert relay.process.cleanup.read_text(encoding="ascii") == "clean"
        for file in (tmp_path / "transport").rglob("*"):
            if file.is_file():
                content = file.read_bytes()
                assert key.encode() not in content and inherited.encode() not in content


async def test_reaped_inflight_relay_preserves_its_observed_prefix_in_the_original_budget_record(tmp_path):
    key = "GENERATED_CANCEL_TEST_UPSTREAM_KEY_44"
    vault = Vault(tmp_path / "private-state")
    vault.set("llm/relay-cancel", key)
    external, state = FastAPI(), {}

    @external.post("/v1/messages")
    async def upstream(request: Request):
        assert request.headers["x-api-key"] == key

        async def content():
            event = {
                "type": "message_start",
                "message": {
                    "id": "known-prefix",
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
            yield ("data: " + json.dumps(event) + "\n\n").encode()
            while True:
                await asyncio.sleep(0.03)
                yield b": still active\n\n"

        return StreamingResponse(content(), media_type="text/event-stream")

    async with serving(external) as upstream_url:
        relay = CredentialRelay(
            tmp_path / "transport",
            vault,
            "llm/relay-cancel",
            "fixture-model",
            deadline=time.monotonic() + 10,
            upstream_origin=upstream_url,
        )
        pending = None
        try:
            client = await relay.start()
            gate = BudgetedProvider(
                "job-cancel-relay",
                "fixture-model",
                ProviderLimits(5, 500, 1, 10, 20),
                ProviderQuote(1, dict.fromkeys(TOKEN_KEYS, 1.0)),
                100,
                state,
                lambda: durable_json(tmp_path / "usage.json", state),
                client,
                relay.grant,
                transport_token="SDK_ONLY_GATE_GRANT",
                trusted_relay=True,
            )
            pending = asyncio.create_task(
                gate.generate({"model": "fixture-model", "max_tokens": 20, "stream": True})
            )
            async with asyncio.timeout(5):
                receipt = tmp_path / "transport/observations.jsonl"
                while not receipt.exists() or not receipt.read_bytes().endswith(b"\n"):
                    await asyncio.sleep(0.01)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await relay.close()
            relay.reconcile(gate)
            snapshot = gate.snapshot()
            assert snapshot["requests"] == 1 and snapshot["tokens"] is None
            assert snapshot["knownTokens"] == 20 and snapshot["reservedTokens"] == 100
            assert state["requests"][0]["id"] == "job-cancel-relay/request/1"
            assert state["requests"][0]["providerMessageId"] == "known-prefix"
            saved = (tmp_path / "usage.json").read_bytes()
            relay.reconcile(gate)
            assert (tmp_path / "usage.json").read_bytes() == saved
            assert key.encode() not in receipt.read_bytes()
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await relay.close()

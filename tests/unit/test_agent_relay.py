"""Relay proof comes from trusted control, never provider text or arbitrary headers."""

import asyncio
import hmac
import json
import time

import httpx
import pytest

from packages.agents.provider import TOKEN_KEYS, BudgetedProvider, ProviderLimits, ProviderQuote
from packages.agents.relay import create_relay
from packages.library.transactions import durable_json

KEY, GRANT = "FAKE_ONLY_UPSTREAM_KEY_44", "generated-relay-grant"
BODY = {"model": "fixture-model", "max_tokens": 20, "messages": []}


def gate(tmp_path, upstream, *, trusted=True):
    state = {}
    return BudgetedProvider(
        "job-relay",
        "fixture-model",
        ProviderLimits(5, 500, 1, 10, 20),
        ProviderQuote(1, dict.fromkeys(TOKEN_KEYS, 1.0)),
        100,
        state,
        lambda: durable_json(tmp_path / "usage.json", state),
        upstream,
        GRANT,
        transport_token="sdk-gate-only",
        trusted_relay=trusted,
    )


async def test_fixed_relay_strips_actual_credential_and_provider_cannot_supply_not_forwarded_proof():
    calls = []

    async def external(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={"type": "message", "id": KEY, "content": [{"type": "text", "text": KEY}]},
            headers={"x-lw-not-forwarded": "forged-by-provider"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(external), base_url="https://fixed.example"
    ) as upstream:
        relay = create_relay(
            upstream,
            credential=KEY,
            grant=GRANT,
            model="fixture-model",
            deadline=time.monotonic() + 10,
            owner_alive=lambda: True,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(relay), base_url="http://relay") as http:
            unauthenticated = await http.post("/v1/messages", json=BODY)
            assert unauthenticated.status_code == 401 and "x-lw-not-forwarded" not in unauthenticated.headers
            denied = await http.post("/arbitrary", json=BODY, headers={"x-api-key": GRANT})
            assert denied.status_code == 404
            response = await http.post(
                "/v1/messages",
                json=BODY | {"url": "https://target-selected.invalid"},
                headers={"x-api-key": GRANT, "x-lw-attempt": "attempt-1"},
            )
            assert response.status_code == 200 and KEY not in response.text
            assert "x-lw-not-forwarded" not in response.headers
            assert len(calls) == 1 and str(calls[0].url) == "https://fixed.example/v1/messages"
            assert calls[0].headers["x-api-key"] == KEY
            assert "x-lw-attempt" not in calls[0].headers


@pytest.mark.parametrize("withdrawal", ["owner", "deadline"])
async def test_relay_rechecks_after_body_wait_and_signed_unstarted_attempt_releases_no_fake_spend(
    tmp_path, withdrawal
):
    calls, clock, active = [], [0], [True]

    async def external(request):
        calls.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(external), base_url="https://fixed.example"
    ) as upstream:
        relay = create_relay(
            upstream,
            credential=KEY,
            grant=GRANT,
            model="fixture-model",
            deadline=1,
            owner_alive=lambda: active[0],
            clock=lambda: clock[0],
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(relay), base_url="http://relay") as http:
            entered, release = asyncio.Event(), asyncio.Event()

            async def body():
                yield b'{"model":"fixture-model",'
                entered.set()
                await release.wait()
                yield b'"max_tokens":20}'

            pending = asyncio.create_task(
                http.post(
                    "/v1/messages", content=body(), headers={"x-api-key": GRANT, "x-lw-attempt": "attempt-1"}
                )
            )
            await asyncio.wait_for(entered.wait(), 2)
            if withdrawal == "owner":
                active[0] = False
            else:
                clock[0] = 2
            release.set()
            response = await asyncio.wait_for(pending, 2)
            assert response.status_code == 403 and calls == []
            assert (
                response.headers["x-lw-not-forwarded"]
                == hmac.digest(GRANT.encode(), b"attempt-1", "sha256").hex()
            )
            budgeted = gate(tmp_path, http)
            result = await budgeted.generate(BODY)
            assert result.status_code in (403, 422) and calls == []
            assert budgeted.snapshot()["requests"] == budgeted.snapshot()["tokens"] == 0
            assert budgeted.snapshot()["reservedUsd"] == 0


async def test_provider_header_even_with_matching_digest_cannot_release_a_real_started_attempt(tmp_path):
    async def external(request):
        proof = hmac.digest(GRANT.encode(), b"job-relay/request/1", "sha256").hex()
        return httpx.Response(502, json={"type": "error"}, headers={"x-lw-not-forwarded": proof})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(external), base_url="https://fixed.example"
    ) as upstream:
        budgeted = gate(tmp_path, upstream, trusted=False)
        await budgeted.generate(BODY)
        snapshot = budgeted.snapshot()
        assert snapshot["requests"] == 1 and snapshot["tokens"] is None
        assert snapshot["unknownUsageRequests"] == 1 and snapshot["reservedTokens"] == 120
        assert json.loads((tmp_path / "usage.json").read_bytes())["requests"][0]["dispatchState"] == "started"


async def test_active_stream_uses_one_deadline_and_keeps_known_usage_prefix_and_unspent_reservation(tmp_path):
    closed = []

    class Active(httpx.AsyncByteStream):
        async def __aiter__(self):
            start = {"type": "message_start", "message": {"id": "partial", "usage": {"input_tokens": 20}}}
            yield ("data: " + json.dumps(start) + "\n\n").encode()
            try:
                while True:
                    await asyncio.sleep(0.01)
                    yield b": active\n\n"
            finally:
                closed.append(True)

    async def external(request):
        return httpx.Response(200, stream=Active(), headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(external), base_url="https://fixed.example"
    ) as upstream:
        relay = create_relay(
            upstream,
            credential=KEY,
            grant=GRANT,
            model="fixture-model",
            deadline=time.monotonic() + 0.08,
            owner_alive=lambda: True,
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(relay), base_url="http://relay") as http:
            budgeted = gate(tmp_path, http)
            started = time.monotonic()
            response = await budgeted.generate(BODY)
            assert response.status_code == 502 and time.monotonic() - started < 1
            assert closed == [True]
            snapshot = budgeted.snapshot()
            assert snapshot["tokens"] is None and snapshot["knownTokens"] == 20
            assert snapshot["requests"] == 1 and snapshot["reservedTokens"] == 100
            assert snapshot["failures"] == 1

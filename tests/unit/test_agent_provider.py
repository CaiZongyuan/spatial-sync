"""The actual fixed HTTP transport gates a next call before the external provider."""

import json
import time

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from packages.agents.provider import BudgetedProvider, ProviderLimits, ProviderQuote, install_provider_route
from packages.library.transactions import durable_json


def setup(
    tmp_path,
    *,
    tokens=150,
    usd=10,
    requests=5,
    rates=None,
    mode="json",
    quote_reader=None,
    clock=time.monotonic,
    is_active=lambda: True,
    after_save=None,
):
    external = FastAPI()
    calls = []
    canary = "FAKE_PROVIDER_CREDENTIAL_CANARY_44"

    @external.post("/v1/messages")
    async def fake_provider(request: Request):
        body = await request.json()
        calls.append(body)
        usage = {
            "input_tokens": 20,
            "output_tokens": 10,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
        if mode == "failure":
            return JSONResponse({"error": {"message": canary}}, status_code=429)
        if mode == "zero":
            usage = dict.fromkeys(usage, 0)
        if mode == "partial":
            usage = {"input_tokens": 20, "output_tokens": 5}
        if mode == "overbound":
            usage = usage | {"input_tokens": 150, "output_tokens": 30}
        if mode in ("stream_error", "stream_incomplete"):
            events = [
                {"type": "message_start", "message": {"id": "fixture-message", "usage": {"input_tokens": 20}}}
            ]
            if mode == "stream_error":
                events.append({"type": "error", "error": {"type": "overloaded_error", "message": canary}})
            return Response(
                "".join(f"data: {json.dumps(event)}\n\n" for event in events), media_type="text/event-stream"
            )
        if mode == "stream":
            events = [
                {
                    "type": "message_start",
                    "message": {"id": "fixture-message", "usage": usage | {"output_tokens": 0}},
                },
                {"type": "message_delta", "usage": {"output_tokens": 10}},
                {"type": "message_delta", "usage": {"output_tokens": 10}},
                {"type": "message_stop"},
            ]
            return Response(
                "".join(f"data: {json.dumps(event)}\n\n" for event in events), media_type="text/event-stream"
            )
        return {"id": f"fixture-message-{len(calls)}", "type": "message", "content": [], "usage": usage}

    state = {"requests": []}
    saved = tmp_path / "job-state.json"
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=external), base_url="http://fixture-provider"
    )
    quote = ProviderQuote(
        1,
        {
            key: 1.0
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        }
        if rates is None
        else rates,
    )

    def save():
        durable_json(saved, state)
        if after_save is not None:
            after_save()

    gate = BudgetedProvider(
        "run-fixture",
        "fixture-model",
        ProviderLimits(requests, tokens, usd, 30, 20),
        quote_reader if quote_reader is not None else quote,
        100,
        state,
        save,
        client,
        canary,
        transport_token="TEST_ONLY_OWNED_TRANSPORT",
        clock=clock,
        is_active=is_active,
    )
    app = FastAPI()
    install_provider_route(app, gate)
    return app, gate, client, calls, canary


async def post(app, **overrides):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://owned-transport"
    ) as client:
        return await client.post(
            "/v1/messages",
            headers={"x-api-key": "TEST_ONLY_OWNED_TRANSPORT"},
            json={"model": "fixture-model", "max_tokens": 20, "messages": [], **overrides},
        )


async def test_next_call_reserves_its_bound_before_external_provider_and_clamps_output(tmp_path):
    app, gate, provider, calls, _ = setup(tmp_path)
    try:
        assert (await post(app, max_tokens=500)).status_code == 200
        assert calls[0]["max_tokens"] == 20
        assert (await post(app)).status_code == 200  # actual60 + next120 would exceed150 after this call.
        denied = await post(app)
        assert denied.status_code == 429 and len(calls) == 2
        assert gate.snapshot()["tokens"] == 60 and gate.snapshot()["requests"] == 2
    finally:
        await provider.aclose()


async def test_dollar_boundary_is_checked_before_call_not_after_sdk_soft_budget(tmp_path):
    app, gate, provider, calls, _ = setup(tmp_path, tokens=9999, usd=0.00015)
    try:
        assert (await post(app)).status_code == 200
        assert (await post(app)).status_code == 200
        assert (await post(app)).status_code == 429
        assert len(calls) == 2
        assert gate.snapshot()["llmUsd"] == pytest.approx(0.00006)
    finally:
        await provider.aclose()


async def test_missing_price_cannot_authorize_a_free_call_but_known_zero_usage_and_rate_remain_zero(tmp_path):
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    app, _gate, provider, calls, _ = setup(tmp_path, rates=dict.fromkeys(keys))
    try:
        assert (await post(app)).status_code == 422 and calls == []
    finally:
        await provider.aclose()
    app, gate, provider, calls, _ = setup(tmp_path, rates=dict.fromkeys(keys, 0.0), mode="zero", usd=0)
    try:
        assert (await post(app)).status_code == 200
        assert gate.snapshot()["tokens"] == 0 and gate.snapshot()["llmUsd"] == 0
    finally:
        await provider.aclose()


async def test_failed_provider_preserves_unknown_usage_and_reservation_without_leaking_credential(tmp_path):
    app, gate, provider, calls, canary = setup(tmp_path, mode="failure")
    try:
        result = await post(app)
        assert result.status_code == 429 and canary not in result.text
        actual = gate.snapshot()
        assert actual["requests"] == 1 and actual["failures"] == 1
        assert actual["tokens"] is None and actual["reservedTokens"] == 120
        assert canary not in json.dumps(actual)
        assert (await post(app)).status_code == 429 and len(calls) == 1
    finally:
        await provider.aclose()


async def test_repeated_stream_usage_is_a_highwater_observation_not_an_additional_charge(tmp_path):
    app, gate, provider, calls, _ = setup(tmp_path, mode="stream")
    try:
        assert (await post(app, stream=True)).status_code == 200
        assert gate.snapshot()["tokens"] == 30 and gate.snapshot()["requests"] == 1
        assert gate.snapshot()["llmUsd"] == pytest.approx(0.00003)
    finally:
        await provider.aclose()


async def test_input_override_and_persistence_failure_never_reach_provider(tmp_path):
    app, gate, provider, calls, _ = setup(tmp_path)
    try:
        assert (await post(app, model="another-model")).status_code == 409
        assert calls == []
        (tmp_path / "job-state.json").mkdir()
        assert (await post(app)).status_code == 503
        assert calls == []
        assert (await post(app)).status_code == 503
    finally:
        await provider.aclose()


async def test_forged_actor_and_missing_transport_grant_never_fall_back_to_local_operator(tmp_path):
    app, gate, provider, calls, _ = setup(tmp_path)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://owned-transport"
        ) as client:
            for key in (None, "forged"):
                headers = {"X-Actor": "user:local"}
                if key:
                    headers["x-api-key"] = key
                result = await client.post(
                    "/v1/messages",
                    headers=headers,
                    json={"model": "fixture-model", "max_tokens": 20, "messages": []},
                )
                assert result.status_code == 401
        assert calls == [] and gate.snapshot()["requests"] == 0
    finally:
        await provider.aclose()


async def test_each_request_keeps_its_price_version_and_an_expired_run_cannot_call_again(tmp_path):
    now = [0.0]
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    quotes = [ProviderQuote(1, dict.fromkeys(keys, 1.0))]
    app, gate, provider, calls, _ = setup(
        tmp_path, tokens=9999, quote_reader=lambda: quotes[0], clock=lambda: now[0]
    )
    try:
        assert (await post(app)).status_code == 200
        quotes[0] = ProviderQuote(2, {key: 2.0 for key in quotes[0].rates})
        assert (await post(app)).status_code == 200
        assert gate.snapshot()["llmUsd"] == pytest.approx(0.00009)
        assert gate.snapshot()["priceRevisions"] == [1, 2]
        now[0] = 31
        assert (await post(app)).status_code == 429
        assert len(calls) == 2
    finally:
        await provider.aclose()


async def test_partial_usage_is_known_prefix_plus_unspent_reservation_and_survives_terminal_scope(tmp_path):
    active = [True]
    app, gate, provider, calls, _ = setup(tmp_path, mode="partial", is_active=lambda: active[0])
    try:
        assert (await post(app)).status_code == 200
        actual = gate.snapshot()
        assert actual["tokens"] is None and actual["knownTokens"] == 25
        assert actual["reservedTokens"] == 95
        assert actual["knownLlmUsd"] == pytest.approx(0.000025)
        assert actual["reservedUsd"] == pytest.approx(0.000095)
        active[0] = False
        assert gate.snapshot()["knownTokens"] == 25
        assert (await post(app)).status_code == 403 and len(calls) == 1
    finally:
        await provider.aclose()


async def test_provider_bound_violation_stops_future_calls_without_erasing_actual_use(tmp_path):
    app, gate, provider, calls, _ = setup(tmp_path, mode="overbound", tokens=9999)
    try:
        assert (await post(app)).status_code == 502
        actual = gate.snapshot()
        assert actual["tokens"] == 180 and actual["stopReason"] == "provider_bound_violated"
        assert (await post(app)).status_code == 502 and len(calls) == 1
    finally:
        await provider.aclose()


@pytest.mark.parametrize("changed", ["deadline", "active"])
async def test_durable_reservation_rechecks_authority_at_the_known_unstarted_boundary(tmp_path, changed):
    now, active = [0.0], [True]

    def after_save():
        if changed == "deadline":
            now[0] = 31
        else:
            active[0] = False

    app, gate, provider, calls, _ = setup(
        tmp_path, clock=lambda: now[0], is_active=lambda: active[0], after_save=after_save
    )
    try:
        result = await post(app)
        assert result.status_code == (429 if changed == "deadline" else 403)
        assert calls == []
        actual = gate.snapshot()
        assert actual["requests"] == 0 and actual["failures"] == 0
        assert actual["tokens"] == 0 and actual["llmUsd"] == 0
        assert actual["reservedTokens"] == 0 and actual["notForwardedRequests"] == 1
    finally:
        await provider.aclose()


@pytest.mark.parametrize("mode", ["stream_error", "stream_incomplete"])
async def test_stream_error_is_a_failure_but_unterminated_eof_has_unknown_outcome(tmp_path, mode):
    app, gate, provider, calls, canary = setup(tmp_path, mode=mode)
    try:
        result = await post(app, stream=True)
        assert result.status_code == (200 if mode == "stream_error" else 502)
        assert canary not in result.text
        actual = gate.snapshot()
        assert actual["requests"] == 1
        assert actual["tokens"] is None and actual["knownTokens"] == 20
        assert actual["reservedTokens"] == 100
        assert actual["failures"] == (1 if mode == "stream_error" else None)
        assert actual["unknownOutcomeRequests"] == (0 if mode == "stream_error" else 1)
        assert actual["knownLlmUsd"] == pytest.approx(0.00002)
        assert actual["reservedUsd"] == pytest.approx(0.0001)
        assert (await post(app)).status_code == 429 and len(calls) == 1
    finally:
        await provider.aclose()

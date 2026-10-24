"""Model messages and counters enter the labelled public trace after secret redaction."""

import json

import httpx

from packages.agents.provider import TOKEN_KEYS, BudgetedProvider, ProviderLimits, ProviderQuote


async def test_actual_transport_messages_are_redacted_and_usage_is_the_same_receipt():
    key, transport = "GENERATED_RELAY_GRANT_44", "GENERATED_SDK_GATE_GRANT_44"
    events = []

    async def external(request):
        return httpx.Response(
            200,
            json={
                "type": "message",
                "id": "fixture",
                "content": [{"type": "text", "text": key + transport}],
                "usage": dict(zip(TOKEN_KEYS, (2, 1, 0, 0), strict=True)),
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(external), base_url="http://local-fake"
    ) as upstream:
        gate = BudgetedProvider(
            "job-trace",
            "fixture",
            ProviderLimits(2, 100, 1, 5, 5),
            ProviderQuote(1, dict.fromkeys(TOKEN_KEYS, 1.0)),
            10,
            {},
            lambda: None,
            upstream,
            key,
            transport_token=transport,
            observe=lambda kind, data: events.append({"kind": kind, "data": data}),
        )
        await gate.generate(
            {
                "model": "fixture",
                "max_tokens": 5,
                "messages": [{"role": "user", "content": "Public statement"}],
            }
        )
        assert [event["kind"] for event in events] == ["model.request", "model.response"]
        assert events[0]["data"]["payload"]["messages"][0]["content"] == "Public statement"
        assert events[1]["data"]["usage"]["input_tokens"] == 2 and gate.snapshot()["tokens"] == 3
        assert key not in json.dumps(events) and transport not in json.dumps(events)

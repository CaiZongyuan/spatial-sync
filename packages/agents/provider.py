"""Fixed model transport: reserve a next call, then observe authoritative usage.

The caller owns the Job state and atomic save. This module neither trusts SDK
turn counters nor treats its stop-after-spend option as provider admission.
"""

import asyncio
import hmac
import json
import math
import time
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from packages.info.plans import PlanError

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
MILLION = Decimal(1000000)


@dataclass(frozen=True)
class ProviderLimits:
    requests: int
    tokens: int
    usd: float
    wall_seconds: float
    max_output_tokens: int

    def __post_init__(self):
        if (
            any(
                type(value) is not int or value < 0
                for value in (self.requests, self.tokens, self.max_output_tokens)
            )
            or self.max_output_tokens == 0
            or not math.isfinite(self.usd)
            or self.usd < 0
            or not math.isfinite(self.wall_seconds)
            or self.wall_seconds <= 0
        ):
            raise ValueError("Invalid provider resource limits")


@dataclass(frozen=True)
class ProviderQuote:
    revision: int
    rates: dict[str, float | None]

    def __post_init__(self):
        object.__setattr__(self, "rates", MappingProxyType(dict(self.rates)))

    def bound(self, context, output):
        rates = [self.rates.get(key) for key in TOKEN_KEYS]
        if any(value is None or not math.isfinite(value) or value < 0 for value in rates):
            raise PlanError("agent_price_unavailable", "A complete known price quote is required", status=422)
        input_rate = max(Decimal(str(self.rates[key])) for key in TOKEN_KEYS if key != "output_tokens")
        return (context * input_rate + output * Decimal(str(self.rates["output_tokens"]))) / MILLION

    def cost(self, usage):
        return sum(Decimal(usage.get(key, 0)) * Decimal(str(self.rates[key])) for key in TOKEN_KEYS) / MILLION


def _observe_usage(content, content_type):
    observed, message_id, complete = {}, None, False
    if "text/event-stream" in content_type:
        packets = [
            json.loads(line[5:].strip())
            for line in content.decode().splitlines()
            if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]")
        ]
        complete = any(packet.get("type") == "message_stop" for packet in packets)
        candidates = []
        for packet in packets:
            message = packet.get("message", {})
            if packet.get("type") == "message_start":
                message_id = message.get("id")
                candidates.append(message.get("usage", {}))
            candidates.append(packet.get("usage", {}))
    else:
        packet = json.loads(content)
        message_id, candidates = packet.get("id"), [packet.get("usage", {})]
        complete = packet.get("type") == "message"
    for usage in candidates:
        if not isinstance(usage, dict):
            raise ValueError("Invalid provider usage")
        for key in TOKEN_KEYS:
            if key not in usage:
                continue
            value = usage[key]
            if type(value) is not int or value < 0:
                raise ValueError("Invalid provider counter")
            observed[key] = max(observed.get(key, 0), value)
    return message_id, observed, complete and all(key in observed for key in TOKEN_KEYS)


class BudgetedProvider:
    def __init__(
        self,
        run_id,
        model,
        limits,
        quote,
        context_tokens,
        state,
        save,
        upstream,
        api_key,
        *,
        transport_token,
        is_active=lambda: True,
        clock=time.monotonic,
    ):
        self.run_id, self.model, self.limits = run_id, model, limits
        self.quote, self.context_tokens = quote, context_tokens
        self.state, self.save, self.upstream = state, save, upstream
        self._api_key = api_key
        self._transport_token, self._is_active = transport_token, is_active
        self.state.setdefault("requests", [])
        self._clock, self._started = clock, clock()
        self._lock = asyncio.Lock()
        self._failure = False

    def _available(self):
        if self._failure:
            raise PlanError("agent_usage_unavailable", "Agent accounting could not be committed", status=503)

    def _request_allowed(self):
        self._available()
        if not self._is_active():
            raise PlanError("agent_run_inactive", "The Agent run is not active", status=403)
        if self.state.get("stopReason"):
            raise PlanError("agent_bound_violated", "The provider exceeded its admitted bound", status=502)

    def authenticate(self, value):
        self._request_allowed()
        if not value or not hmac.compare_digest(value.encode(), self._transport_token.encode()):
            raise PlanError(
                "agent_transport_required", "An owned model transport grant is required", status=401
            )

    def _save(self):
        try:
            self.save()
        except (OSError, ValueError, PlanError) as error:
            self._failure = True
            raise PlanError(
                "agent_usage_unavailable", "Agent accounting could not be committed", status=503
            ) from error

    def snapshot(self):
        self._available()
        rows = self.state["requests"]
        unknown = [row for row in rows if not row.get("usageComplete")]
        tokens = sum(sum(row["usage"].values()) for row in rows)
        costs = {
            row["id"]: ProviderQuote(row["quoteRevision"], row["rates"]).cost(row["usage"]) for row in rows
        }
        money = sum(costs.values(), Decimal(0))
        return {
            "requests": len(rows),
            "failures": sum(row["status"] == "failed" for row in rows),
            "tokens": None if unknown else tokens,
            "knownTokens": tokens,
            "llmUsd": None if unknown else float(money),
            "knownLlmUsd": float(money),
            "reservedTokens": sum(max(0, row["boundTokens"] - sum(row["usage"].values())) for row in unknown),
            "reservedUsd": float(
                sum(
                    (max(Decimal(0), Decimal(str(row["boundUsd"])) - costs[row["id"]]) for row in unknown),
                    Decimal(0),
                )
            ),
            "unknownUsageRequests": len(unknown),
            "priceRevisions": sorted({row["quoteRevision"] for row in rows}),
            "stopReason": self.state.get("stopReason"),
        }

    def _reserve(self, payload):
        self._request_allowed()
        if payload.get("model") != self.model:
            raise PlanError("agent_input_mismatch", "The model differs from the admitted Plan", status=409)
        requested = payload.get("max_tokens")
        if type(requested) is not int or requested <= 0:
            raise PlanError("invalid_model_request", "A positive output bound is required", status=422)
        if type(self.context_tokens) is not int or self.context_tokens <= 0:
            raise PlanError("agent_bound_unavailable", "The model context bound is unavailable", status=422)
        remaining_time = self.limits.wall_seconds - (self._clock() - self._started)
        output = min(requested, self.limits.max_output_tokens)
        try:
            quote = self.quote() if callable(self.quote) else self.quote
        except (OSError, ValueError) as error:
            raise PlanError(
                "agent_price_unavailable", "The model price quote is unavailable", status=422
            ) from error
        bound_usd = quote.bound(self.context_tokens, output)
        bound_tokens = self.context_tokens + output
        current = self.snapshot()
        if (
            remaining_time <= 0
            or current["requests"] >= self.limits.requests
            or current["knownTokens"] + current["reservedTokens"] + bound_tokens > self.limits.tokens
            or Decimal(str(current["knownLlmUsd"])) + Decimal(str(current["reservedUsd"])) + bound_usd
            > Decimal(str(self.limits.usd))
        ):
            raise PlanError("agent_budget_exhausted", "The next model request cannot be reserved", status=429)
        record = {
            "id": f"{self.run_id}/request/{len(self.state['requests']) + 1}",
            "at": time.time(),
            "model": self.model,
            "quoteRevision": quote.revision,
            "rates": dict(quote.rates),
            "boundTokens": bound_tokens,
            "boundUsd": float(bound_usd),
            "status": "pending",
            "usage": {},
            "usageComplete": False,
        }
        self.state["requests"].append(record)
        self._save()  # No provider operation before its reservation is durable.
        return record, quote, payload | {"max_tokens": output}, remaining_time

    def _redact(self, value):
        if isinstance(value, str):
            for secret in (self._api_key, self._transport_token):
                if secret:
                    value = value.replace(secret, "<redacted>")
            return value
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        return value

    def _safe_response(self, content, content_type):
        if "text/event-stream" in content_type:
            lines = []
            for line in content.decode().splitlines():
                if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                    line = "data: " + json.dumps(self._redact(json.loads(line[5:])))
                lines.append(line)
            return ("\n".join(lines) + "\n\n").encode()
        return json.dumps(self._redact(json.loads(content))).encode()

    async def generate(self, payload):
        async with self._lock:
            record, quote, body, timeout = self._reserve(payload)
            try:
                response = await self.upstream.post(
                    "/v1/messages",
                    json=body,
                    timeout=timeout,
                    headers={"x-api-key": self._api_key, "anthropic-version": "2023-06-01"},
                )
                content_type = response.headers.get("content-type", "application/json")
                message_id, usage, complete = _observe_usage(response.content, content_type)
                record.update(
                    status="succeeded" if response.is_success else "failed",
                    usage=usage,
                    usageComplete=complete and response.is_success,
                    providerMessageId=self._redact(message_id),
                    statusCode=response.status_code,
                )
                if record["usageComplete"]:
                    record["usd"] = float(quote.cost(usage))
                if sum(usage.values()) > record["boundTokens"] or quote.cost(usage) > Decimal(
                    str(record["boundUsd"])
                ):
                    self.state["stopReason"] = "provider_bound_violated"
                self._save()
                if self.state.get("stopReason"):
                    raise PlanError(
                        "agent_bound_violated", "The provider exceeded its admitted bound", status=502
                    )
                if not response.is_success:
                    return Response(
                        json.dumps(
                            {
                                "type": "error",
                                "error": {
                                    "type": "provider_error",
                                    "message": "Model provider request failed",
                                },
                            }
                        ),
                        status_code=response.status_code,
                        media_type="application/json",
                    )
                return Response(
                    self._safe_response(response.content, content_type),
                    status_code=response.status_code,
                    media_type=content_type.split(";")[0],
                )
            except (httpx.HTTPError, ValueError, UnicodeError) as error:
                record.update(status="failed", usageComplete=False, errorCode="provider_response_unavailable")
                self._save()
                raise PlanError(
                    "agent_provider_unavailable", "Model provider response is unavailable", status=502
                ) from error


def install_provider_route(app, gate):
    @app.exception_handler(PlanError)
    async def rejected(_request, error):
        return JSONResponse(
            {"type": "error", "error": {"type": error.detail["code"], "message": error.detail["message"]}},
            status_code=error.status,
        )

    @app.post("/v1/messages")
    async def messages(request: Request):
        gate.authenticate(request.headers.get("x-api-key", ""))
        try:
            body = await request.json()
        except ValueError:
            raise PlanError("invalid_model_request", "A JSON model request is required", status=422) from None
        if not isinstance(body, dict):
            raise PlanError("invalid_model_request", "A model request object is required", status=422)
        return await gate.generate(body)

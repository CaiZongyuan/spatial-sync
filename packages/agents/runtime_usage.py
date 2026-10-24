"""Normalize trusted SDK result fields; model-authored message text is never a receipt."""

import math
from copy import deepcopy

from packages.info.plans import PlanError

FIELDS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadTokens": "cache_read_input_tokens",
    "cacheWriteTokens": "cache_creation_input_tokens",
}
MODEL_FIELDS = {
    "inputTokens": "inputTokens",
    "outputTokens": "outputTokens",
    "cacheReadTokens": "cacheReadInputTokens",
    "cacheWriteTokens": "cacheCreationInputTokens",
}


def count(value):
    return value if type(value) is int and value >= 0 else None


def amount(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def normalize_runtime_result(case_id, value):
    session = value.get("session_id")
    if not isinstance(session, str) or not session:
        raise PlanError(
            "runtime_usage_invalid", "The typed runtime result has no session identity", status=503
        )
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    models = value.get("model_usage") if isinstance(value.get("model_usage"), dict) else {}
    result = {
        "receiptId": case_id + "/" + session,
        "caseRunId": case_id,
        "sessionId": session,
        "resultId": value.get("uuid"),
        "source": "runtime",
        "modelUsage": deepcopy(models),
    }
    for field, key in FIELDS.items():
        observed = count(usage.get(key))
        if (
            observed is None
            and models
            and all(
                isinstance(model, dict) and count(model.get(MODEL_FIELDS[field])) is not None
                for model in models.values()
            )
        ):
            observed = sum(model[MODEL_FIELDS[field]] for model in models.values())
        result[field] = observed
    cost = amount(value.get("total_cost_usd"))
    if (
        cost is None
        and models
        and all(
            isinstance(model, dict) and amount(model.get("costUSD")) is not None for model in models.values()
        )
    ):
        cost = sum(model["costUSD"] for model in models.values())
    result["llmUsd"] = cost
    result["coverageComplete"] = not value.get("coverageIncomplete", False)
    return result


def merge_runtime_result(receipts, receipt):
    previous = receipts.get(receipt["receiptId"])
    if previous is None:
        receipts[receipt["receiptId"]] = deepcopy(receipt)
        return
    for field in (*FIELDS, "llmUsd"):
        value = receipt[field]
        if value is not None:
            previous[field] = max(previous[field] or 0, value)
    previous["coverageComplete"] = previous["coverageComplete"] and receipt["coverageComplete"]


def runtime_totals(receipts, rows):
    covered = {receipt["caseRunId"] for receipt in receipts.values()}
    uncovered = [
        row
        for row in rows
        if row.get("dispatchState") != "not_forwarded" and row.get("caseRunId") not in covered
    ]
    result = {"costSource": "runtime", "unknownUsageRequests": len(uncovered)}
    unknown = bool(uncovered) or any(not receipt["coverageComplete"] for receipt in receipts.values())
    for field, key in FIELDS.items():
        known = sum(receipt[field] or 0 for receipt in receipts.values())
        known += sum(row["usage"].get(key, 0) for row in uncovered)
        missing = unknown or any(receipt[field] is None for receipt in receipts.values())
        result[field] = None if missing else known
        result["known" + field[0].upper() + field[1:]] = known
    result["knownTokens"] = sum(result["known" + field[0].upper() + field[1:]] for field in FIELDS)
    result["tokens"] = None if any(result[field] is None for field in FIELDS) else result["knownTokens"]
    result["knownLlmUsd"] = sum(receipt["llmUsd"] or 0 for receipt in receipts.values())
    result["llmUsd"] = (
        None
        if unknown or any(receipt["llmUsd"] is None for receipt in receipts.values())
        else result["knownLlmUsd"]
    )
    result["reservedUsd"] = None if result["llmUsd"] is None else 0.0
    result["reservedTokens"] = None if result["tokens"] is None else 0
    return result

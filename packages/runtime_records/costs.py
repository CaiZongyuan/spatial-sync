"""Read-only projection of reported imported costs; no price or token-cost inference."""

from datetime import datetime
from decimal import Decimal

FIELDS = (
    "requests",
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "tokens",
    "llmUsd",
)


def observed_at(record):
    return datetime.fromisoformat(record["importedAt"]).timestamp()


def aggregate_imported(records, window, include_end):
    selected = [
        record
        for record in records
        if window.start <= observed_at(record) < window.end
        or include_end
        and observed_at(record) == window.end
    ]
    result = {
        "failures": None,
        "successRate": None,
        "cacheHitRate": None,
        "timeBasis": "importedAt",
        "runtimeSessionCount": len(selected),
    }
    for field in FIELDS:
        values = [record["usage"][field] for record in selected]
        known = sum((Decimal(str(value)) for value in values if value is not None), Decimal(0))
        known = float(known) if field == "llmUsd" else int(known)
        result[field] = None if any(value is None for value in values) else known
        result["known" + field[0].upper() + field[1:]] = known
    sources = {record["usage"]["costSource"] for record in selected if record["usage"]["llmUsd"] is not None}
    result["costSource"] = (
        "unknown"
        if result["llmUsd"] is None
        else (
            next(iter(sources)) if len(sources) == 1 else "runtime-mixed" if sources else "runtime-reported"
        )
    )
    return result

"""Claude native JSONL normalization with capture/message-owned metering."""

import hashlib
import json
import math
import re
from datetime import UTC, datetime

from packages.info.plans import PlanError

FORMATS = {"claude-code-stream-json", "claude-code-session-jsonl"}
TOKEN_FIELDS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cacheReadTokens": "cache_read_input_tokens",
    "cacheWriteTokens": "cache_creation_input_tokens",
    "tokens": "total_tokens",
}
SECRET = re.compile(
    r"(?i)\b(?:sk-ant-|sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9._~+/-]{8,}"
)
SENSITIVE = {
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "authorization",
    "password",
    "secret",
    "credentials",
    "token",
    "credential",
    "client_secret",
}


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def redacted(value):
    if isinstance(value, str):
        return SECRET.sub("[redacted]", value)
    if isinstance(value, list):
        return [redacted(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in SENSITIVE else redacted(item) for key, item in value.items()
        }
    return value


def number(value, *, integer=False):
    if value is None:
        return None
    if (
        type(value) not in ((int,) if integer else (int, float))
        or value < 0
        or type(value) is int
        and value > 2**63 - 1
        or not math.isfinite(value)
    ):
        raise PlanError(
            "invalid_runtime_usage", "Native usage must be finite nonnegative reported numbers", status=422
        )
    return value


def text(value, *, required=False, limit=4096):
    if value is None and not required:
        return None
    if not isinstance(value, str) or required and not value or limit is not None and len(value) > limit:
        raise PlanError(
            "invalid_runtime_record", "A native identity or text field has an invalid type", status=422
        )
    return value


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC).isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def safe_content(content):
    if isinstance(content, str):
        return redacted(content)
    if not isinstance(content, list):
        return None
    blocks = []
    fields = {
        "text": ("type", "text"),
        "thinking": ("type", "thinking"),
        "tool_use": ("type", "id", "name", "input"),
        "tool_result": ("type", "tool_use_id", "content", "is_error"),
    }
    for block in content:
        if not isinstance(block, dict):
            continue
        selected = fields.get(text(block.get("type"), required=True))
        if block["type"] in {"text", "thinking"}:
            text(block.get("text" if block["type"] == "text" else "thinking"), limit=None)
        if block["type"] == "tool_use":
            text(block.get("id"), required=True)
            text(block.get("name"), required=True)
            if not isinstance(block.get("input"), dict):
                raise PlanError("invalid_runtime_record", "Native tool input must be a mapping", status=422)
        if block["type"] == "tool_result":
            text(block.get("tool_use_id"), required=True)
            if "is_error" in block and type(block["is_error"]) is not bool:
                raise PlanError(
                    "invalid_runtime_record", "Native tool error status must be boolean", status=422
                )
        blocks.append(
            redacted({key: block[key] for key in selected if key in block})
            if selected
            else {"type": str(block.get("type", "unknown")), "contentOmitted": True}
        )
    return blocks


def metric(usage, value=None):
    if usage is not None and not isinstance(usage, dict):
        raise PlanError("invalid_runtime_usage", "Native usage must be a mapping", status=422)
    usage = usage if isinstance(usage, dict) else {}
    value = value or {}
    result = {field: number(usage.get(key), integer=True) for field, key in TOKEN_FIELDS.items()}
    result.update(
        requests=None,
        turns=number(value.get("num_turns"), integer=True),
        llmUsd=number(value.get("total_cost_usd")),
    )
    models = value.get("modelUsage") if isinstance(value.get("modelUsage"), dict) else {}
    estimated = any(
        isinstance(model, dict) and text(model.get("costBasis")) in {"list", "estimate", "unknown"}
        for model in models.values()
    )
    result["costSource"] = (
        "unknown" if result["llmUsd"] is None else ("runtime-estimate" if estimated else "runtime-reported")
    )
    return result


def merge_metric(previous, incoming):
    result = dict(previous or incoming)
    for field in (*TOKEN_FIELDS, "requests", "turns"):
        value = incoming.get(field)
        if value is not None:
            result[field] = max(result.get(field) or 0, value)
    amount = incoming.get("llmUsd")
    if amount is not None and (result.get("llmUsd") is None or amount >= result["llmUsd"]):
        result["llmUsd"] = amount
        result["costSource"] = incoming["costSource"]
    return result


def normalize(format_name, records):
    if format_name not in FORMATS or not records or any(not isinstance(record, dict) for record in records):
        raise PlanError(
            "unsupported_runtime_format", "Select a supported Claude native JSONL capture", status=422
        )
    key = "session_id" if format_name == "claude-code-stream-json" else "sessionId"
    sessions = {text(record[key], required=True) for record in records if key in record}
    if len(sessions) != 1:
        raise PlanError(
            "ambiguous_runtime_session", "Import one native runtime session per capture", status=422
        )
    native_session = next(iter(sessions))
    initial = [
        record for record in records if record.get("type") == "system" and record.get("subtype") == "init"
    ]
    if format_name == "claude-code-stream-json" and (len(initial) != 1 or not initial[0].get("uuid")):
        raise PlanError(
            "ambiguous_runtime_capture",
            "Stream JSON requires its single native system/init identity",
            status=422,
        )
    capture = text(initial[0]["uuid"], required=True) if initial else native_session
    result_ids = {
        text(record.get("uuid"), required=True) for record in records if record.get("type") == "result"
    }
    if len(result_ids) > 1 or None in result_ids:
        raise PlanError(
            "unsupported_runtime_counter_scope",
            "Split captures with distinct result totals before importing",
            status=422,
        )
    if format_name == "claude-code-stream-json":
        final = next((index for index, record in enumerate(records) if record.get("type") == "result"), None)
        if final is not None and any(
            record.get("type") in {"assistant", "user"} for record in records[final + 1 :]
        ):
            raise PlanError(
                "unsupported_runtime_counter_scope",
                "Split post-result conversation captures before importing",
                status=422,
            )
    events, receipts = [], {}
    for record in records:
        native_type = text(record.get("type"), required=True)
        if native_type not in {"assistant", "user", "system", "result", "rate_limit_event"}:
            continue
        native_id = text(record.get("uuid"), required=True)
        if native_type == "result" and "is_error" in record and type(record["is_error"]) is not bool:
            raise PlanError(
                "invalid_runtime_record", "Native result error status must be boolean", status=422
            )
        if native_type in {"assistant", "user"} and not isinstance(record.get("message"), dict):
            raise PlanError("invalid_runtime_record", "A native message mapping is required", status=422)
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        content = safe_content(message.get("content"))
        if native_type == "result":
            content = redacted(record.get("result")) if isinstance(record.get("result"), str) else None
        tool = isinstance(content, list) and any(
            block.get("type") in {"tool_use", "tool_result"} for block in content
        )
        event = {
            "id": native_id,
            "nativeId": native_id,
            "nativeType": native_type,
            "kind": "result"
            if native_type == "result"
            else "tool"
            if tool
            else "message"
            if message
            else "metadata",
            "role": text(message.get("role")),
            "timestamp": timestamp(record.get("timestamp")),
            "parentId": text(record.get("parentUuid")),
            "content": content,
            "model": text(message.get("model"))
            or (text(record.get("model")) if native_type == "system" else None),
            "messageId": text(message.get("id")),
            "subtype": text(record.get("subtype")),
        }
        event["contentHash"] = digest(event)
        events.append(event)
        if native_type == "assistant":
            if not message.get("id") and message.get("usage"):
                raise PlanError(
                    "runtime_usage_scope_unknown",
                    "Assistant usage requires its native message ID",
                    status=422,
                )
            if message.get("id"):
                identity = "message:" + text(message["id"], required=True)
                receipts[identity] = merge_metric(receipts.get(identity), metric(message.get("usage")))
        if native_type == "result":
            identity = "capture:" + capture
            receipts[identity] = merge_metric(receipts.get(identity), metric(record.get("usage"), record))
    if not events:
        raise PlanError("runtime_records_empty", "No supported native runtime records were found", status=422)
    return {
        "runtime": "claude-code",
        "format": format_name,
        "nativeSessionId": native_session,
        "captureId": capture,
        "runtimeVersion": text(initial[0].get("claude_code_version")) if initial else None,
        "events": events,
        "receipts": receipts,
        "models": sorted(
            {event["model"] for event in events if event["model"] is not None}
            | {
                text(model, required=True)
                for record in records
                if record.get("type") == "result" and isinstance(record.get("modelUsage"), dict)
                for model in record["modelUsage"]
            }
        ),
        "status": "error"
        if any(record.get("is_error") for record in records if record.get("type") == "result")
        else "completed"
        if result_ids
        else "observed",
    }


def usage_summary(receipts):
    capture = [value for key, value in receipts.items() if key.startswith("capture:")]
    selected = capture or list(receipts.values())
    result = {
        field: None
        if not selected or any(value.get(field) is None for value in selected)
        else sum(value[field] for value in selected)
        for field in (*TOKEN_FIELDS, "requests", "turns", "llmUsd")
    }
    costs = {value["costSource"] for value in selected if value.get("llmUsd") is not None}
    result.update(
        source="runtime-import",
        scope="capture" if capture else "messages" if selected else "unknown",
        basis="runtime-result" if capture else "runtime-messages",
        costSource="unknown"
        if result["llmUsd"] is None
        else "runtime-estimate"
        if "runtime-estimate" in costs
        else "runtime-reported",
    )
    present = [result[field] is not None for field in TOKEN_FIELDS]
    result["coverage"] = {
        "tokens": "reported" if all(present) else "partial" if any(present) else "unknown",
        "cost": "reported" if result["llmUsd"] is not None else "unknown",
        "requests": "reported" if result["requests"] is not None else "unknown",
    }
    return result

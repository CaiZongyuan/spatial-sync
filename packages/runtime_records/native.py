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
    if type(value) not in ((int,) if integer else (int, float)) or not math.isfinite(value) or value < 0:
        return None
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
        selected = fields.get(block.get("type"))
        blocks.append(
            redacted({key: block[key] for key in selected if key in block})
            if selected
            else {"type": str(block.get("type", "unknown")), "contentOmitted": True}
        )
    return blocks


def metric(usage, value=None):
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
        isinstance(model, dict) and model.get("costBasis") in {"list", "estimate", "unknown"}
        for model in models.values()
    )
    result["costSource"] = (
        "unknown" if result["llmUsd"] is None else ("runtime-estimate" if estimated else "runtime-reported")
    )
    return result


def merge_metric(previous, incoming):
    result = dict(previous or incoming)
    for field in (*TOKEN_FIELDS, "requests", "turns", "llmUsd"):
        value = incoming.get(field)
        if value is not None:
            result[field] = max(result.get(field) or 0, value)
    if incoming.get("costSource") != "unknown":
        result["costSource"] = incoming["costSource"]
    return result


def normalize(format_name, records):
    if format_name not in FORMATS or not records or any(not isinstance(record, dict) for record in records):
        raise PlanError(
            "unsupported_runtime_format", "Select a supported Claude native JSONL capture", status=422
        )
    key = "session_id" if format_name == "claude-code-stream-json" else "sessionId"
    sessions = {record[key] for record in records if isinstance(record.get(key), str) and record[key]}
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
    capture = initial[0]["uuid"] if initial else native_session
    result_ids = {record.get("uuid") for record in records if record.get("type") == "result"}
    if len(result_ids) > 1 or None in result_ids:
        raise PlanError(
            "unsupported_runtime_counter_scope",
            "Split captures with distinct result totals before importing",
            status=422,
        )
    events, receipts = [], {}
    for record in records:
        native_type = record.get("type")
        if native_type not in {"assistant", "user", "system", "result", "rate_limit_event"}:
            continue
        native_id = record.get("uuid")
        if not isinstance(native_id, str) or not native_id:
            raise PlanError("runtime_event_identity_required", "A native event UUID is required", status=422)
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
            "role": message.get("role"),
            "timestamp": timestamp(record.get("timestamp")),
            "parentId": record.get("parentUuid"),
            "content": content,
            "model": message.get("model"),
            "messageId": message.get("id"),
            "subtype": record.get("subtype"),
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
                identity = "message:" + message["id"]
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
        "runtimeVersion": initial[0].get("claude_code_version") if initial else None,
        "events": events,
        "receipts": receipts,
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

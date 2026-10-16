"""Strict JSON encoding shared by the gateway read model and global fanout."""

import json

import orjson


def encode(model) -> str:
    data = model.model_dump(mode="json", exclude_none=True) if hasattr(model, "model_dump") else model
    try:
        return orjson.dumps(data).decode("utf-8")
    except TypeError:
        # Rejected untrusted JSON may contain integers outside orjson's 64-bit
        # range. Keep their exact audit values and JSON's finite-number contract.
        return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

"""Compile the supported JSON Schema subset once at Catalog loading time."""

import math
import re
from copy import deepcopy
from typing import Annotated, Any

from pydantic import BeforeValidator, TypeAdapter, WithJsonSchema

KEYWORDS = {
    "type", "enum", "const", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "minLength", "maxLength", "pattern", "items", "minItems", "maxItems", "properties", "required",
}
TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}


def json_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(json_equal(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(json_equal(a, b) for a, b in zip(left, right))
    return left == right


def compile_schema(schema: dict) -> TypeAdapter:
    exported = deepcopy(schema)

    def compile_node(node, path):
        if not isinstance(node, dict) or set(node) - KEYWORDS:
            raise ValueError(f"Unsupported schema keywords at {path}: {set(node) - KEYWORDS}")
        kinds = node.get("type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not isinstance(kinds, list) or any(kind not in TYPES for kind in kinds) or len(kinds) != len(set(kinds)):
            raise ValueError(f"Invalid schema type at {path}")
        if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
            raise ValueError(f"enum must be a nonempty array at {path}")
        for key in ("minLength", "maxLength", "minItems", "maxItems"):
            if key in node and (type(node[key]) is not int or node[key] < 0):
                raise ValueError(f"{key} must be a nonnegative integer at {path}")
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if key in node and (type(node[key]) not in (int, float) or not math.isfinite(node[key])):
                raise ValueError(f"{key} must be finite at {path}")
        pattern = re.compile(node["pattern"]) if "pattern" in node else None
        properties = {key: compile_node(value, f"{path}.{key}") for key, value in node.get("properties", {}).items()}
        required = node.get("required", [])
        if (not isinstance(required, list) or any(not isinstance(k, str) for k in required)
                or len(required) != len(set(required)) or not set(required) <= properties.keys()):
            raise ValueError(f"required must name declared properties at {path}")
        item = compile_node(node["items"], f"{path}[]") if "items" in node else None

        def validate(value):
            actual = (
                "null" if value is None else "boolean" if type(value) is bool
                else "integer" if type(value) is int else "number" if type(value) is float
                else "string" if isinstance(value, str) else "array" if isinstance(value, list)
                else "object" if isinstance(value, dict) else "invalid"
            )
            if actual == "invalid" or (kinds and actual not in kinds and not (actual == "integer" and "number" in kinds)):
                raise ValueError(f"{path}: expected {kinds}, got {actual}")
            if "enum" in node and not any(json_equal(value, choice) for choice in node["enum"]):
                raise ValueError(f"{path}: value is outside enum")
            if "const" in node and not json_equal(value, node["const"]):
                raise ValueError(f"{path}: value does not match const")
            if actual in ("integer", "number"):
                if not math.isfinite(value):
                    raise ValueError(f"{path}: number must be finite")
                for name, passes in (
                    ("minimum", lambda limit: value >= limit), ("maximum", lambda limit: value <= limit),
                    ("exclusiveMinimum", lambda limit: value > limit), ("exclusiveMaximum", lambda limit: value < limit),
                ):
                    if name in node and not passes(node[name]):
                        raise ValueError(f"{path}: violates {name} {node[name]}")
            if actual == "string":
                if len(value) < node.get("minLength", 0) or len(value) > node.get("maxLength", math.inf):
                    raise ValueError(f"{path}: string length is outside limits")
                if pattern and not pattern.search(value):
                    raise ValueError(f"{path}: string does not match pattern")
            if actual == "array":
                if len(value) < node.get("minItems", 0) or len(value) > node.get("maxItems", math.inf):
                    raise ValueError(f"{path}: array length is outside limits")
                if item:
                    for element in value:
                        item(element)
            if actual == "object":
                if set(value) - properties.keys() or not set(required) <= value.keys():
                    raise ValueError(f"{path}: unknown or missing required properties")
                for key, element in value.items():
                    properties[key](element)
            return value

        return validate

    validator = compile_node(exported, "$")

    def add_defaults(node):
        if node.get("type") == "object" or "object" in node.get("type", []):
            node["additionalProperties"] = False
        for child in node.get("properties", {}).values():
            add_defaults(child)
        if "items" in node:
            add_defaults(node["items"])

    add_defaults(exported)
    return TypeAdapter(Annotated[Any, BeforeValidator(validator), WithJsonSchema(exported)])

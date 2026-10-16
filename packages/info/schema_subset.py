"""Compile the supported JSON Schema subset once at Catalog loading time."""

import math
import operator
import re
from copy import deepcopy
from typing import Annotated, Any

from pydantic import BeforeValidator, TypeAdapter, WithJsonSchema

KEYWORDS = {
    "type",
    "enum",
    "const",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "items",
    "minItems",
    "maxItems",
    "properties",
    "required",
}
TYPES = {"null", "boolean", "integer", "number", "string", "array", "object"}


def _portable_pattern(pattern):
    """Compile a guarded ECMA Unicode pattern without a JavaScript runtime.

    Supports Unicode literals/classes, dot, ASCII digit/word and ECMA whitespace
    shorthands, ordinary/noncapturing groups, lookahead, quantifiers and anchors.
    Flags, named groups, lookbehind, backreferences and atomic/possessive forms
    are rejected at loading. Matching follows JSON Schema consumers using `u`.
    """
    if not isinstance(pattern, str) or any(0xD800 <= ord(char) <= 0xDFFF for char in pattern):
        raise ValueError("pattern must be portable Unicode regex text")
    whitespace = r"\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"

    def escaped(index, in_class=False):
        if index >= len(pattern):
            raise ValueError("Incomplete escape in portable pattern")
        char = pattern[index]
        index += 1
        if char in "dDwW":
            return "\\" + char, None, index
        if char in "sS":
            return "[" + ("^" if char == "S" else "") + whitespace + "]", None, index
        if char in "bB" and not in_class:
            # Python 3.13's \B excludes empty input; ECMA defines it as the
            # logical complement of \b, including that empty-input position.
            return (r"\b" if char == "b" else r"(?!\b)"), None, index
        if char in "nrtfv" or (char == "b" and in_class):
            value = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\v", "b": "\b"}[char]
            return re.escape(value), value, index
        if char in "xu":
            width = 2 if char == "x" else 4
            if char == "u" and pattern[index : index + 1] == "{":
                match = re.match(r"\{([0-9a-fA-F]+)\}", pattern[index:])
                if match is None:
                    raise ValueError("Invalid Unicode escape in portable pattern")
                number, index = int(match.group(1), 16), index + len(match.group())
            else:
                digits = pattern[index : index + width]
                if len(digits) != width or not re.fullmatch(r"[0-9a-fA-F]+", digits):
                    raise ValueError("Invalid character escape in portable pattern")
                number, index = int(digits, 16), index + width
            if number > 0x10FFFF or 0xD800 <= number <= 0xDFFF:
                raise ValueError("Use Unicode codepoint escapes, not surrogate units, in portable patterns")
            value = chr(number)
            return re.escape(value), value, index
        if char in r"\.^$*+?{}[]()|/" or (char == "-" and in_class):
            return re.escape(char), char, index
        raise ValueError(f"Unsupported escape in portable pattern: \\{char}")

    def class_atom(index):
        if pattern[index] == "\\":
            return escaped(index + 1, True)
        value = pattern[index]
        return re.escape(value), value, index + 1

    def character_class(index):
        negative = pattern[index : index + 1] == "^"
        index += int(negative)
        atoms = []
        while index < len(pattern) and pattern[index] != "]":
            atom, literal, index = class_atom(index)
            if pattern[index : index + 1] == "-" and index + 1 < len(pattern) and pattern[index + 1] != "]":
                end_atom, end_literal, index = class_atom(index + 1)
                if literal is None or end_literal is None or ord(literal) > ord(end_literal):
                    raise ValueError("Invalid character range in portable pattern")
                atom = "[" + atom + "-" + end_atom + "]"
            atoms.append(atom)
        if index >= len(pattern):
            raise ValueError("Unclosed character class in portable pattern")
        # Every atom consumes one Unicode codepoint. A complemented union can
        # therefore use a generated lookahead followed by any one codepoint.
        union = "(?:" + "|".join(atoms) + ")" if atoms else r"(?!)"
        return ("(?!" + union + r")[\s\S]" if negative else union), index + 1

    translated = []
    quantified = False
    assertion = False
    groups = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            atom, _, index = escaped(index + 1)
            translated.append(atom)
            quantified = False
            assertion = atom in (r"\b", r"(?!\b)")
            continue
        if char == "[":
            atom, index = character_class(index + 1)
            translated.append(atom)
            quantified = False
            assertion = False
            continue
        if char == ".":
            translated.append(r"[^\n\r\u2028\u2029]")
            quantified = False
            assertion = False
        elif char == "(" and pattern[index : index + 2] == "(?":
            prefix = pattern[index : index + 3]
            if prefix not in ("(?:", "(?=", "(?!"):
                raise ValueError("Unsupported group or flags in portable pattern")
            translated.append(prefix)
            groups.append(prefix in ("(?=", "(?!"))
            index += 2
            quantified = False
            assertion = False
        elif char == "(":
            groups.append(False)
            translated.append(char)
            quantified = assertion = False
        elif char == ")":
            if not groups:
                raise ValueError("Unmatched closing group in portable pattern")
            assertion = groups.pop()
            translated.append(char)
            quantified = False
        elif char == "{":
            if assertion:
                raise ValueError("Unicode portable patterns cannot quantify assertions")
            match = re.match(r"\{[0-9]+(?:,[0-9]*)?\}", pattern[index:])
            if match is None:
                raise ValueError("Escape literal braces in portable patterns")
            translated.append(match.group())
            index += len(match.group()) - 1
            quantified = True
        elif char in "]}":
            raise ValueError("Escape literal closing brackets in portable patterns")
        elif char in "*+?":
            if assertion:
                raise ValueError("Unicode portable patterns cannot quantify assertions")
            if quantified and char == "+":
                raise ValueError("Possessive quantifiers are outside the portable pattern subset")
            translated.append(char)
            quantified = True
        else:
            # JavaScript's non-multiline $ requires the actual input end.
            translated.append(r"\Z" if char == "$" else char)
            quantified = False
            assertion = char in "^$"
        index += 1
    try:
        return re.compile("".join(translated), re.ASCII)
    except re.error as exc:
        raise ValueError(f"Invalid portable pattern: {exc}") from exc


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
        if not isinstance(node, dict):
            raise ValueError(f"Schema must be an object at {path}")
        if set(node) - KEYWORDS:
            raise ValueError(f"Unsupported schema keywords at {path}: {set(node) - KEYWORDS}")
        kinds = node.get("type", [])
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if (
            not isinstance(kinds, list)
            or ("type" in node and not kinds)
            or any(kind not in TYPES for kind in kinds)
            or len(kinds) != len(set(kinds))
        ):
            raise ValueError(f"Invalid schema type at {path}")
        if "enum" in node and (not isinstance(node["enum"], list) or not node["enum"]):
            raise ValueError(f"enum must be a nonempty array at {path}")
        for key in ("minLength", "maxLength", "minItems", "maxItems"):
            if key in node and (type(node[key]) is not int or node[key] < 0):
                raise ValueError(f"{key} must be a nonnegative integer at {path}")
        for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
            if key in node and (
                type(node[key]) not in (int, float)
                or (type(node[key]) is float and not math.isfinite(node[key]))
            ):
                raise ValueError(f"{key} must be finite at {path}")
        pattern = _portable_pattern(node["pattern"]) if "pattern" in node else None
        properties = {
            key: compile_node(value, f"{path}.{key}") for key, value in node.get("properties", {}).items()
        }
        required = node.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(k, str) for k in required)
            or len(required) != len(set(required))
            or not set(required) <= properties.keys()
        ):
            raise ValueError(f"required must name declared properties at {path}")
        item = compile_node(node["items"], f"{path}[]") if "items" in node else None
        bounds = tuple(
            (name, compare, node[name])
            for name, compare in (
                ("minimum", operator.ge),
                ("maximum", operator.le),
                ("exclusiveMinimum", operator.gt),
                ("exclusiveMaximum", operator.lt),
            )
            if name in node
        )

        def validate(value):
            actual = (
                "null"
                if value is None
                else "boolean"
                if type(value) is bool
                else "integer"
                if type(value) is int
                else "number"
                if type(value) is float
                else "string"
                if isinstance(value, str)
                else "array"
                if isinstance(value, list)
                else "object"
                if isinstance(value, dict)
                else "invalid"
            )
            integral = actual == "number" and math.isfinite(value) and value.is_integer()
            if actual == "invalid" or (
                kinds
                and actual not in kinds
                and not (actual == "integer" and "number" in kinds)
                and not (integral and "integer" in kinds)
            ):
                raise ValueError(f"{path}: expected {kinds}, got {actual}")
            if "enum" in node and not any(json_equal(value, choice) for choice in node["enum"]):
                raise ValueError(f"{path}: value is outside enum")
            if "const" in node and not json_equal(value, node["const"]):
                raise ValueError(f"{path}: value does not match const")
            if actual in ("integer", "number"):
                if type(value) is float and not math.isfinite(value):
                    raise ValueError(f"{path}: number must be finite")
                for name, compare, limit in bounds:
                    if not compare(value, limit):
                        raise ValueError(f"{path}: violates {name} {limit}")
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
            if actual == "object" and ("object" in kinds or "properties" in node):
                if set(value) - properties.keys() or not set(required) <= value.keys():
                    raise ValueError(f"{path}: unknown or missing required properties")
                for key, element in value.items():
                    properties[key](element)
            return value

        return validate

    validator = compile_node(exported, "$")

    def add_defaults(node):
        if node.get("type") == "object" or "object" in node.get("type", []) or "properties" in node:
            node["additionalProperties"] = False
        for child in node.get("properties", {}).values():
            add_defaults(child)
        if "items" in node:
            add_defaults(node["items"])

    add_defaults(exported)
    return TypeAdapter(Annotated[Any, BeforeValidator(validator), WithJsonSchema(exported)])

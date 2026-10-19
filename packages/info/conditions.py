"""Compiled all-of conditions shared by gateway and device hosts."""

from copy import deepcopy
from operator import ge, gt, le, lt

from .schema_subset import compile_schema, json_equal

OPERATORS = {"equals", "notEquals", "in", "notIn", "lt", "lte", "gt", "gte", "isNull", "notNull", "stale"}
UNKNOWN = object()


def _test(condition):
    keys = set(condition) & OPERATORS
    if (
        len(keys) != 1
        or "property" not in condition
        or set(condition) - OPERATORS - {"property", "entity", "message"}
    ):
        raise ValueError("Condition requires one supported operator and a property")
    op = next(iter(keys))
    expected = condition[op]
    if op in ("isNull", "notNull") and expected is not True:
        raise ValueError("Null operators require true")
    if op in ("in", "notIn") and not isinstance(expected, list):
        raise ValueError("Membership operators require an array")
    if op in ("lt", "lte", "gt", "gte") and type(expected) not in (int, float):
        raise ValueError("Ordered operators require a number")
    if op == "stale" and type(expected) is not bool:
        raise ValueError("Stale operator requires a boolean")

    def compare(value, expired=False):
        if value is UNKNOWN:
            return False
        if op == "stale":
            return expired is expected
        if expired:
            return False
        if op == "equals":
            return json_equal(value, expected)
        if op == "notEquals":
            return not json_equal(value, expected)
        if op in ("in", "notIn"):
            contained = any(json_equal(value, candidate) for candidate in expected)
            return contained if op == "in" else not contained
        if op == "isNull":
            return value is None
        if op == "notNull":
            return value is not None
        return type(value) in (int, float) and {"lt": lt, "lte": le, "gt": gt, "gte": ge}[op](value, expected)

    return compare


class CompiledConditions(list):
    def __init__(self, conditions):
        super().__init__(deepcopy(conditions))
        self.tests = [_test(condition) for condition in self]


def validate_conditions(conditions, entity_key, property_schema, *, allow_cross_entity=True):
    compiled = CompiledConditions(conditions)
    for condition in compiled:
        target = condition.get("entity", entity_key)
        if not allow_cross_entity and "entity" in condition:
            raise ValueError("Reusable type conditions cannot reference another entity")
        schema = property_schema(target, condition["property"])
        op = next(iter(set(condition) & OPERATORS))
        if op in ("lt", "lte", "gt", "gte"):
            kinds = schema.get("type", [])
            kinds = [kinds] if isinstance(kinds, str) else kinds
            if not set(kinds) & {"number", "integer"}:
                raise ValueError("Ordered condition requires a numeric property")
        elif op not in ("isNull", "notNull", "stale"):
            adapter = compile_schema(schema)
            for expected in condition[op] if op in ("in", "notIn") else [condition[op]]:
                adapter.validate_python(expected)
    return compiled


def evaluate_conditions(conditions, entity_key, values, metadata=None):
    compiled = conditions if isinstance(conditions, CompiledConditions) else CompiledConditions(conditions)
    failed = []
    for condition, compare in zip(compiled, compiled.tests, strict=True):
        target, prop = condition.get("entity", entity_key), condition["property"]
        actual = values.get(target, {}).get(prop, UNKNOWN)
        meta = (metadata or {}).get(target, {}).get(prop)
        expired = meta.get("stale", False) if isinstance(meta, dict) else getattr(meta, "stale", False)
        if not compare(actual, expired):
            stale_reading = expired and "stale" not in condition
            failure = {
                "condition": dict(condition),
                "actual": "unknown" if actual is UNKNOWN or stale_reading else deepcopy(actual),
                "message": "读数已过期" if stale_reading else condition.get(
                    "message", f"Condition on {condition['property']} is not satisfied"
                ),
            }
            if stale_reading:
                failure["reason"] = "stale_reading"
            failed.append(failure)
    return failed

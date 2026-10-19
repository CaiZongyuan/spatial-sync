"""Compiled all-of conditions shared by gateway and device hosts."""

from copy import deepcopy
from math import isfinite
from operator import ge, gt, le, lt

from .schema_subset import compile_schema, json_equal

OPERATORS = {
    "equals",
    "notEquals",
    "in",
    "notIn",
    "lt",
    "lte",
    "gt",
    "gte",
    "isNull",
    "notNull",
    "stale",
    "deviatesFrom",
}
UNKNOWN = object()


def _test(condition):
    keys = set(condition) & OPERATORS
    if (
        len(keys) != 1
        or "property" not in condition
        or set(condition) - OPERATORS - {"property", "entity", "message", "by"}
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
    if op == "deviatesFrom":
        threshold = condition.get("by")
        if not isinstance(expected, str) or not expected:
            raise ValueError("Deviation requires a reference property")
        if type(threshold) not in (int, float) or not isfinite(threshold) or threshold < 0:
            raise ValueError("Deviation threshold must be a finite nonnegative number")
    elif "by" in condition:
        raise ValueError("Only deviation conditions accept by")

    def compare(value, expired=False, reference=UNKNOWN, reference_expired=False):
        if value is UNKNOWN:
            return UNKNOWN
        if op == "stale":
            return expired is expected
        if expired:
            return UNKNOWN
        if op == "deviatesFrom":
            if reference_expired or any(
                type(item) not in (int, float) or not isfinite(item) for item in (value, reference)
            ):
                return UNKNOWN
            return abs(value - reference) > threshold
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
        def atoms(items):
            for condition in items:
                if "all" in condition:
                    if (
                        set(condition) != {"all"}
                        or not isinstance(condition["all"], list)
                        or not condition["all"]
                    ):
                        raise ValueError("All requires a nonempty list of conditions")
                    yield from atoms(condition["all"])
                else:
                    yield condition

        super().__init__(deepcopy(list(atoms(conditions))))
        self.tests = [_test(condition) for condition in self]

    def results(self, entity_key, values, metadata):
        for condition, compare in zip(self, self.tests, strict=True):
            target, prop = condition.get("entity", entity_key), condition["property"]
            entity = values.get(target, {})
            meta = (metadata or {}).get(target, {})

            def stale(name):
                item = meta.get(name)
                return item.get("stale", False) if isinstance(item, dict) else getattr(item, "stale", False)

            actual = entity.get(prop, UNKNOWN)
            reference = condition.get("deviatesFrom")
            expired, reference_expired = stale(prop), stale(reference) if reference else False
            result = compare(actual, expired, entity.get(reference, UNKNOWN), reference_expired)
            yield condition, actual, expired or reference_expired, result


def validate_conditions(conditions, entity_key, property_schema, *, allow_cross_entity=True):
    compiled = CompiledConditions(conditions)
    for condition in compiled:
        target = condition.get("entity", entity_key)
        if not allow_cross_entity and "entity" in condition:
            raise ValueError("Reusable type conditions cannot reference another entity")
        schema = property_schema(target, condition["property"])
        op = next(iter(set(condition) & OPERATORS))
        if op in ("lt", "lte", "gt", "gte", "deviatesFrom"):
            schemas = [schema]
            if op == "deviatesFrom":
                schemas.append(property_schema(target, condition[op]))
            for candidate in schemas:
                kinds = candidate.get("type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if not set(kinds) & {"number", "integer"}:
                    raise ValueError("Ordered condition requires numeric properties")
        elif op not in ("isNull", "notNull", "stale"):
            adapter = compile_schema(schema)
            for expected in condition[op] if op in ("in", "notIn") else [condition[op]]:
                adapter.validate_python(expected)
    return compiled


def evaluate_conditions(conditions, entity_key, values, metadata=None):
    compiled = conditions if isinstance(conditions, CompiledConditions) else CompiledConditions(conditions)
    failed = []
    for condition, actual, expired, result in compiled.results(entity_key, values, metadata):
        if result is not True:
            stale_reading = expired and "stale" not in condition
            failure = {
                "condition": dict(condition),
                "actual": "unknown" if result is UNKNOWN or stale_reading else deepcopy(actual),
                "message": "读数已过期"
                if stale_reading
                else condition.get("message", f"Condition on {condition['property']} is not satisfied"),
            }
            if stale_reading:
                failure["reason"] = "stale_reading"
            failed.append(failure)
    return failed


def condition_state(conditions, entity_key, values, metadata=None):
    """True/False/None for a known condition or an unavailable observation."""
    compiled = conditions if isinstance(conditions, CompiledConditions) else CompiledConditions(conditions)
    results = [item[3] for item in compiled.results(entity_key, values, metadata)]
    if any(item is False for item in results):
        return False
    if any(item is UNKNOWN for item in results):
        return None
    return True

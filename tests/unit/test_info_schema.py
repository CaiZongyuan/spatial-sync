import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.info.schema_subset import compile_schema


def test_compiled_schema_validates_strict_nested_values_and_exports_standard_schema():
    adapter = compile_schema(
        {
            "type": "object",
            "required": ["rpm", "slots"],
            "properties": {
                "rpm": {"type": "number", "minimum": 500, "maximum": 15000},
                "slots": {"type": "array", "items": {"type": ["string", "null"]}, "minItems": 2},
            },
        }
    )
    value = {"rpm": 3000, "slots": ["tube1", None]}
    assert adapter.validate_python(value) == value
    assert adapter.json_schema()["additionalProperties"] is False
    for invalid in [
        dict(value, rpm="3000"),
        dict(value, rpm=True),
        dict(value, extra=1),
        dict(value, slots=["tube1"]),
        dict(value, rpm=15001),
    ]:
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)


def test_standard_schema_agrees_on_integral_numbers_and_inferred_objects():
    integer = compile_schema({"type": "integer"})
    assert integer.validate_python(1.0) == 1
    with pytest.raises(ValidationError):
        integer.validate_python(1.5)
    inferred = compile_schema({"properties": {"name": {"type": "string"}}})
    assert inferred.json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError):
        inferred.validate_python({"extra": "x"})
    unconstrained = compile_schema({})
    assert unconstrained.validate_python({"extra": "x"}) == {"extra": "x"}
    assert unconstrained.json_schema() == {}


@pytest.mark.parametrize(
    "schema, valid, invalid",
    [
        (
            {"type": ["string", "null"], "pattern": "^A[0-9]+$", "minLength": 2, "maxLength": 4},
            [None, "A1"],
            ["B1", "A1234", "A"],
        ),
        (
            {
                "type": "array",
                "items": {"type": "number", "exclusiveMinimum": 0, "maximum": 3},
                "minItems": 1,
                "maxItems": 2,
            },
            [[1], [1, 3]],
            [[], [0], [4], [1, 2, 3]],
        ),
        ({"enum": [False, "idle"]}, [False, "idle"], [0, True, "running"]),
        ({"const": {"nested": [True, None]}}, [{"nested": [True, None]}], [{"nested": [1, None]}]),
    ],
)
def test_schema_subset_constraint_examples(schema, valid, invalid):
    adapter = compile_schema(schema)
    for value in valid:
        assert adapter.validate_python(value) == value
    for value in invalid:
        with pytest.raises(ValidationError):
            adapter.validate_python(value)


@pytest.mark.parametrize("schema", [{"anyOf": []}, {"$ref": "#/other"}, {"type": "float"}, {"type": []}])
def test_schema_rejects_unsupported_or_invalid_definitions_at_load(schema):
    with pytest.raises(ValueError):
        compile_schema(schema)


def test_portable_patterns_match_shared_standard_schema_examples():
    cases = json.loads(Path("tests/fixtures/schema-patterns.json").read_text(encoding="utf-8"))
    for case in cases:
        if not case["accepted"]:
            with pytest.raises(ValueError, match="portable"):
                compile_schema(case["schema"])
            continue
        adapter = compile_schema(case["schema"])
        assert adapter.json_schema() == case["schema"], case["name"]
        for value in case["valid"]:
            assert adapter.validate_python(value) == value, case["name"]
        for value in case["invalid"]:
            with pytest.raises(ValidationError):
                adapter.validate_python(value)


def test_large_json_integer_yields_schema_result_or_validation_error_without_float_overflow():
    huge = 10**1000
    assert compile_schema({"type": "integer"}).validate_python(huge) == huge
    assert compile_schema({"type": "number", "minimum": huge}).validate_python(huge) == huge
    with pytest.raises(ValidationError):
        compile_schema({"type": "number", "maximum": 15000}).validate_python(huge)


def test_numeric_bounds_keep_error_order_original_values_and_compiled_schema_ownership():
    definition = {"type": ["number", "null"], "minimum": 5, "maximum": 3}
    adapter = compile_schema(definition)
    definition["minimum"] = 100
    assert adapter.validate_python(None) is None
    with pytest.raises(ValidationError, match="minimum 5"):
        adapter.validate_python(4)
    with pytest.raises(ValidationError, match="maximum 3"):
        adapter.validate_python(6)
    with pytest.raises(ValidationError, match="expected"):
        adapter.validate_python(True)
    for value in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValidationError, match="finite"):
            adapter.validate_python(value)
    integer = compile_schema({"type": "integer", "minimum": 0, "exclusiveMaximum": 3})
    assert type(integer.validate_python(2.0)) is float
    assert integer.validate_python(2.0) == 2.0
    with pytest.raises(ValidationError, match="exclusiveMaximum"):
        integer.validate_python(3)

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

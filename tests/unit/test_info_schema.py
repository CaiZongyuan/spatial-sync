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

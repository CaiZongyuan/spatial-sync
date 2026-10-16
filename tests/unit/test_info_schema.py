import pytest
from pydantic import ValidationError

from packages.info.schema_subset import compile_schema


def test_compiled_schema_validates_strict_nested_values_and_exports_standard_schema():
    adapter = compile_schema({
        "type": "object", "required": ["rpm", "slots"],
        "properties": {
            "rpm": {"type": "number", "minimum": 500, "maximum": 15000},
            "slots": {"type": "array", "items": {"type": ["string", "null"]}, "minItems": 2},
        },
    })
    value = {"rpm": 3000, "slots": ["tube1", None]}
    assert adapter.validate_python(value) == value
    assert adapter.json_schema()["additionalProperties"] is False
    for invalid in [dict(value, rpm="3000"), dict(value, rpm=True), dict(value, extra=1),
                    dict(value, slots=["tube1"]), dict(value, rpm=15001)]:
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)

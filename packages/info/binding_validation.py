"""Binding semantics shared by runtime Catalog construction and package inspection."""

from pydantic import TypeAdapter

from .conditions import validate_conditions
from .declarations import AlertRules

_ALERT_RULES = TypeAdapter(AlertRules)


def compile_alert_rules(key, binding, definition, property_schema):
    """Validate type defaults and binding replacements through the shared condition language."""
    defaults = _ALERT_RULES.validate_python(definition.get("alerts", {}))
    overrides = _ALERT_RULES.validate_python(binding.get("alerts", {}))
    for rule in defaults.values():
        validate_conditions([rule.when], key, property_schema, allow_cross_entity=False)
    return {
        name: (
            rule.model_dump(mode="json", exclude_none=True),
            validate_conditions([rule.when], key, property_schema),
        )
        for name, rule in (defaults | overrides).items()
    }


def validate_binding_semantics(key, binding, definition, property_schema, sites):
    actions = definition.get("actions", {})
    interlocks = binding.get("interlocks", {})
    if set(interlocks) - actions.keys():
        raise ValueError(f"Interlock references unknown action on {key}")
    conditions = {
        name: validate_conditions(
            action.get("preconditions", []) + interlocks.get(name, []), key, property_schema
        )
        for name, action in actions.items()
    }

    def validate_config(value):
        if isinstance(value, dict):
            if "requires" in value:
                validate_conditions(value["requires"], key, property_schema)
            if "site" in value and value["site"] not in sites:
                raise ValueError(f"Config references an unknown Site: {value['site']}")
            for child in value.values():
                validate_config(child)
        elif isinstance(value, list):
            for child in value:
                validate_config(child)

    validate_config(binding.get("config", {}))
    return conditions

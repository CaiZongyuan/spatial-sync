"""Binding semantics shared by runtime Catalog construction and package inspection."""

from .conditions import validate_conditions


def validate_binding_semantics(key, binding, definition, property_schema, sites):
    actions = definition["actions"]
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

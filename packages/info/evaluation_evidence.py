"""Required Source observations shared by Agent and embodied evaluators."""

from math import isfinite

from packages.info.conditions import CompiledConditions, condition_state
from packages.info.schema_subset import compile_schema, json_equal


class EvidenceUnavailable(Exception):
    pass


def unavailable():
    raise EvidenceUnavailable("Required physical observations are unavailable")


def require_scope(sample, scope):
    if not isinstance(sample, dict) or not json_equal(sample.get("scope"), scope):
        unavailable()


def require_physical(conditions, sample, scope, catalog):
    require_scope(sample, scope)
    state = sample.get("state", {})
    entities = state.get("entities", {}) if isinstance(state, dict) else {}
    if not isinstance(entities, dict):
        unavailable()
    for condition in CompiledConditions(conditions):
        entity = condition.get("entity", "")
        if not entity.startswith("/"):
            continue
        observed = entities.get(entity, {})
        if not isinstance(observed, dict):
            unavailable()
        values, metadata = observed.get("values", {}), observed.get("meta", {})
        if not isinstance(values, dict) or not isinstance(metadata, dict):
            unavailable()
        properties = [condition["property"]]
        if "deviatesFrom" in condition:
            properties.append(condition["deviatesFrom"])
        for name in properties:
            meta = metadata.get(name)
            if (
                name not in values
                or not isinstance(meta, dict)
                or meta.get("stale", False)
                or meta.get("source") not in ("sim", "virtual", "device")
                or any(
                    type(meta.get(key)) not in (int, float) or not isfinite(meta[key]) or meta[key] < 0
                    for key in ("simTime", "sampleTimeMs")
                )
            ):
                unavailable()
            try:
                definition = catalog.types[catalog.entities[entity]["type"]]["properties"][name]
                compile_schema(definition["schema"]).validate_python(values[name])
            except (KeyError, ValueError, TypeError):
                unavailable()
        if condition_state([condition], "", {entity: values}, {entity: metadata}) is None:
            unavailable()

"""Snapshot existing pure program definitions for the engine-free library validator."""


def program_schemas():
    # Both registries contain pure program classes; importing a host or physics port is unnecessary.
    from apps.api.app.sources.synthetic_source import REGISTRY as synthetic
    from packages.sim_runtime.info_catalog import REGISTRY as native

    result = {}
    for source, registry in (("synthetic", synthetic), ("newton", native)):
        for name, program in registry.items():
            result[name] = {
                "type": program.type,
                "source": source,
                "configModule": program.__module__,
                "configClass": program.__qualname__,
                **({"taughtLimits": program.taught_limits} if hasattr(program, "taught_limits") else {}),
                "fields": {
                    field: information.json_schema_extra
                    for field, information in program.Config.model_fields.items()
                    if information.json_schema_extra
                },
            }
    return result

"""Resolve scene bindings and compile reusable definitions before a session runs."""

from copy import deepcopy
from fnmatch import fnmatchcase
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, PrivateAttr

from packages.library.scenes import builtin_scene_path
from packages.protocol.schema import Catalog as CatalogDocument

from .conditions import validate_conditions
from .declarations import EntityBindingDeclaration, EntityTypeDeclaration
from .schema_subset import compile_schema

DEVICE_ROOT = Path(__file__).resolve().parents[2] / "devices"


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sceneRevision: str
    types: dict[str, dict] = {}
    entities: dict[str, dict] = {}
    _params: dict = PrivateAttr(default_factory=dict)
    _properties: dict = PrivateAttr(default_factory=dict)
    _conditions: dict = PrivateAttr(default_factory=dict)
    _configs: dict = PrivateAttr(default_factory=dict)
    _events: dict = PrivateAttr(default_factory=dict)
    _alerts: dict = PrivateAttr(default_factory=dict)

    def action(self, entity_key, action):
        return self.types[self.entities[entity_key]["type"]]["actions"][action]

    def property(self, entity_key, prop):
        return self.types[self.entities[entity_key]["type"]]["properties"][prop]

    def validate_params(self, entity_key, action, params):
        definition = self.entities[entity_key]["type"]
        return deepcopy(self._params[definition, action].validate_python(params))

    def validate_property(self, entity_key, prop, value):
        definition = self.entities[entity_key]["type"]
        return deepcopy(self._properties[definition, prop].validate_python(value))

    def conditions(self, entity_key, action):
        return self._conditions[entity_key, action]

    def program_config(self, entity_key):
        return self._configs[entity_key]

    def alerts(self):
        return self._alerts

    def alert_rules(self):
        """Plain JSON authoring snapshot for internal Runner/gateway agreement."""
        result = {}
        for (key, name), (rule, _) in self._alerts.items():
            result.setdefault(key, {})[name] = deepcopy(rule)
        return result

    def property_validators(self, entity_key):
        type_id = self.entities[entity_key]["type"]
        return {
            name: self._properties[type_id, name].validate_python
            for name in self.types[type_id]["properties"]
        }

    def validate_event(self, entity_key, event, data):
        type_id = self.entities[entity_key]["type"]
        adapter = self._events[type_id, event]
        return deepcopy(adapter.validate_python(data)) if adapter else deepcopy(data)


def load_catalog(manifest, scene_id, registry, *, root=DEVICE_ROOT, scene_file=None):
    root = Path(root)
    scene_file = (
        Path(scene_file)
        if scene_file
        else (
            builtin_scene_path(scene_id) / "devices.yaml"
            if root == DEVICE_ROOT
            else root / "scenes" / f"{scene_id}.yaml"
        )
    )
    scene = yaml.safe_load(scene_file.read_text(encoding="utf-8"))
    if set(scene) != {"scene", "entities"} or scene["scene"] != scene_id:
        raise ValueError("Scene binding document must name its registered scene")
    types = {}
    for path in sorted((root / "types").glob("*.yaml")):
        definition = yaml.safe_load(path.read_text(encoding="utf-8"))
        if definition["type"] in types:
            raise ValueError(f"Duplicate entity type: {definition['type']}")
        types[definition["type"]] = definition
    nodes = {node.entityKey for node in manifest.nodes}
    sites = {site.entityKey for site in manifest.sites}
    entities = {}
    configs = {}
    for template in scene["entities"]:
        if ("key" in template) == ("match" in template):
            raise ValueError("Binding requires exactly one key or match")
        keys = (
            sorted(key for key in nodes if fnmatchcase(key, template["match"]))
            if "match" in template
            else [template["key"]]
        )
        if not keys:
            raise ValueError(f"Binding glob matched no entities: {template['match']}")
        for key in keys:
            if key in entities:
                raise ValueError(f"Duplicate binding: {key}")
            binding = deepcopy(template)
            binding.pop("match", None)
            binding["key"] = key
            if key not in nodes and not binding.get("unplaced", False):
                raise ValueError(f"Binding entity does not exist in Manifest: {key}")
            cls = registry.get(binding["program"])
            if cls is None or cls.type != binding["type"]:
                raise ValueError(f"Unknown program or program/type mismatch: {binding['program']}")
            config = binding.get("config", {})
            for name, value in list(config.items()):
                if isinstance(value, str) and value.endswith(".yaml"):
                    target = (scene_file.parent / value).resolve()
                    if not target.is_relative_to(scene_file.parent.resolve()):
                        raise ValueError("Config documents must stay in the scene directory")
                    config[name] = yaml.safe_load(target.read_text(encoding="utf-8"))
                elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                    expanded = []
                    for item in value:
                        expanded.extend(
                            sorted(s for s in sites if fnmatchcase(s, item)) if "*" in item else [item]
                        )
                    config[name] = expanded
            parsed = cls.Config.model_validate(config)
            configs[key] = parsed
            binding["config"] = parsed.model_dump(mode="json")
            entities[key] = binding
    type_declarations = {key: EntityTypeDeclaration.model_validate(value) for key, value in types.items()}
    binding_declarations = {
        key: EntityBindingDeclaration.model_validate(value) for key, value in entities.items()
    }
    type_rules = {
        key: {
            name: rule.model_dump(mode="json", exclude_none=True) for name, rule in declaration.alerts.items()
        }
        for key, declaration in type_declarations.items()
    }
    binding_rules = {
        key: {
            name: rule.model_dump(mode="json", exclude_none=True) for name, rule in declaration.alerts.items()
        }
        for key, declaration in binding_declarations.items()
    }
    document = CatalogDocument(
        sceneRevision=manifest.sceneRevision,
        types={
            key: value.model_dump(mode="json", exclude={"alerts"}, exclude_none=True)
            for key, value in type_declarations.items()
        },
        entities={
            key: value.model_dump(mode="json", exclude={"alerts"}, exclude_none=True)
            for key, value in binding_declarations.items()
        },
    )
    catalog = Catalog.model_validate(document.model_dump(mode="json", by_alias=True, exclude_none=True))
    catalog._configs = configs
    for type_id, definition in catalog.types.items():
        for prop, value in definition["properties"].items():
            catalog._properties[type_id, prop] = compile_schema(value["schema"])
        for rule in type_rules[type_id].values():
            validate_conditions(
                [rule["when"]],
                "type-self",
                lambda _, prop, definition=definition: definition["properties"][prop]["schema"],
                allow_cross_entity=False,
            )
        for action, value in definition["actions"].items():
            schemas = {
                name: {k: v for k, v in schema.items() if k != "unit"}
                for name, schema in value["params"].items()
            }
            catalog._params[type_id, action] = compile_schema(
                {
                    "type": "object",
                    "properties": schemas,
                    "required": value["required"],
                }
            )
            validate_conditions(
                value["preconditions"],
                "type-self",
                lambda _, prop, definition=definition: definition["properties"][prop]["schema"],
                allow_cross_entity=False,
            )
        for name, event in definition["events"].items():
            catalog._events[type_id, name] = (
                compile_schema(event["schema"]) if event.get("schema") is not None else None
            )
    for key, binding in catalog.entities.items():
        rules = type_rules[binding["type"]] | binding_rules[key]
        for name, rule in rules.items():
            compiled = validate_conditions(
                [rule["when"]], key, lambda entity, prop: catalog.property(entity, prop)["schema"]
            )
            catalog._alerts[key, name] = (rule, compiled)
        actions = catalog.types[binding["type"]]["actions"]
        if set(binding["interlocks"]) - actions.keys():
            raise ValueError(f"Interlock references unknown action on {key}")
        for name, action in actions.items():
            catalog._conditions[key, name] = validate_conditions(
                action["preconditions"] + binding["interlocks"].get(name, []),
                key,
                lambda entity, prop: catalog.property(entity, prop)["schema"],
            )

        def validate_config(value):
            if isinstance(value, dict):
                if "requires" in value:
                    validate_conditions(
                        value["requires"], key, lambda entity, prop: catalog.property(entity, prop)["schema"]
                    )
                if "site" in value and value["site"] not in sites:
                    raise ValueError(f"Config references an unknown Site: {value['site']}")
                for child in value.values():
                    validate_config(child)
            elif isinstance(value, list):
                for child in value:
                    validate_config(child)

        validate_config(binding["config"])
    # Publish exactly the schemas used by compiled validators, including nested
    # object defaults. Compilation above still reads only the authoring subset.
    for type_id, definition in catalog.types.items():
        for prop, value in definition["properties"].items():
            value["schema"] = catalog._properties[type_id, prop].json_schema()
        for name, action in definition["actions"].items():
            exported = catalog._params[type_id, name].json_schema()["properties"]
            action["params"] = {
                param: exported[param] | ({"unit": schema["unit"]} if "unit" in schema else {})
                for param, schema in action["params"].items()
            }
        for name, event in definition["events"].items():
            adapter = catalog._events[type_id, name]
            if adapter is not None:
                event["schema"] = adapter.json_schema()
    return catalog

"""Resolve pinned authoring templates, then instantiate them against real scene resources."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath, PureWindowsPath

from packages.library.archive import package_files
from packages.library.common import content_hash

REFERENCE = re.compile(r"(robot|device|labware|environment)/([a-z0-9]+(?:-[a-z0-9]+)*)@([1-9][0-9]*)")


class TemplateError(ValueError):
    pass


def template_id(value):
    if not isinstance(value, str) or not REFERENCE.fullmatch(value):
        raise TemplateError("Template must name an exact asset kind/name@rev")
    return value


def references(document):
    return sorted(
        {
            template_id(binding["template"])
            for binding in document["entities"]
            if binding.get("template") is not None
        }
    )


def verify_snapshot(snapshot):
    path = Path(snapshot["path"])
    package_files(path)
    if content_hash(path) != snapshot["contentHash"]:
        raise TemplateError("Referenced immutable asset changed: " + snapshot["id"])


def load_snapshots(roots, identifiers, expected=()):
    """Operator roots are trusted configuration; only exact indexed package identities are accepted."""
    from packages.library.metadata import asset_metadata

    pinned = {value["id"]: value["contentHash"] for value in expected}
    result = {}
    for identifier in identifiers:
        template_id(identifier)
        bare, revision = identifier.split("@")
        found = []
        for root in roots.values():
            root = Path(root).resolve()
            path = root / "assets" / bare / revision
            if not path.resolve().is_relative_to(root):
                raise TemplateError("Template asset escapes its library root")
            if (path / "asset.yaml").is_file():
                found.append(path)
        if len(found) != 1:
            raise TemplateError(
                ("Unknown" if not found else "Conflicting") + " asset template: " + identifier
            )
        path = found[0]
        metadata = asset_metadata(path)
        if metadata["id"] != bare:
            raise TemplateError("Template asset metadata does not match its identity")
        digest = content_hash(path)
        if identifier in pinned and digest != pinned[identifier]:
            raise TemplateError("Referenced immutable asset changed: " + identifier)
        result[identifier] = {
            "id": identifier,
            "metadata": metadata,
            "path": str(path),
            "contentHash": digest,
            "files": {name: str(path / name) for name in package_files(path)},
        }
    return result


@dataclass(frozen=True)
class Declaration:
    binding: dict
    template_config: dict | None = None


@dataclass(frozen=True)
class ResolvedBindings:
    scene: str
    declarations: tuple[Declaration, ...]

    def bootstrap(self):
        return {
            "scene": self.scene,
            "entities": [
                deepcopy(item.binding)
                | {"config": deepcopy(item.template_config or {}) | deepcopy(item.binding.get("config", {}))}
                for item in self.declarations
            ],
        }


def resolve(document, source, assets, expected=()):
    declarations, verified = [], set()
    pinned = {value["id"]: value["contentHash"] for value in expected}
    for authored in document["entities"]:
        binding = deepcopy(authored)
        identifier = binding.pop("template", None)
        if identifier is None:
            declarations.append(Declaration(binding))
            continue
        template_id(identifier)
        asset = assets.get(identifier)
        if asset is None:
            raise TemplateError("Unknown asset template: " + identifier)
        if identifier in pinned and asset["contentHash"] != pinned[identifier]:
            raise TemplateError("Referenced immutable asset changed: " + identifier)
        if identifier not in verified:
            verify_snapshot(asset)
            verified.add(identifier)
        candidates = [
            item
            for item in asset["metadata"].get("implements", [])
            if (binding.get("type") is None or item["type"] == binding["type"])
            and source in item.get("programs", {})
        ]
        if len(candidates) != 1:
            raise TemplateError(
                f"Template {identifier} needs one implements entry for type "
                f"{binding.get('type') or '(unspecified)'} and source {source}; found {len(candidates)}"
            )
        implementation = candidates[0]
        program = implementation["programs"][source]
        if binding.get("program") is not None and binding["program"] != program:
            raise TemplateError("Scene program conflicts with the template's selected source program")
        binding.update(type=implementation["type"], program=program)
        declarations.append(Declaration(binding, deepcopy(implementation.get("config", {}))))
    return ResolvedBindings(document["scene"], tuple(declarations))


def config_fields(program):
    return {
        name: information.json_schema_extra
        for name, information in program.Config.model_fields.items()
        if information.json_schema_extra
    }


def manifest_resources(manifest):
    return {
        "entities": {node.entityKey for node in manifest.nodes},
        "bodies": {node.entityKey for node in manifest.nodes if getattr(node, "kind", None) == "body"},
        "sites": {site.entityKey for site in manifest.sites},
        "joints": {joint.entityKey for joint in getattr(manifest, "joints", [])},
    }


def rebase(value, key):
    if not isinstance(value, str):
        raise TemplateError("A template spatial reference must be a path string")
    if value == "/Asset":
        value = "."
    elif value.startswith("/Asset/"):
        value = value.removeprefix("/Asset/")
    path = PurePosixPath(value)
    if (
        PureWindowsPath(value).drive
        or "\\" in value
        or ":" in value
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise TemplateError("Template spatial references must stay relative to /Asset")
    return key if str(path) == "." else key.rstrip("/") + "/" + str(path)


def instantiate(resolved, resources, programs):
    nodes = set(resources["entities"])
    roles = {
        "body": set(resources["bodies"]),
        "site": set(resources["sites"]),
        "joint": set(resources["joints"]),
    }
    bindings, bound = [], set()
    for declaration in resolved.declarations:
        authored = declaration.binding
        if ("key" in authored) == ("match" in authored):
            raise TemplateError("Binding requires exactly one key or match")
        keys = (
            sorted(key for key in nodes if fnmatchcase(key, authored["match"]))
            if "match" in authored
            else [authored["key"]]
        )
        if not keys:
            raise TemplateError("Binding glob matched no entities: " + authored["match"])
        for key in keys:
            if key in bound:
                raise TemplateError("Duplicate binding: " + key)
            if key not in nodes and not authored.get("unplaced", False):
                raise TemplateError("Binding entity does not exist: " + key)
            bound.add(key)
            binding = deepcopy(authored)
            binding.pop("match", None)
            binding["key"] = key
            if declaration.template_config is not None:
                program = programs.get(binding["program"])
                if program is None:
                    raise TemplateError("Unknown template program: " + binding["program"])
                base = deepcopy(declaration.template_config)
                overrides = binding.get("config", {})
                for field, information in program["fields"].items():
                    if field not in base:
                        continue
                    if information.get("documentSchema") and field not in overrides:
                        raise TemplateError("Scene-owned Config document requires a scene override: " + field)
                    if information.get("spatialReference"):
                        base[field] = (
                            [rebase(value, key) for value in base[field]]
                            if isinstance(base[field], list)
                            else rebase(base[field], key)
                        )
                config = base | deepcopy(overrides)
                for field, information in program["fields"].items():
                    role = information.get("spatialReference")
                    if not role or field not in config:
                        continue
                    values = config[field] if isinstance(config[field], list) else [config[field]]
                    expanded = []
                    for value in values:
                        if not isinstance(value, str):
                            raise TemplateError(field + " requires spatial path strings")
                        matches = sorted(path for path in roles[role] if fnmatchcase(path, value))
                        if not matches:
                            raise TemplateError(f"{field} references a missing {role}: {value}")
                        if not isinstance(config[field], list) and len(matches) != 1:
                            raise TemplateError(
                                "Scalar spatial reference matched multiple resources: " + field
                            )
                        expanded.extend(matches)
                    config[field] = expanded if isinstance(config[field], list) else expanded[0]
                binding["config"] = config
            bindings.append(binding)
    return {"scene": resolved.scene, "entities": bindings}

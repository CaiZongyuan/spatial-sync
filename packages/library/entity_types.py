"""Immutable robot EntityType declarations in the active user library."""

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError

from packages.info.declarations import EntityTypeDeclaration
from packages.library.archive import destination_parent, import_lock, invalid
from packages.library.common import TYPE_ROOT, LibraryError, document, load_entity_types
from packages.library.transactions import durable_json

USER_TYPE = re.compile(r"u-[a-z0-9]+(?:-[a-z0-9]+)*/v[1-9][0-9]*")
ContentHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_BYTES = 256 * 1024

_VECTOR = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
_OPTIONAL_TEXT = {"type": ["string", "null"]}
_STANDARD_SCHEMAS = {
    "phase": {"type": "string"},
    "activeAction": _OPTIONAL_TEXT,
    "lastCompletedAction": _OPTIONAL_TEXT,
    "fallen": {"type": "boolean"},
    "jointPositions": {"type": "array", "items": {"type": "number"}, "minItems": 1},
    "basePosition": _VECTOR,
    "baseHeight": {"type": "number"},
    "yawRad": {"type": "number"},
    "tiltDeg": {"type": "number"},
    "pitchDeg": {"type": "number"},
    "bodyVelocity": _VECTOR,
    "currentWaypoint": _OPTIONAL_TEXT,
    "targetWaypoint": _OPTIONAL_TEXT,
    "trackingError": {"type": "number"},
}


class RobotEntityType(EntityTypeDeclaration):
    type: Annotated[str, Field(pattern=f"^{USER_TYPE.pattern}$", max_length=160)]


@dataclass(frozen=True)
class TypeSnapshot:
    path: Path
    content: bytes
    declaration: dict

    @property
    def identity(self):
        return {"id": self.declaration["type"], "contentHash": hashlib.sha256(self.content).hexdigest()}

    def public(self):
        return self.declaration | {"contentHash": self.identity["contentHash"]}


class EntityTypes:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, identifier):
        if len(identifier) > 160 or not USER_TYPE.fullmatch(identifier):
            raise LibraryError("unknown_entity_type", "Unknown EntityType version")
        namespace, version = identifier.split("/")
        return destination_parent(self.root, ("device-types", namespace)) / (version + ".yaml")

    def snapshot(self, identifier, expected=None):
        path = self.path(identifier)
        try:
            info = path.lstat()
            if (not stat.S_ISREG(info.st_mode) or path.is_junction()
                    or info.st_nlink != 1 or info.st_size > MAX_BYTES):
                raise ValueError
            raw = path.read_bytes()
            value = RobotEntityType.model_validate(json.loads(raw)).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            result = TypeSnapshot(path, raw, value)
            if value["type"] != identifier or (
                expected is not None and result.identity["contentHash"] != expected
            ):
                raise ValueError
        except FileNotFoundError:
            raise LibraryError("unknown_entity_type", f"Unknown EntityType version: {identifier}") from None
        except (OSError, ValueError):
            raise LibraryError(
                "entity_type_changed", f"Invalid or changed EntityType: {identifier}", 409
            ) from None
        return result

    def builtin(self):
        for path in sorted(TYPE_ROOT.glob("*.yaml")):
            value = document(path)
            yield TypeSnapshot(path, path.read_bytes(), value)

    def describe(self, identifier):
        if USER_TYPE.fullmatch(identifier):
            return self.snapshot(identifier).public()
        for item in self.builtin():
            if item.declaration["type"] == identifier:
                return item.public()
        raise LibraryError("unknown_entity_type", "Unknown EntityType version")

    def list(self):
        directory = destination_parent(self.root, ("device-types",))
        return [item.public() for item in self.builtin()] + [
            self.snapshot(f"{path.parent.name}/{path.stem}").public()
            for path in sorted(directory.glob("*/*.yaml"))
        ]

    def publish(self, declaration):
        from packages.info.catalog import compile_catalog

        identifier = declaration.type
        value = declaration.model_dump(mode="json", by_alias=True, exclude_none=True)
        properties = value["properties"]
        for name, schema in _STANDARD_SCHEMAS.items():
            existing = properties.setdefault(name, {"schema": schema})
            if existing["schema"] != schema:
                raise invalid("Property schema must match the robot telemetry contract", path=name)
        stop = value["actions"].setdefault("stop", {"concurrent": True})
        if (not stop["concurrent"] or stop.get("params") or stop.get("preconditions")
                or stop.get("longRunning")):
            raise invalid("stop must be immediate, concurrent, and unconditional", path="stop")
        try:
            value = RobotEntityType.model_validate(value).model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            compile_catalog("entity-type-publication", {identifier: value}, {}, {})
            value = json.loads(json.dumps(value, sort_keys=True, allow_nan=False))
            if len(json.dumps(value, ensure_ascii=False).encode()) > MAX_BYTES:
                raise ValueError
        except (ValidationError, ValueError, KeyError, TypeError):
            raise invalid("Invalid robot EntityType declaration", path="type") from None
        if identifier in load_entity_types():
            raise LibraryError("entity_type_conflict", "EntityType ID is already registered", 409)
        with import_lock(self.root):
            path = self.path(identifier)
            if path.exists() or path.is_symlink() or path.is_junction():
                version = self.snapshot(identifier)
                if version.declaration != value:
                    raise LibraryError("entity_type_conflict", "Published EntityType is immutable", 409)
                return version.public(), False
            destination_parent(self.root, path.relative_to(self.root).parts[:-1], create=True)
            durable_json(path, value)
            return self.snapshot(identifier).public(), True

    def require_pin(self, binding, *, populate=False):
        identifier = binding.get("type") or ""
        pinned = binding.get("typeContentHash")
        if not USER_TYPE.fullmatch(identifier):
            if pinned is not None:
                raise LibraryError("entity_type_pin_invalid", "Pin requires a published user EntityType", 422)
            return None
        if pinned is None and not populate:
            raise LibraryError("entity_type_unpinned", f"Missing EntityType content hash: {identifier}", 409)
        result = self.snapshot(identifier, pinned)
        if populate:
            binding["typeContentHash"] = result.identity["contentHash"]
        return result

"""Library identity and unchanged raw-file tree hashing shared by both package kinds."""

import hashlib
import os
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_ROOT = REPO_ROOT / "library"
TYPE_ROOT = REPO_ROOT / "devices/types"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
KINDS = {"robot", "device", "labware", "environment"}


class LibraryError(Exception):
    def __init__(self, code, message, status=404, **details):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **details}


def document(path):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a mapping: {path}")
    return value


def user_entity_types(*library_roots):
    candidates = list(library_roots)
    env_library = os.getenv("LW_LIBRARY")
    if env_library:
        candidates.append(Path(env_library).expanduser())
    seen, types = set(), {}
    for root in candidates:
        if root is None:
            continue
        type_dir = (Path(root).expanduser().resolve() / "types")
        if type_dir in seen or not type_dir.is_dir():
            continue
        seen.add(type_dir)
        for file in sorted(type_dir.glob("*.yaml")):
            value = document(file)
            if value["type"] in types:
                raise ValueError(f"Duplicate entity type: {value['type']}")
            types[value["type"]] = value
    return types


def load_entity_types(*library_roots):
    types = {}
    for file in sorted(TYPE_ROOT.glob("*.yaml")):
        value = document(file)
        if value["type"] in types:
            raise ValueError(f"Duplicate entity type: {value['type']}")
        types[value["type"]] = value
    for type_id, value in user_entity_types(*library_roots).items():
        if type_id in types:
            raise ValueError(f"Duplicate entity type: {type_id}")
        types[type_id] = value
    return types


def content_hash(path):
    digest = hashlib.sha256()
    for file in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
        if file.is_symlink() or not file.resolve().is_relative_to(path.resolve()):
            raise ValueError(f"Scene package cannot contain links outside its files: {file}")
        if file.is_file():
            digest.update(file.relative_to(path).as_posix().encode() + b"\0")
            with file.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()

"""Library identity and unchanged raw-file tree hashing shared by both package kinds."""

import hashlib
import os
import re
import stat
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


def extra_entity_types(extra_dir=None):
    raw = extra_dir if extra_dir is not None else os.getenv("SPATIAL_EXTRA_DEVICE_TYPES_DIR")
    if not raw:
        return {}
    path = Path(raw).expanduser()
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError(f"Extra device types directory cannot be a link: {path}")
    resolved = path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"Extra device types directory does not exist: {path}")
    types = {}
    for file in sorted(resolved.glob("*.yaml")):
        info = file.stat(follow_symlinks=False)
        if (
            file.is_symlink()
            or (hasattr(file, "is_junction") and file.is_junction())
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or not file.resolve().is_relative_to(resolved)
        ):
            raise ValueError(f"Extra device type file must be a regular local file: {file}")
        value = document(file)
        if value["type"] in types:
            raise ValueError(f"Duplicate entity type: {value['type']}")
        types[value["type"]] = value
    return types


def load_entity_types(extra_dir=None):
    types = {}
    for file in sorted(TYPE_ROOT.glob("*.yaml")):
        value = document(file)
        if value["type"] in types:
            raise ValueError(f"Duplicate entity type: {value['type']}")
        types[value["type"]] = value
    for type_id, value in extra_entity_types(extra_dir).items():
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

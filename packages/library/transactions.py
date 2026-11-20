"""The user library's publication lock and durable revision high-water marks."""

import json
import os
import tempfile
from pathlib import Path

from packages.library.common import LibraryError
from packages.library.reports import SUBJECT

METADATA_MAX_BYTES = 256 * 1024


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def durable_json(path, value):
    durable_bytes(path, json_bytes(value))


def sync_directory(path):
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def durable_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.is_junction():
        raise ValueError("Transaction document cannot be a filesystem link")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def revisions(root):
    path = root / ".library-revisions.json"
    if not path.exists():
        return {}
    try:
        if path.is_symlink() or path.is_junction():
            raise ValueError("Revision ledger cannot be a filesystem link")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or any(
            not SUBJECT.fullmatch(f"{key}@{rev}") or type(rev) is not int or rev < 1
            for key, rev in value.items()
        ):
            raise ValueError("Invalid revision ledger")
        return value
    except (OSError, ValueError) as exc:
        raise LibraryError("library_unavailable", "Cannot inspect revision high-water marks", 503) from exc


def reserve_revision(root, subject, revision):
    """Called under import_lock; a failed transaction can leave a harmless revision gap."""
    value = revisions(root)
    value[subject] = max(value.get(subject, 0), revision)
    durable_json(root / ".library-revisions.json", value)

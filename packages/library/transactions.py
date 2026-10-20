"""The user library's publication lock and durable revision high-water marks."""

import json
import os
import tempfile
from pathlib import Path

from packages.library.common import LibraryError
from packages.library.reports import SUBJECT


def durable_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.is_junction():
        raise ValueError("Transaction document cannot be a filesystem link")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
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

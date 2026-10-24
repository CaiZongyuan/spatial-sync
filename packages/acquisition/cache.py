"""Raw source cache paths, completeness, size accounting and LRU cleanup.

The cache lives outside every immutable imported version: deleting it (or going
offline) must never affect a published local asset.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packages.library.transactions import durable_json

CACHE_MARKER = ".acquisition-cache.json"
PROVIDER = re.compile(r"[a-z0-9][a-z0-9_.-]*")
PACKAGE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
REVISION = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    provider: str
    revision: str
    package: str
    bytes: int
    used: float

    @property
    def identity(self):
        return (self.provider, self.revision, self.package)


def _component(value):
    if not isinstance(value, str) or not (
        PROVIDER.fullmatch(value) or PACKAGE.fullmatch(value) or REVISION.fullmatch(value)
    ):
        raise ValueError("Unsafe raw-cache path component")
    return value


def package_path(root, provider, revision, package):
    return Path(root) / _component(provider) / _component(revision) / _component(package)


def files_bytes(path):
    total = 0
    for entry in Path(path).rglob("*"):
        if entry.name == CACHE_MARKER:
            continue
        if entry.is_symlink() or entry.is_junction():
            raise ValueError("Raw cache entry contains a filesystem link")
        if entry.is_file():
            total += entry.stat().st_size
    return total


def marker(path):
    value = Path(path) / CACHE_MARKER
    if not value.is_file() or value.is_symlink():
        return None
    try:
        data = json.loads(value.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def complete(path):
    data = marker(path)
    return (
        data is not None
        and isinstance(data.get("bytes"), int)
        and not isinstance(data.get("bytes"), bool)
        and data["bytes"] >= 0
    )


def cached_bytes(root, provider, revision, package):
    """Only a completed download counts as reusable; a partial tree stays unknown."""
    path = package_path(root, provider, revision, package)
    if not complete(path):
        return 0
    try:
        return files_bytes(path)
    except (OSError, ValueError):
        return 0


def mark_complete(root, provider, revision, package):
    path = package_path(root, provider, revision, package)
    if not path.is_dir():
        return False
    try:
        size = files_bytes(path)
    except (OSError, ValueError):
        return False
    durable_json(
        path / CACHE_MARKER,
        {
            "provider": provider,
            "revision": revision,
            "package": package,
            "bytes": size,
            "completedAt": datetime.now(UTC).isoformat(),
            "lastUsedAt": datetime.now(UTC).isoformat(),
        },
    )
    return True


def touch(root, provider, revision, package):
    path = package_path(root, provider, revision, package)
    data = marker(path)
    if data is not None:
        durable_json(path / CACHE_MARKER, data | {"lastUsedAt": datetime.now(UTC).isoformat()})


def _entry(root, path):
    relative = path.relative_to(root).parts
    if len(relative) != 3:
        return None
    provider, revision, package = relative
    if not (PROVIDER.fullmatch(provider) and REVISION.fullmatch(revision) and PACKAGE.fullmatch(package)):
        return None
    try:
        size = files_bytes(path)
        used = max((item.stat().st_mtime for item in path.rglob("*")), default=path.stat().st_mtime)
    except (OSError, ValueError):
        return None
    return CacheEntry(path, provider, revision, package, size, used)


def entries(root):
    root = Path(root)
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.glob("*/*/*")):
        if path.is_dir() and not path.is_symlink() and not path.is_junction():
            item = _entry(root, path)
            if item is not None:
                result.append(item)
    return result


def total_bytes(root):
    return sum(item.bytes for item in entries(root))


def reclaimable(root, active):
    return sum(item.bytes for item in entries(root) if item.identity not in active)


def clean(root, *, active, limit, required=0):
    """Remove unused cache entries least-recently-used first until the budget fits."""
    root = Path(root)
    current = total_bytes(root)
    if current + required <= limit:
        return 0
    removed = 0
    for item in sorted(entries(root), key=lambda entry: entry.used):
        if current + required <= limit:
            break
        if item.identity in active:
            continue
        try:
            shutil.rmtree(item.path)
        except OSError:
            continue
        current -= item.bytes
        removed += item.bytes
        for parent in (item.path.parent, item.path.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                break
    return removed

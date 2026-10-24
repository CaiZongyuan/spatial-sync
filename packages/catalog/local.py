"""Local immutable assets and their provenance-only link to catalog entries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalAsset:
    """A local immutable version and the catalog entry established by provenance."""

    version: object
    provenance: dict | None
    entry: object | None


def read_provenance(version):
    path = Path(version.path) / "provenance.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def package_bytes(version):
    try:
        return sum(
            path.stat().st_size for path in Path(version.path).rglob("*") if path.is_file()
        )
    except OSError:
        return None

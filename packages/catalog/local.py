"""Local immutable assets and their provenance-only link to catalog entries."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from packages.library.common import LibraryError


@dataclass(frozen=True)
class LocalAsset:
    """One read's local facts and the catalog entry established by provenance."""

    version: object
    provenance: dict | None
    entry: object | None

    @cached_property
    def summary(self):
        return self.version.summary()

    @cached_property
    def report(self):
        return _read_report(self)

    @cached_property
    def license(self):
        return _read_license(self)


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
    indexed = getattr(version, "byte_size", None)
    if indexed is not None:
        return indexed
    try:
        return sum(
            path.stat().st_size for path in Path(version.path).rglob("*") if path.is_file()
        )
    except OSError:
        return None


def local_report(local):
    return local.report


def _read_report(local):
    report = getattr(local.version, "report", None)
    if not callable(report):
        return {}
    try:
        value = report()
    except (OSError, ValueError, LibraryError):
        return {}
    return value if isinstance(value, dict) else {}


def local_usage(local):
    """Derive usage from this version's own recorded checks, never from a correlated source."""
    report = local_report(local)
    checks = {
        check.get("id"): check
        for check in report.get("checks", [])
        if isinstance(check, dict) and isinstance(check.get("id"), str)
    }

    def passed(identifier):
        return (checks.get(identifier) or {}).get("status") == "pass"

    structure = ("S1", "S2", "S4", "S6")
    if all(identifier in checks for identifier in structure):
        dressing = all(passed(identifier) for identifier in structure)
    else:
        dressing = None
    collision = (local.version.metadata.get("physics") or {}).get("collision")
    if collision is None:
        static = None
    elif dressing is None:
        static = None
    elif dressing is False:
        static = False
    elif "P1" not in checks or "P3" not in checks:
        static = None
    else:
        static = passed("P1") and passed("P3")
    if checks:
        evidence = (
            "Derived from this version's recorded checks: S1/S2/S4/S6 for dressing and "
            "P1/P3 plus an authored collision for static collision. Missing checks stay unknown."
        )
    else:
        evidence = "This version has no recorded validation checks; usage stays unknown."
    return {
        "dressing": dressing,
        "staticCollision": static,
        "evidence": evidence,
    }


def local_license(local):
    return local.license


def _read_license(local):
    value = (local.provenance or {}).get("license")
    name = value.get("name") if isinstance(value, dict) else value if isinstance(value, str) else None
    if not name:
        return {"name": None, "admitted": False, "unknown": True, "obligations": []}
    obligations = value.get("obligations", []) if isinstance(value, dict) else []
    version_path = getattr(local.version, "path", None)
    evidence = False
    if version_path:
        root = Path(version_path).resolve()
        evidence = any(
            (root / filename).is_file()
            for filename in ("LICENSE", "LICENSE.txt", "LICENSE.md", "NOTICE")
        )
        declared = value.get("file") if isinstance(value, dict) else None
        if not evidence and isinstance(declared, str):
            candidate = (root / declared).resolve()
            evidence = candidate.is_relative_to(root) and candidate.is_file()
    if not evidence:
        return {"name": name, "admitted": False, "unknown": True, "obligations": []}
    admitted = value.get("admitted") if isinstance(value, dict) else None
    if not isinstance(admitted, bool):
        admitted = name in {"CC0-1.0", "CC-BY-4.0"}
    return {
        "name": name,
        "admitted": admitted,
        "unknown": False,
        "obligations": list(obligations) if isinstance(obligations, list) else [],
    }


def local_source(local):
    if local.entry is not None:
        return {
            "provider": local.entry.provider,
            "packageId": local.entry.package_id,
            "revision": local.entry.revision,
            "bytes": local.entry.source.bytes,
            "files": local.entry.source.files,
        }
    provenance = local.provenance or {}
    return {
        "provider": None,
        "packageId": provenance.get("sourcePackage"),
        "revision": provenance.get("sourceRevision"),
        "bytes": None,
        "files": None,
    }


def local_unknown(local):
    source = local_source(local)
    license_document = local_license(local)
    usage = local_usage(local)
    unknown = []
    if source["provider"] is None:
        unknown.append("provider")
    if source["bytes"] is None:
        unknown.append("sourceBytes")
    if license_document["unknown"]:
        unknown.append("license")
    for name in ("dressing", "staticCollision"):
        if usage[name] is None:
            unknown.append(name)
    if local.version.metadata.get("dimensionsM") is None:
        unknown.append("dimensionsM")
    if (local.version.metadata.get("physics") or {}).get("collision") is None:
        if "staticCollision" not in unknown:
            unknown.append("staticCollision")
    return list(dict.fromkeys(unknown))

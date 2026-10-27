"""Immutable uploaded asset packages and their indexed files."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from packages.library.archive import MAX_UPLOAD_BYTES as MAX_UPLOAD_BYTES
from packages.library.archive import indexed_file, package_files
from packages.library.archive import invalid as invalid
from packages.library.common import BUILTIN_ROOT, TYPE_ROOT, LibraryError, content_hash, document
from packages.library.metadata import AssetMetadata as AssetMetadata
from packages.library.metadata import asset_metadata as metadata
from packages.library.reports import (
    HASH,
    SUBJECT,
    ReportStore,
    parse_report,
    validation_read,
    validation_status,
)


def gaps(data, path, report=None):
    missing = ["structure_validation", "physics_validation"]
    checks = (report or {}).get("checks", [])
    if {f"S{i}" for i in range(1, 9)}.issubset({item["id"] for item in checks}):
        missing.remove("structure_validation")
    measured = {
        item["id"] for item in checks if item["tier"] == "physics"
        and (item["status"] in ("pass", "fail") or item.get("notApplicable"))
    }
    if ((report or {}).get("physics") or {}).get("status") == "completed" and {
        "P1", "P2", "P3", "P4"
    }.issubset(measured) and not any(
        item["tier"] == "physics" and item["status"] == "fail" for item in checks
    ):
        missing.remove("physics_validation")
    if not data.get("visual"):
        missing.append("visual")
    if not data.get("dimensionsM"):
        missing.append("dimensions")
    physics = data.get("physics") or {}
    if not physics.get("massKg"):
        missing.append("mass")
    if not physics.get("collision"):
        missing.append("collision")
    if physics.get("massSource") == "estimated":
        missing.append("mass_estimated")
    if not (path / "provenance.json").is_file():
        missing.append("provenance")
    if not any((path / name).is_file() for name in ("LICENSE", "LICENSE.txt", "LICENSE.md")):
        missing.append("license")
    return missing


@dataclass(frozen=True)
class AssetVersion:
    id: str
    rev: int
    root: str
    path: Path
    metadata: dict
    content_hash: str
    validation: dict | None
    files: tuple[str, ...]
    reports: ReportStore | None = None
    byte_size: int | None = None

    @property
    def bare_id(self):
        return self.id.partition("@")[0]

    def report(self):
        return (
            self.reports.latest(f"asset:{self.id}", self.content_hash, self.validation)
            if self.reports
            else (self.validation)
        )

    def url(self, relative):
        return f"/api/v1/library/files/{self.root}/" + quote(
            f"assets/{self.bare_id}/{self.rev}/{relative}", safe="/"
        )

    def summary(self):
        report = self.report()
        missing = gaps(self.metadata, self.path, report)
        items = (report or {}).get("completeness")
        if items is None:
            physics = self.metadata.get("physics") or {}
            statuses = {
                "collision": "warning" if physics.get("collision") else "missing",
                "massInertia": "warning" if physics.get("massKg") else "missing",
                "visual": "warning" if self.metadata.get("visual") else "missing",
                "license": "missing" if "license" in missing else "present",
                "jointSites": "warning" if self.metadata.get("implements") else "not_applicable",
                "types": "present" if self.metadata.get("implements") else "missing",
            }
            items = [
                {
                    "id": name,
                    "status": status,
                    "message": "Declared package metadata; full inspection is not recorded",
                }
                for name, status in statuses.items()
            ]
        missing = list(
            dict.fromkeys(
                missing + [item["id"] for item in items if item["status"] in ("missing", "warning")]
            )
        )
        thumbnail = next(
            (
                name
                for name in self.files
                if name.startswith("previews/") and Path(name).suffix.lower() in (".png", ".jpg", ".webp")
            ),
            None,
        )
        return {
            "id": self.id,
            "assetId": self.bare_id,
            "assetVersion": self.id,
            "rev": self.rev,
            "kind": self.metadata["kind"],
            "title": self.metadata["title"],
            "description": self.metadata.get("description", ""),
            "dimensionsM": deepcopy(self.metadata.get("dimensionsM")),
            "implementedTypes": list(
                dict.fromkeys(item["type"] for item in self.metadata.get("implements", []))
            ),
            "root": self.root,
            "contentHash": self.content_hash,
            "gaps": missing,
            "gapCount": len(missing),
            "completeness": {
                "status": "warning"
                if any(item["status"] in ("missing", "warning") for item in items)
                else "complete",
                "gaps": missing,
                "items": deepcopy(items),
            },
            "validationStatus": validation_status(report),
            "thumbnailUrl": self.url(thumbnail) if thumbnail else None,
            "visualUrl": self.url(self.metadata["visual"]) if self.metadata.get("visual") else None,
        }


class AssetLibrary:
    def __init__(self, state_root, builtin_root=BUILTIN_ROOT, user_root=None):
        self.roots = {
            "builtin": Path(builtin_root).resolve(),
            "user": Path(
                user_root if user_root is not None else os.getenv("LW_LIBRARY", "~/.spatial-sync/library")
            )
            .expanduser()
            .resolve(),
        }
        self.jobs_root = Path(state_root) / "imports"
        self.reports = ReportStore(state_root)
        self._lock = threading.Lock()
        self.versions, self.files = {}, {}

    def startup(self):
        versions, files = {}, {}
        for root_name, root in self.roots.items():
            for file in sorted((root / "assets").glob("*/*/*/asset.yaml")):
                path = file.parent
                if not path.resolve().is_relative_to(root):
                    raise ValueError("Asset package escapes library root")
                data = metadata(path)
                kind, name, rev = path.relative_to(root / "assets").parts
                if (
                    data["id"] != f"{kind}/{name}"
                    or not rev.isdecimal()
                    or int(rev) < 1
                    or str(int(rev)) != rev
                ):
                    raise ValueError(f"Invalid asset package identity: {file}")
                indexed = tuple(package_files(path))
                report_path = path / "validation.json"
                report = (
                    json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
                )
                version_id = f"{data['id']}@{int(rev)}"
                if report is not None:
                    try:
                        report = parse_report(report, f"asset:{version_id}")
                    except ValueError as exc:
                        raise ValueError(f"Invalid asset validation report: {report_path}") from exc
                version = AssetVersion(
                    version_id,
                    int(rev),
                    root_name,
                    path,
                    data,
                    content_hash(path),
                    report,
                    indexed,
                    self.reports,
                    sum((path / name).stat().st_size for name in indexed),
                )
                previous = self.versions.get(version_id)
                if previous is not None and previous.content_hash != version.content_hash:
                    raise LibraryError("asset_changed", "Asset package changed after indexing", 409)
                if version.id in versions:
                    raise ValueError(f"Conflicting asset version {version.id} in built-in and user libraries")
                versions[version.id] = version
                for relative in indexed:
                    files[(root_name, f"assets/{data['id']}/{rev}/{relative}")] = (version, relative)
        self.versions, self.files = versions, files

    @validation_read()
    def list(self, kind=None, query=None, only_gaps=False):
        result = []
        for version in sorted(self.versions.values(), key=lambda item: (item.bare_id, item.rev)):
            if kind and version.metadata["kind"] != kind:
                continue
            if query and query.casefold() not in f"{version.id} {version.metadata['title']}".casefold():
                continue
            summary = version.summary()
            if not only_gaps or summary["gaps"]:
                result.append(deepcopy(summary))
        return result

    def resolve(self, selector):
        bare_id, separator, revision = selector.partition("@")
        if separator and (not revision.isdecimal() or int(revision) < 1 or str(int(revision)) != revision):
            raise LibraryError("unknown_version", "Unknown asset version")
        matches = [
            version
            for version in self.versions.values()
            if version.bare_id == bare_id and (not separator or version.rev == int(revision))
        ]
        if not matches:
            raise LibraryError("unknown_version", "Unknown asset version")
        return max(matches, key=lambda version: version.rev)

    def describe(self, selector, scenes=None):
        version = self.resolve(selector)
        self.indexed_file(version.root, f"assets/{version.bare_id}/{version.rev}/asset.yaml")
        referencing, uninspected = [], []
        if scenes is not None:
            for scene in sorted(scenes.versions.values(), key=lambda item: (item.name, item.rev)):
                reasons = []
                known = False
                try:
                    scenes.indexed_file(scene.root, f"scenes/{scene.name}/{scene.rev}/scene.yaml")
                    known = any(
                        binding.get("template") == version.id for binding in scene.bindings["entities"]
                    )
                    report = scene.report() or {}
                    references = report.get("referencedAssets")
                    if isinstance(references, list):
                        for reference in references:
                            if (
                                not isinstance(reference, dict)
                                or not SUBJECT.fullmatch(f"asset:{reference.get('id')}")
                                or not isinstance(reference.get("contentHash"), str)
                                or not HASH.fullmatch(reference["contentHash"])
                            ):
                                reasons.append("Recorded asset references are malformed")
                            elif reference.get("id") == version.id:
                                if reference.get("contentHash") == version.content_hash:
                                    known = True
                                else:
                                    reasons.append("Recorded asset reference hash differs from this version")
                    elif references is not None:
                        reasons.append("Recorded asset references are malformed")
                    # Registered Python stages have no library USD dependencies; their
                    # explicit template declarations above are still real references.
                    if not scene.metadata["stage"].startswith("python:"):
                        dependency_check = next(
                            (check for check in report.get("checks", []) if check["id"] == "S7"), None
                        )
                        if not isinstance(references, list) or not dependency_check:
                            reasons.append("USD asset dependencies have not been recorded")
                        elif dependency_check["status"] != "pass":
                            reasons.append(dependency_check["message"])
                except (OSError, ValueError, LibraryError) as exc:
                    reasons.append(str(exc))
                if known:
                    referencing.append({
                        "id": scene.id, "sceneId": scene.name, "rev": scene.rev,
                        "title": scene.metadata["title"], "root": scene.root,
                        "contentHash": scene.content_hash,
                    })
                if reasons:
                    uninspected.append({"id": scene.id, "reason": "; ".join(dict.fromkeys(reasons))})
        provenance_file = version.path / "provenance.json"
        provenance = None
        if provenance_file.is_file():
            try:
                provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                provenance = {"error": "Provenance has not passed S4 validation"}
        definitions = {
            value["type"]: value for file in sorted(TYPE_ROOT.glob("*.yaml")) if (value := document(file))
        }
        report = version.report()
        return deepcopy(
            version.summary()
            | {
                "metadata": version.metadata,
                "validation": report,
                "validationCoverage": (report or {}).get("inspection")
                or "Read the recorded checks; physics execution is not recorded",
                "provenance": provenance,
                "referencingScenes": referencing,
                "referenceInspection": {
                    "complete": scenes is not None and not uninspected,
                    "uninspectedScenes": uninspected,
                    "reason": None if scenes is not None else "Scene index is unavailable",
                },
                "types": {
                    binding["type"]: definitions.get(binding["type"])
                    for binding in version.metadata.get("implements", [])
                },
                "files": {relative: version.url(relative) for relative in version.files},
            }
        )

    def indexed_file(self, root, relative):
        return indexed_file(self.roots, self.files, root, relative, changed="asset_changed")

    def jobs(self):
        return sorted(
            (json.loads(path.read_text(encoding="utf-8")) for path in self.jobs_root.glob("*.json")),
            key=lambda job: job["createdAt"],
            reverse=True,
        )

    def job(self, import_id):
        if not re.fullmatch(r"[a-f0-9]{32}", import_id):
            raise LibraryError("unknown_import", "Unknown import job")
        path = self.jobs_root / f"{import_id}.json"
        if not path.is_file():
            raise LibraryError("unknown_import", "Unknown import job")
        return json.loads(path.read_text(encoding="utf-8"))

    def import_tar(self, stream):
        from packages.library.imports import LibraryImports
        from packages.library.scenes import SceneLibrary
        from packages.library.validation import StructuralValidator

        scenes = SceneLibrary(
            builtin_root=self.roots["builtin"], user_root=self.roots["user"], state_root=self.jobs_root.parent
        )
        return LibraryImports(self, scenes, StructuralValidator(self.jobs_root.parent)).import_tar(stream)

    def import_staged(self, path, *, allowed_root, limits=None):
        """Publish only a server-staged directory below the configured staging root."""
        from packages.library.imports import LibraryImports
        from packages.library.scenes import SceneLibrary
        from packages.library.validation import StructuralValidator

        scenes = SceneLibrary(
            builtin_root=self.roots["builtin"], user_root=self.roots["user"], state_root=self.jobs_root.parent
        )
        return LibraryImports(self, scenes, StructuralValidator(self.jobs_root.parent)).import_staged(
            path, allowed_root=allowed_root, limits=limits
        )

    def _complete(self, version, deduplicated, *, kind="asset"):
        job = {
            "id": uuid.uuid4().hex,
            "status": "succeeded",
            "kind": kind,
            "createdAt": datetime.now(UTC).isoformat(),
            f"{kind}Version": version.id,
            "contentHash": version.content_hash,
            "deduplicated": deduplicated,
            "validation": deepcopy(version.report()),
        }
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.jobs_root, delete=False) as output:
            json.dump(job, output, ensure_ascii=False)
        Path(output.name).replace(self.jobs_root / f"{job['id']}.json")
        return job

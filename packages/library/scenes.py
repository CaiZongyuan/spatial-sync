"""GPU-free scene discovery. Filesystem roots are operator configuration, never API inputs."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import quote

from packages.library.archive import indexed_file, package_files
from packages.library.common import BUILTIN_ROOT, NAME, TYPE_ROOT, LibraryError, content_hash, document
from packages.library.common import REPO_ROOT as REPO_ROOT
from packages.library.metadata import scene_metadata
from packages.library.reports import ReportStore, launchable, parse_report, pinned_assets
from packages.library.reports import validation_status as validation_status
from packages.library.templates import TemplateError, load_snapshots, references, resolve, verify_snapshot


def builtin_scene_path(name):
    return BUILTIN_ROOT / "scenes" / name / "1"


def visual_url(snapshot, filename):
    return f"/api/v1/library/files/{snapshot['root']}/" + quote(
        f"assets/{snapshot['id'].replace('@', '/')}/{filename}", safe="/"
    )


@dataclass(frozen=True)
class SceneVersion:
    name: str
    rev: int
    path: Path
    root: str
    metadata: dict
    bindings: dict
    locations: dict
    content_hash: str
    validation: dict | None
    reports: ReportStore | None = None
    files: tuple[str, ...] = ()
    template_assets: dict = field(default_factory=dict)
    template_error: str | None = None
    runtime_assets: dict = field(default_factory=dict)
    asset_error: str | None = None

    @property
    def id(self):
        return f"{self.name}@{self.rev}"

    @property
    def builder(self):
        return self.metadata["stage"].removeprefix("python:")

    def url(self, relative):
        return f"/api/v1/library/files/{self.root}/" + quote(
            f"scenes/{self.name}/{self.rev}/{relative}", safe="/"
        )

    @property
    def launchable(self):
        return self.template_error is None and self.asset_error is None and launchable(self.report())

    def report(self):
        return (
            self.reports.latest(f"scene:{self.id}", self.content_hash, self.validation)
            if self.reports
            else (self.validation)
        )

    def template_dependencies(self):
        if self.template_error:
            raise TemplateError(self.template_error)
        selected = references(self.bindings)
        if set(selected) != set(self.template_assets):
            raise TemplateError("Template dependency snapshots are incomplete")
        for identifier in selected:
            snapshot = self.template_assets[identifier]
            if snapshot["id"] != identifier:
                raise TemplateError("Template dependency snapshot identity differs")
            verify_snapshot(snapshot)
        return [{"id": identifier, "contentHash": self.template_assets[identifier]["contentHash"]}
                for identifier in selected]

    def asset_dependencies(self):
        if self.asset_error:
            raise TemplateError(self.asset_error)
        for snapshot in self.runtime_assets.values():
            verify_snapshot(snapshot)
        return [{"id": identifier, "contentHash": snapshot["contentHash"]}
                for identifier, snapshot in sorted(self.runtime_assets.items())]

    def require_launchable(self):
        report = self.report()
        if not launchable(report):
            message = "Scene validation failed" if report is not None else "Validation report missing"
            raise LibraryError(
                "scene_not_launchable", message, 409, sceneVersion=self.id, validation=deepcopy(report)
            )
        try:
            self.template_dependencies()
            self.asset_dependencies()
        except (TemplateError, OSError, ValueError) as exc:
            raise LibraryError(
                "scene_not_launchable", str(exc), 409, sceneVersion=self.id, validation=deepcopy(report)
            ) from exc
        try:
            unchanged = content_hash(self.path) == self.content_hash
        except (OSError, ValueError) as exc:
            raise LibraryError(
                "scene_not_launchable",
                "Scene package is unavailable",
                409,
                sceneVersion=self.id,
            ) from exc
        if not unchanged:
            raise LibraryError(
                "scene_not_launchable",
                "Scene package changed after indexing; use a new version",
                409,
                sceneVersion=self.id,
            )

    def with_templates(self, roots):
        selected = []
        try:
            selected = references(self.bindings)
            usd = not self.metadata["stage"].startswith("python:")
            if not selected and not usd:
                return self
            expected = pinned_assets(self, "scene")
            identifiers = sorted(set(selected) | ({item["id"] for item in expected} if usd else set()))
            snapshots = load_snapshots(roots, identifiers, expected)
            for snapshot in snapshots.values():
                snapshot["root"] = next(name for name, root in roots.items()
                                        if Path(snapshot["path"]).is_relative_to(Path(root)))
            return replace(
                self, template_assets={identifier: snapshots[identifier] for identifier in selected},
                template_error=None, runtime_assets=snapshots if usd else {}, asset_error=None,
            )
        except (ValueError, OSError, LibraryError) as exc:
            if selected or self.metadata["stage"].startswith("python:"):
                return replace(self, template_assets={}, template_error=str(exc))
            return replace(self, runtime_assets={}, asset_error=str(exc))

    def runtime_payload(self):
        """Transport verified authoring scope to the Runner without importing its SDK."""
        self.require_launchable()
        from packages.sim_runtime.info_catalog import REGISTRY

        from .templates import config_description

        return {
            "kind": "scene", "metadata": deepcopy(self.metadata),
            "package": {"id": self.id, "path": str(self.path), "contentHash": self.content_hash,
                        "files": {name: str(self.path / name) for name in self.files}},
            "assets": deepcopy(list(self.runtime_assets.values())),
            "expectedAssets": pinned_assets(self, "scene"), "structure": deepcopy(self.report()),
            "programs": {name: config_description(program) for name, program in REGISTRY.items()},
        }

    def read_visual(self, url):
        """Read only a declared GLB in this version's verified asset snapshots."""
        for snapshot in self.runtime_assets.values():
            visual = snapshot["metadata"].get("visual")
            if visual and url == visual_url(snapshot, visual):
                verify_snapshot(snapshot)
                return Path(snapshot["files"][visual]).read_bytes()
        raise KeyError(url)

    def declarations(self, source):
        if self.template_error:
            raise TemplateError(self.template_error)
        return resolve(self.bindings, source, self.template_assets)

    def summary(self):
        report = self.report()
        allowed = self.launchable
        gaps = [
            check
            for check in (report or {}).get("checks", [])
            if check["status"] != "pass" and not check.get("notApplicable")
        ]
        untyped = (report or {}).get("untypedEntities", [])
        if self.template_error:
            gaps.append({"kind": "template_dependency", "message": self.template_error})
        if self.asset_error:
            gaps.append({"kind": "asset_dependency", "message": self.asset_error})
        return {
            "sceneId": self.name,
            "sceneVersion": self.id,
            "id": self.id,
            "rev": self.rev,
            "name": self.metadata["title"],
            "title": self.metadata["title"],
            "description": self.metadata.get("description", ""),
            "sources": self.metadata["sources"],
            "launchable": allowed,
            "contentHash": self.content_hash,
            "root": self.root,
            "stage": self.metadata["stage"],
            "launchReason": None
            if allowed
            else (
                self.template_error or self.asset_error
                or (
                    "缺少验证报告"
                    if report is None
                    else "; ".join(
                        check["message"] for check in report["checks"] if check["status"] == "fail"
                    )
                    or "验证报告标记此版本不可启动"
                )
            ),
            "validationStatus": validation_status(report),
            "entityCount": (report or {}).get("entityCount"),
            "deviceCount": (report or {}).get("bindingCount"),
            "gapCount": len(gaps) + len(untyped) if report else None,
            "gaps": deepcopy(gaps)
            + [
                {"kind": "untyped_entity", "entityKey": key, "message": f"Untyped entity: {key}"}
                for key in untyped
            ],
            "countsReason": (report or {}).get("inspection", "Physical entities have not been inspected"),
            "thumbnailUrl": next(
                (
                    self.url(name)
                    for name in self.files
                    if name.startswith("previews/") and Path(name).suffix.lower() in (".png", ".jpg", ".webp")
                ),
                None,
            ),
        }


class SceneLibrary:
    def __init__(self, builtin_root=BUILTIN_ROOT, user_root=None, state_root=None):
        self.roots = {
            "builtin": Path(builtin_root).resolve(),
            "user": Path(
                user_root if user_root is not None else os.getenv("LW_LIBRARY", "~/.spatial-sync/library")
            )
            .expanduser()
            .resolve(),
        }
        self.reports = ReportStore(
            state_root
            if state_root is not None
            else Path(os.getenv("SPATIAL_STATE_ROOT", "~/.spatial-sync/state")).expanduser()
        )
        self.versions, self.files = {}, {}
        self.refresh()

    def refresh(self):
        versions, files = {}, {}
        for root_name, root in self.roots.items():
            for metadata_file in sorted((root / "scenes").glob("*/*/scene.yaml")):
                path = metadata_file.parent.resolve()
                if not path.is_relative_to(root.resolve()):
                    raise ValueError(f"Scene package escapes library root: {metadata_file}")
                try:
                    data, bindings, locations = scene_metadata(path)
                except LibraryError as exc:
                    problems = "; ".join(f"{p['path']}: {p['message']}" for p in exc.detail["problems"])
                    raise ValueError(f"Invalid scene package: {problems}") from exc
                name, rev = metadata_file.parent.parent.name, metadata_file.parent.name
                if (
                    not NAME.fullmatch(name)
                    or not rev.isdecimal()
                    or int(rev) < 1
                    or str(int(rev)) != rev
                    or data.get("name") != name
                ):
                    raise ValueError(f"Invalid scene package identity: {metadata_file}")
                report_file = path / "validation.json"
                report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else None
                if report is not None:
                    try:
                        report = parse_report(report, f"scene:{name}@{int(rev)}")
                    except ValueError as exc:
                        raise ValueError(f"Invalid scene validation report: {report_file}") from exc
                version = SceneVersion(
                    name,
                    int(rev),
                    path,
                    root_name,
                    data,
                    bindings,
                    locations,
                    content_hash(path),
                    report,
                    self.reports,
                    tuple(package_files(path)),
                )
                if version.id in versions:
                    raise ValueError(f"Conflicting scene version {version.id} in built-in and user libraries")
                previous = self.versions.get(version.id)
                if previous is not None and previous.content_hash != version.content_hash:
                    raise LibraryError(
                        "scene_not_launchable",
                        "Scene package changed after indexing",
                        409,
                        sceneVersion=version.id,
                    )
                versions[version.id] = version
                for relative in version.files:
                    files[(root_name, f"scenes/{name}/{rev}/{relative}")] = (version, relative)
        self.versions, self.files = versions, files

    def indexed_file(self, root, relative):
        return indexed_file(self.roots, self.files, root, relative, changed="scene_not_launchable")

    def list(self, query=None, launchable=None):
        versions = sorted(
            (version.with_templates(self.roots) for version in self.versions.values()),
            key=lambda version: (version.name, version.rev),
        )
        return [
            deepcopy(version.summary())
            for version in versions
            if (
                (launchable is None or version.launchable is launchable)
                and (not query or query.casefold() in f"{version.id} {version.metadata['title']}".casefold())
            )
        ]

    def resolve(self, selector, *, launch=False, source=None):
        name, separator, rev = selector.partition("@")
        if not NAME.fullmatch(name) or (separator and (not rev.isdecimal() or int(rev) < 1)):
            raise LibraryError("unknown_version", "Unknown scene version")
        matches = [
            version.with_templates(self.roots)
            for version in self.versions.values()
            if version.name == name and (not separator or version.rev == int(rev))
        ]
        if not matches:
            raise LibraryError("unknown_version", f"Unknown scene version: {selector}")
        if launch and not separator:
            matches = [version for version in matches if version.launchable]
        if not matches:
            raise LibraryError("scene_not_launchable", "No launchable scene version", 409)
        version = max(matches, key=lambda item: item.rev)
        if launch:
            version.require_launchable()
        if source is not None and source not in version.metadata["sources"]:
            raise LibraryError("unknown_scene", "Scene/source combination is not registered")
        return version

    def describe(self, selector):
        version = self.resolve(selector)
        bindings = version.bindings["entities"]
        report = version.report() or {}
        effective = report.get("effectiveBindings", {})
        templated = any(binding.get("template") for binding in bindings)
        effective_bindings = effective.get(version.metadata["sources"][0], []) if templated else []
        display_bindings = effective_bindings or bindings
        if not templated:
            template_status = "not_required"
        elif version.template_error or any(
            check["id"] == "S5" and check["status"] == "fail" for check in report.get("checks", [])
        ):
            template_status = "failed"
        else:
            template_status = "resolved" if effective else "not_recorded"
        definitions = {
            value["type"]: value for file in sorted(TYPE_ROOT.glob("*.yaml")) if (value := document(file))
        }
        devices = [
            deepcopy(binding) | {"actions": definitions.get(binding.get("type"), {}).get("actions", {})}
            for binding in display_bindings
        ]
        return deepcopy(
            version.summary()
            | {
                "metadata": version.metadata,
                "entities": bindings,
                "rawBindings": version.bindings,
                "effectiveBindings": effective,
                "templateResolution": {
                    "status": template_status,
                    "error": version.template_error,
                },
                "devices": devices,
                "types": {
                    binding["type"]: definitions[binding["type"]]
                    for binding in display_bindings
                    if binding.get("type") in definitions
                },
                "locations": version.locations["locations"],
                "validation": version.report(),
                "inspection": "Authored bindings retained; effective bindings from recorded validation",
                "assets": (version.report() or {}).get("referencedAssets", []),
                "files": {relative: version.url(relative) for relative in version.files},
            }
        )

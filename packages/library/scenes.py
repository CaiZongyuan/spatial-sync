"""GPU-free scene discovery. Filesystem roots are operator configuration, never API inputs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
from copy import deepcopy
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path

import yaml

from packages.sim_runtime.scenes import SCENE_CONFIGS

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILTIN_ROOT = REPO_ROOT / "library"
TYPE_ROOT = REPO_ROOT / "devices/types"
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PYTHON_SOURCES = {"lab-demo": ("synthetic",), **{name: ("newton",) for name in SCENE_CONFIGS}}


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


def builtin_scene_path(name):
    return BUILTIN_ROOT / "scenes" / name / "1"


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


def validation_status(report):
    if report is None:
        return "missing"
    for tool, expected in report.get("toolVersions", {}).items():
        try:
            actual = platform.python_version() if tool == "python" else installed_version(tool)
        except PackageNotFoundError:
            return "stale"
        if actual != expected:
            return "stale"
    return "available"


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

    @property
    def id(self):
        return f"{self.name}@{self.rev}"

    @property
    def builder(self):
        return self.metadata["stage"].removeprefix("python:")

    @property
    def launchable(self):
        return self.validation is not None and self.validation["launchable"] and not any(
            check.get("status") == "fail" for check in self.validation.get("checks", [])
        )

    def summary(self):
        return {
            "sceneId": self.name, "sceneVersion": self.id, "id": self.id, "rev": self.rev,
            "name": self.metadata["title"], "title": self.metadata["title"],
            "description": self.metadata.get("description", ""), "sources": self.metadata["sources"],
            "launchable": self.launchable, "contentHash": self.content_hash, "root": self.root,
            "validationStatus": validation_status(self.validation),
            "entityCount": None, "deviceCount": None, "gapCount": None,
            "countsReason": "Physical entities and full validation gaps have not been inspected",
        }


class SceneLibrary:
    def __init__(self, builtin_root=BUILTIN_ROOT, user_root=None):
        roots = (("builtin", Path(builtin_root)), ("user", Path(
            user_root if user_root is not None else os.getenv("LW_LIBRARY", "~/.spatial-sync/library")
        ).expanduser()))
        self.versions = {}
        for root_name, root in roots:
            for metadata_file in sorted((root / "scenes").glob("*/*/scene.yaml")):
                path = metadata_file.parent.resolve()
                if not path.is_relative_to(root.resolve()):
                    raise ValueError(f"Scene package escapes library root: {metadata_file}")
                data = document(metadata_file)
                name, rev = metadata_file.parent.parent.name, metadata_file.parent.name
                if (not NAME.fullmatch(name) or not rev.isdecimal() or int(rev) < 1 or str(int(rev)) != rev
                        or data.get("name") != name):
                    raise ValueError(f"Invalid scene package identity: {metadata_file}")
                if not isinstance(data.get("title"), str) or not data["title"].strip():
                    raise ValueError(f"Scene package requires a title: {metadata_file}")
                if not isinstance(data.get("sources"), list) or not data["sources"] or any(
                    not isinstance(source, str) or source not in ("synthetic", "newton")
                    for source in data["sources"]
                ) or len(data["sources"]) != len(set(data["sources"])):
                    raise ValueError(f"Invalid scene sources: {metadata_file}")
                stage = data.get("stage")
                builder = stage.removeprefix("python:") if isinstance(stage, str) else ""
                if (not isinstance(stage, str) or not stage.startswith("python:")
                        or builder not in PYTHON_SOURCES):
                    raise ValueError(f"Unknown registered Python builder: {metadata_file}")
                if any(source not in PYTHON_SOURCES[builder] for source in data["sources"]):
                    raise ValueError(f"Python builder/source combination is not registered: {metadata_file}")
                simulation = data.get("simulation", {})
                if not isinstance(simulation, dict) or set(simulation) - {"solver", "warmupSteps"}:
                    raise ValueError(f"Unsupported scene simulation settings: {metadata_file}")
                if simulation and "newton" not in data["sources"]:
                    raise ValueError(f"Newton simulation settings require a Newton source: {metadata_file}")
                if "solver" in simulation and simulation["solver"] not in ("xpbd", "mujoco", "featherstone"):
                    raise ValueError(f"Unknown scene solver: {metadata_file}")
                if "warmupSteps" in simulation and (
                    isinstance(simulation["warmupSteps"], bool)
                    or not isinstance(simulation["warmupSteps"], int) or simulation["warmupSteps"] < 1
                ):
                    raise ValueError(f"warmupSteps must be a positive integer: {metadata_file}")
                bindings = document(path / "devices.yaml")
                locations = document(path / "locations.yaml")
                if bindings.get("scene") != name or not isinstance(bindings.get("entities"), list):
                    raise ValueError(f"Invalid scene binding document: {path / 'devices.yaml'}")
                if not isinstance(locations.get("locations"), dict):
                    raise ValueError(f"Invalid teach locations document: {path / 'locations.yaml'}")
                report_file = path / "validation.json"
                report = json.loads(report_file.read_text(encoding="utf-8")) if report_file.exists() else None
                if report is not None and (
                    not isinstance(report, dict) or report.get("schema") != "lw.validation/1"
                    or report.get("subject") != f"scene:{name}@{int(rev)}"
                    or not isinstance(report.get("launchable"), bool)
                    or not isinstance(report.get("toolVersions", {}), dict)
                    or any(not isinstance(tool, str) or not isinstance(value, str)
                           for tool, value in report.get("toolVersions", {}).items())
                    or not isinstance(report.get("checks"), list)
                    or any(not isinstance(check, dict) or check.get("status") not in ("pass", "warn", "fail")
                           for check in report["checks"])
                ):
                    raise ValueError(f"Invalid scene validation report: {report_file}")
                version = SceneVersion(
                    name, int(rev), path, root_name, data, bindings, locations, content_hash(path), report
                )
                if version.id in self.versions:
                    raise ValueError(f"Conflicting scene version {version.id} in built-in and user libraries")
                self.versions[version.id] = version

    def list(self, query=None, launchable=None):
        versions = sorted(self.versions.values(), key=lambda version: (version.name, version.rev))
        return [deepcopy(version.summary()) for version in versions if (
            (launchable is None or version.launchable is launchable)
            and (not query or query.casefold() in f"{version.id} {version.metadata['title']}".casefold())
        )]

    def resolve(self, selector, *, launch=False, source=None):
        name, separator, rev = selector.partition("@")
        if not NAME.fullmatch(name) or (separator and (not rev.isdecimal() or int(rev) < 1)):
            raise LibraryError("unknown_version", "Unknown scene version")
        matches = [version for version in self.versions.values() if version.name == name and (
            not separator or version.rev == int(rev)
        )]
        if not matches:
            raise LibraryError("unknown_version", f"Unknown scene version: {selector}")
        if launch and not separator:
            matches = [version for version in matches if version.launchable]
        if not matches:
            raise LibraryError("scene_not_launchable", "No launchable scene version", 409)
        version = max(matches, key=lambda item: item.rev)
        if launch and not version.launchable:
            message = (
                "Scene validation failed" if version.validation is not None else "Validation report missing"
            )
            raise LibraryError("scene_not_launchable", message, 409,
                               sceneVersion=version.id, validation=deepcopy(version.validation))
        if launch and content_hash(version.path) != version.content_hash:
            raise LibraryError(
                "scene_not_launchable", "Scene package changed after indexing; use a new version", 409,
                sceneVersion=version.id,
            )
        if source is not None and source not in version.metadata["sources"]:
            raise LibraryError("unknown_scene", "Scene/source combination is not registered")
        return version

    def describe(self, selector):
        version = self.resolve(selector)
        bindings = version.bindings["entities"]
        definitions = {value["type"]: value for file in sorted(TYPE_ROOT.glob("*.yaml"))
                       if (value := document(file))}
        devices = [deepcopy(binding) | {"actions": definitions[binding["type"]]["actions"]}
                   for binding in bindings]
        return deepcopy(version.summary() | {
            "metadata": version.metadata, "entities": bindings, "devices": devices,
            "types": {binding["type"]: definitions[binding["type"]] for binding in bindings},
            "locations": version.locations["locations"],
            "validation": version.validation,
            "inspection": "declarative bindings; match selectors are unexpanded",
            "assets": [],
        })

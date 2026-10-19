"""Immutable uploaded asset packages. No physics engine or host-path API inputs."""

from __future__ import annotations

import json
import math
import os
import re
import tarfile
import tempfile
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version as installed_version
from pathlib import Path, PurePosixPath
from typing import Literal
from urllib.parse import quote

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)

from packages.library.scenes import BUILTIN_ROOT, NAME, TYPE_ROOT, LibraryError, content_hash, document

KINDS = {"robot", "device", "labware", "environment"}
MAX_UPLOAD_BYTES = 128 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
MAX_FILES = 4096
RESERVED = re.compile(r"(?:con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", re.IGNORECASE)


def invalid(message, *, path="", **details):
    return LibraryError(
        "import_invalid",
        "Invalid asset package",
        422,
        problems=[{"path": path, "message": message}],
        **details,
    )


def relative_path(value):
    """One portable path spelling, including when the gateway runs on Windows."""
    if not isinstance(value, str) or not value or unicodedata.normalize("NFC", value) != value:
        raise ValueError("Expected a nonempty NFC relative path")
    parts = value.split("/")
    if any(
        part in ("", ".", "..")
        or part.endswith((".", " "))
        or RESERVED.fullmatch(part)
        or any(character in '<>:"\\|?*' or ord(character) < 32 for character in part)
        for part in parts
    ):
        raise ValueError("Unsafe or ambiguous package path")
    return PurePosixPath(value)


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Physics(Schema):
    massKg: float | None = Field(default=None, gt=0)
    massSource: Literal["measured", "datasheet", "estimated"] | None = None
    collision: Literal["auto", "authored"] | None = None


class BindingTemplate(Schema):
    type: str = Field(min_length=1)
    programs: dict[str, str] = Field(default_factory=dict)
    config: dict[str, JsonValue] = Field(default_factory=dict)


class AssetMetadata(Schema):
    id: str
    kind: str
    title: str = Field(min_length=1)
    description: str = ""
    stage: str
    visual: str | None = None
    dimensionsM: list[float] | None = None
    physics: Physics | None = None
    implements: list[BindingTemplate] = Field(default_factory=list)
    dressing: bool = False

    @model_validator(mode="after")
    def package_identity(self):
        kind, separator, name = self.id.partition("/")
        if not separator or kind not in KINDS or kind != self.kind or not NAME.fullmatch(name):
            raise ValueError("id must be <kind>/<lowercase-name>, without a revision; kind must agree")
        if not self.title.strip():
            raise ValueError("title cannot be blank")
        if relative_path(self.stage).suffix not in (".usd", ".usda", ".usdc"):
            raise ValueError("stage must name a package-local USD file")
        if self.visual is not None and relative_path(self.visual).suffix != ".glb":
            raise ValueError("visual must name a package-local GLB file")
        if self.dimensionsM is not None and (
            len(self.dimensionsM) != 3
            or any(not math.isfinite(value) or value <= 0 for value in self.dimensionsM)
        ):
            raise ValueError("dimensionsM must contain three positive finite numbers")
        return self


def metadata(path):
    try:
        if (path / "asset.yaml").stat().st_size > 1024 * 1024:
            raise ValueError("asset.yaml exceeds 1 MiB")
        data = document(path / "asset.yaml")
        value = AssetMetadata.model_validate(data)
    except ValidationError as exc:
        raise LibraryError(
            "import_invalid",
            "Invalid asset metadata",
            422,
            problems=[
                {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                for error in exc.errors(include_input=False, include_url=False)
            ],
        ) from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise invalid("asset.yaml must be a valid metadata mapping", path="asset.yaml") from exc
    files = set(package_files(path))
    if "asset.yaml" not in files:
        raise invalid("Package metadata must be named exactly asset.yaml", path="asset.yaml")
    for entry in path.iterdir():
        if entry.name.casefold() == "validation.json" and (
            entry.name != "validation.json" or not entry.is_file()
        ):
            raise invalid("validation.json is reserved for gateway-generated reports", path="validation.json")
    for field in ("stage", "visual"):
        filename = getattr(value, field)
        if filename is not None and filename not in files:
            raise invalid(
                "Referenced file is missing or its path case does not match the package", path=field
            )
    return data


def package_files(path):
    files, names = [], {}
    for entry in sorted(path.rglob("*")):
        relative = entry.relative_to(path).as_posix()
        relative_path(relative)
        key = relative.casefold()
        if (
            key in names
            or entry.is_symlink()
            or entry.is_junction()
            or not entry.resolve().is_relative_to(path.resolve())
        ):
            raise ValueError("Package contains links or ambiguous filenames")
        names[key] = relative
        if entry.is_file():
            files.append(relative)
        elif not entry.is_dir():
            raise ValueError("Package contains a nonregular file")
    return files


def extract_package(stream, path):
    names, entries, files, size = {}, set(), set(), 0
    try:
        # Uncompressed streaming tar keeps metadata reads inside the upload byte budget.
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for index, entry in enumerate(archive):
                if index >= MAX_FILES:
                    raise invalid(f"Package exceeds {MAX_FILES} entries")
                relative = entry.name.removesuffix("/") if entry.isdir() else entry.name
                try:
                    parts = relative_path(relative).parts
                except ValueError as exc:
                    raise invalid(str(exc), path=entry.name) from exc
                key = relative.casefold()
                if key in entries or any(
                    "/".join(parts[:i]).casefold() in files for i in range(1, len(parts))
                ):
                    raise invalid("Duplicate or conflicting archive path", path=relative)
                for i in range(1, len(parts) + 1):
                    prefix = "/".join(parts[:i])
                    if prefix.casefold() in names and names[prefix.casefold()] != prefix:
                        raise invalid("Case aliases are not portable", path=relative)
                    names.setdefault(prefix.casefold(), prefix)
                entries.add(key)
                if (not entry.isdir() and not entry.isreg()) or entry.sparse is not None:
                    raise invalid("Only regular files and directories are accepted", path=relative)
                target = path.joinpath(*parts)
                if entry.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if any(name.startswith(key + "/") for name in names):
                    raise invalid("File conflicts with an archive directory", path=relative)
                size += entry.size
                if entry.size < 0 or size > MAX_PACKAGE_BYTES:
                    raise invalid("Expanded package exceeds 256 MiB")
                files.add(key)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(entry) as source, target.open("xb") as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise invalid("Unreadable tar package") from exc


@contextmanager
def import_lock(root):
    """Serialize revision allocation across workers using the same user library."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".import.lock"
    if lock_path.is_symlink() or lock_path.is_junction():
        raise invalid("Library import lock cannot be a filesystem link")
    with lock_path.open("a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def gaps(data, path):
    missing = ["structure_validation", "physics_validation"]
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

    @property
    def bare_id(self):
        return self.id.partition("@")[0]

    def url(self, relative):
        return f"/api/v1/library/files/{self.root}/" + quote(
            f"assets/{self.bare_id}/{self.rev}/{relative}", safe="/"
        )

    def summary(self):
        missing = gaps(self.metadata, self.path)
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
            "root": self.root,
            "contentHash": self.content_hash,
            "gaps": missing,
            "gapCount": len(missing),
            "completeness": {"status": "warning" if missing else "complete", "gaps": missing},
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
                if report is not None and (
                    not isinstance(report, dict)
                    or report.get("schema") != "lw.validation/1"
                    or report.get("subject") != f"asset:{version_id}"
                ):
                    raise ValueError(f"Invalid asset validation report: {report_path}")
                version = AssetVersion(
                    version_id, int(rev), root_name, path, data, content_hash(path), report, indexed
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

    def _asset_directory(self, bare_id, *, create=False):
        root = self.roots["user"]
        path = root
        # Check existing parents before mkdir/rename, including Windows junctions.
        # The library is operator-owned; archive members cannot introduce these links.
        for part in ("assets", *bare_id.split("/")):
            path = path / part
            if (
                path.is_symlink()
                or path.is_junction()
                or not path.resolve().is_relative_to(root)
                or (path.exists() and not path.is_dir())
            ):
                raise invalid("Library destination contains a link or invalid ancestor")
            if create:
                path.mkdir(exist_ok=True)
        return path

    def list(self, kind=None, query=None, only_gaps=False):
        return [
            deepcopy(version.summary())
            for version in sorted(self.versions.values(), key=lambda item: (item.bare_id, item.rev))
            if (not kind or version.metadata["kind"] == kind)
            and (not query or query.casefold() in f"{version.id} {version.metadata['title']}".casefold())
            and (not only_gaps or gaps(version.metadata, version.path))
        ]

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

    def describe(self, selector):
        version = self.resolve(selector)
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
        return deepcopy(
            version.summary()
            | {
                "metadata": version.metadata,
                "validation": version.validation,
                "validationCoverage": "S1 schema only; remaining structure and physics checks have not run",
                "provenance": provenance,
                "referencingScenes": [],
                "types": {
                    binding["type"]: definitions.get(binding["type"])
                    for binding in version.metadata.get("implements", [])
                },
                "files": {relative: version.url(relative) for relative in version.files},
            }
        )

    def indexed_file(self, root, relative):
        match = self.files.get((root, relative))
        if match is None:
            raise LibraryError("unknown_file", "Unknown library file")
        version, filename = match
        try:
            path = self.roots[root]
            for part in version.path.relative_to(path).parts:
                path = path / part
                if path.is_symlink() or path.is_junction():
                    raise ValueError("Package ancestor changed to a filesystem link")
            package_files(version.path)
            if content_hash(version.path) != version.content_hash:
                raise ValueError("Changed package")
        except (OSError, ValueError) as exc:
            raise LibraryError("asset_changed", "Asset package changed after indexing", 409) from exc
        return version.path / filename

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
        root = self.roots["user"]
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temporary:
            stage = Path(temporary) / "package"
            stage.mkdir()
            extract_package(stream, stage)
            data = metadata(stage)
            upload_hash = content_hash(stage)
            with self._lock, import_lock(root):
                self._asset_directory(data["id"])
                self.startup()
                existing = next(
                    (
                        version
                        for version in self.versions.values()
                        if version.bare_id == data["id"]
                        and upload_hash
                        in (
                            version.content_hash,
                            (version.validation or {}).get("importContentHash"),
                        )
                    ),
                    None,
                )
                if existing is not None:
                    return self._complete(existing, True)
                if (stage / "validation.json").exists():
                    raise invalid(
                        "validation.json is generated by the gateway; omit it from a new package",
                        path="validation.json",
                    )
                revision = 1 + max(
                    (version.rev for version in self.versions.values() if version.bare_id == data["id"]),
                    default=0,
                )
                version_id = f"{data['id']}@{revision}"
                report = {
                    "schema": "lw.validation/1",
                    "subject": f"asset:{version_id}",
                    "ranAt": datetime.now(UTC).isoformat(),
                    "toolVersions": {tool: installed_version(tool) for tool in ("pydantic", "pyyaml")},
                    "importContentHash": upload_hash,
                    "launchable": False,
                    "checks": [
                        {
                            "id": "S1",
                            "tier": "structure",
                            "status": "pass",
                            "message": "asset.yaml matches the asset package schema",
                        },
                        {
                            "id": "coverage",
                            "tier": "structure",
                            "status": "warn",
                            "message": "Remaining structure and physics checks have not run",
                        },
                    ],
                }
                (stage / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                destination = self._asset_directory(data["id"], create=True) / str(revision)
                if destination.exists() or destination.is_symlink() or destination.is_junction():
                    raise invalid("Revision destination already exists")
                stage.rename(destination)
                self.startup()
                return self._complete(self.versions[version_id], False)

    def _complete(self, version, deduplicated):
        job = {
            "id": uuid.uuid4().hex,
            "status": "succeeded",
            "kind": "asset",
            "createdAt": datetime.now(UTC).isoformat(),
            "assetVersion": version.id,
            "contentHash": version.content_hash,
            "deduplicated": deduplicated,
            "validation": deepcopy(version.validation),
        }
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.jobs_root, delete=False) as output:
            json.dump(job, output, ensure_ascii=False)
        Path(output.name).replace(self.jobs_root / f"{job['id']}.json")
        return job

"""Versioned validation documents and append-only reports outside immutable packages."""

from __future__ import annotations

import json
import platform
import re
import tempfile
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, model_validator

from packages.library.templates import template_id

SUBJECT = re.compile(
    r"(?:scene:[a-z0-9]+(?:-[a-z0-9]+)*|asset:(?:robot|device|labware|environment)/"
    r"[a-z0-9]+(?:-[a-z0-9]+)*)@[1-9][0-9]*"
)
HASH = re.compile(r"[a-f0-9]{64}")


class Check(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    id: str = Field(min_length=1)
    tier: Literal["structural", "structure", "physics"]
    status: Literal["pass", "warn", "fail"]
    message: str = Field(min_length=1)
    entityKey: str | None = None
    location: str | None = None
    notApplicable: bool = False


class CompletenessItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Literal["collision", "massInertia", "visual", "license", "jointSites", "types"]
    status: Literal["present", "warning", "missing", "not_applicable"]
    message: str = Field(min_length=1)


class PhysicsEvidence(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    reportId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    ranAt: AwareDatetime | None = None
    toolVersions: dict[str, str] = Field(default_factory=dict)


class PhysicsLayer(BaseModel):
    """Execution provenance is separate from each check's unchanged outcome enum."""

    model_config = ConfigDict(extra="allow", strict=True)
    status: Literal["completed", "skipped"]
    reason: str | None = None
    missingTools: list[str] = Field(default_factory=list)
    device: Literal["cpu"] | None = None
    ranAt: AwareDatetime | None = None
    toolVersions: dict[str, str] = Field(default_factory=dict)
    retainedFrom: PhysicsEvidence | None = None

    @model_validator(mode="after")
    def execution_identity(self):
        if self.status == "skipped" and not (self.reason and self.reason.strip()):
            raise ValueError("Skipped physics requires a reason")
        if self.status == "completed" and (
            self.device != "cpu" or self.ranAt is None or not self.toolVersions.get("newton")
        ):
            raise ValueError("Completed physics requires its actual CPU execution identity")
        if len(set(self.missingTools)) != len(self.missingTools) or any(
            not name.strip() for name in self.missingTools
        ):
            raise ValueError("Missing tool observations must be nonempty and unique")
        return self


class ValidationReport(BaseModel):
    """lw.validation/1 includes its original partial and timestamp-free reports."""

    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False, populate_by_name=True)
    schema_: Literal["lw.validation/1"] = Field(alias="schema")
    subject: str = Field(pattern="^" + SUBJECT.pattern + "$", min_length=1)
    ranAt: AwareDatetime | None = None
    toolVersions: dict[str, str] = Field(default_factory=dict)
    checks: list[Check]
    launchable: StrictBool
    importContentHash: str | None = Field(default=None, pattern="^" + HASH.pattern + "$")
    contentHash: str | None = Field(default=None, pattern="^" + HASH.pattern + "$")
    reportId: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    completeness: list[CompletenessItem] | None = None
    untypedEntities: list[str] = Field(default_factory=list)
    physics: PhysicsLayer | None = None

    @model_validator(mode="after")
    def measured_physics(self):
        if self.physics and self.physics.status == "completed" and not any(
            item.tier == "physics" and item.id in PHYSICS_CHECKS
            and item.status in ("pass", "fail") and not item.notApplicable
            for item in self.checks
        ):
            raise ValueError("A physics placeholder is not a completed measurement")
        return self


PHYSICS_CHECKS = frozenset(f"P{i}" for i in range(1, 7))


def parse_report(value, subject=None):
    encoded = json.dumps(value, allow_nan=False)
    report = ValidationReport.model_validate_json(encoded)
    if subject is not None and report.subject != subject:
        raise ValueError("Validation report belongs to another version")
    # Preserve old timestamp spelling and absent optional fields as well as package bytes.
    return json.loads(encoded)


def launchable(report):
    return (
        report is not None
        and report["launchable"]
        and not any(check["status"] == "fail" for check in report["checks"])
    )


def pinned_assets(version, kind):
    """A known exact dependency is immutable across all bound, durable validation receipts."""
    subject = f"{kind}:{version.id}"
    history = version.reports.history(subject, version.content_hash) if version.reports else []
    pinned = {}
    for report in [version.validation, *history]:
        if report is None:
            continue
        parse_report(report, subject)
        entries = report.get("referencedAssets", [])
        if not isinstance(entries, list):
            raise ValueError("Invalid recorded asset dependencies")
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError("Invalid recorded asset dependency")
            identifier = template_id(item.get("id"))
            digest = item.get("contentHash")
            if digest is None:
                continue  # Legacy hashless references establish no historical byte identity.
            if not isinstance(digest, str) or not HASH.fullmatch(digest):
                raise ValueError("Invalid recorded asset hash: " + identifier)
            if identifier in pinned and pinned[identifier] != digest:
                raise ValueError("Conflicting recorded asset hashes: " + identifier)
            pinned[identifier] = digest
    return [{"id": name, "contentHash": pinned[name]} for name in sorted(pinned)]


_version_reads = ContextVar("validation_version_reads", default=None)


@contextmanager
def validation_read():
    """Read each tool's installed metadata once per browse, never across requests."""
    if _version_reads.get() is not None:
        yield
        return
    token = _version_reads.set({})
    try:
        yield
    finally:
        _version_reads.reset(token)


def _tool_version(tool):
    versions = _version_reads.get()
    if versions is not None and tool in versions:
        return versions[tool]
    try:
        actual = installed_version(tool)
    except PackageNotFoundError:
        actual = None
    if versions is not None:
        versions[tool] = actual
    return actual


def validation_status(report):
    if report is None:
        return "missing"
    physics = report.get("physics") or {}
    versions = [report.get("toolVersions", {}), physics.get("toolVersions", {}),
                (physics.get("retainedFrom") or {}).get("toolVersions", {})]
    for tool, expected in (item for group in versions for item in group.items()):
        actual = platform.python_version() if tool == "python" else _tool_version(tool)
        if actual != expected:
            return "stale"
    for tool in physics.get("missingTools", []):
        if _tool_version(tool) is not None:
            return "stale"  # The formerly unavailable worker can now be run explicitly.
    return "available"


def latest_physics_evidence(version, kind):
    """A skip cannot erase a bound measurement; genuinely new execution can replace it."""
    subject = f"{kind}:{version.id}"
    history = version.reports.history(subject, version.content_hash) if version.reports else []
    candidates = []
    for report in [version.validation, *history]:
        if report is None:
            continue
        parse_report(report, subject)
        layer = report.get("physics") or {}
        if layer.get("status") == "skipped":
            continue
        checks = [item for item in report["checks"] if item["tier"] == "physics"
                  and item["id"] in PHYSICS_CHECKS]
        # Individual legacy failures remain conservative evidence. The historical
        # P1-P6/warn placeholder establishes no measured result.
        if layer.get("status") == "completed" or any(item["status"] == "fail" for item in checks):
            candidates.append(report)
    earliest = datetime.min.replace(tzinfo=UTC)
    return max(candidates, key=lambda item: (
        datetime.fromisoformat(item["ranAt"]) if item.get("ranAt") else earliest,
        item.get("reportId") or "",
    ), default=None)


def merge_physics(structure, result, retained=None):
    """Compose fresh structural facts with completed or explicitly skipped physics."""
    from copy import deepcopy

    report = deepcopy(structure)
    layer = deepcopy(result["physics"])
    PhysicsLayer.model_validate_json(json.dumps(layer, allow_nan=False))
    checks = deepcopy(result["checks"])
    if layer["status"] == "skipped" and retained is not None:
        previous = [deepcopy(item) for item in retained["checks"]
                    if item["tier"] == "physics" and item["id"] in PHYSICS_CHECKS]
        present = {item["id"] for item in previous}
        checks = previous + [item for item in checks if item["id"] not in present]
        old_layer = retained.get("physics") or {}
        layer["retainedFrom"] = {
            "reportId": retained.get("reportId"),
            "ranAt": old_layer.get("ranAt", retained.get("ranAt")),
            "toolVersions": deepcopy(old_layer.get("toolVersions", retained.get("toolVersions", {}))),
        }
    if layer["status"] == "completed":
        report["toolVersions"] = report.get("toolVersions", {}) | layer.get("toolVersions", {})
    report["physics"] = layer
    report["checks"] = [item for item in report["checks"] if item["tier"] != "physics"] + checks
    report["launchable"] = report["launchable"] and not any(
        item["status"] == "fail" for item in report["checks"]
    )
    report["inspection"] = (
        "Recorded structure and Newton CPU physics checks" if layer["status"] == "completed"
        else "Recorded structure; physics was skipped: " + layer["reason"]
    )
    return parse_report(report, structure["subject"])


class ReportStore:
    def __init__(self, state_root):
        self.root = Path(state_root) / "validation"

    def directory(self, subject, content_hash):
        if not SUBJECT.fullmatch(subject) or not HASH.fullmatch(content_hash):
            raise ValueError("Invalid validation report identity")
        kind, selector = subject.split(":", 1)
        return self.root / kind / selector.replace("@", "/") / content_hash

    def latest(self, subject, content_hash, embedded=None):
        directory = self.directory(subject, content_hash)
        pointer = directory / "latest.json"
        if not pointer.is_file():
            return embedded
        report_id = json.loads(pointer.read_text(encoding="utf-8"))["reportId"]
        if not isinstance(report_id, str) or not re.fullmatch(r"[a-f0-9]{32}", report_id):
            raise ValueError("Invalid validation history pointer")
        value = self._read(directory, report_id, subject, content_hash)
        if value is None:
            raise ValueError("Validation history does not match immutable package")
        return value

    @staticmethod
    def _read(directory, report_id, subject, content_hash):
        value = json.loads((directory / f"{report_id}.json").read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Invalid validation history document")
        if (
            value.get("subject") != subject
            or value.get("contentHash") != content_hash
            or value.get("reportId") != report_id
        ):
            return None
        return parse_report(value, subject)

    def history(self, subject, content_hash):
        directory = self.directory(subject, content_hash)
        return [
            value
            for path in sorted(directory.glob("*.json"))
            if re.fullmatch(r"[a-f0-9]{32}", path.stem)
            and (value := self._read(directory, path.stem, subject, content_hash)) is not None
        ]

    def save(self, report, content_hash):
        value = parse_report(report)
        value = parse_report(
            value
            | {
                "contentHash": content_hash,
                "reportId": uuid.uuid4().hex,
                "ranAt": datetime.now(UTC).isoformat(),
            }
        )
        directory = self.directory(value["subject"], content_hash)
        directory.mkdir(parents=True, exist_ok=True)
        self._write(directory / f"{value['reportId']}.json", value)
        self._write(directory / "latest.json", {"reportId": value["reportId"]})
        return value

    @staticmethod
    def _write(path, value):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

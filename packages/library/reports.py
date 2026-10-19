"""Versioned validation documents and append-only reports outside immutable packages."""

from __future__ import annotations

import json
import platform
import re
import tempfile
import uuid
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_version
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool

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


def pinned_assets(embedded, latest):
    """Keep published pins authoritative while retaining dependencies first inspected later."""
    pinned = {item["id"]: item for item in (latest or {}).get("referencedAssets", [])}
    pinned.update({item["id"]: item for item in (embedded or {}).get("referencedAssets", [])})
    return [pinned[name] for name in sorted(pinned)]


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
        value = parse_report(
            json.loads((directory / f"{report_id}.json").read_text(encoding="utf-8")), subject
        )
        if value.get("contentHash") != content_hash or value.get("reportId") != report_id:
            raise ValueError("Validation history does not match immutable package")
        return value

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

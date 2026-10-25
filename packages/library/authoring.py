"""External Authoring Reports: explicit, read-only summaries outside immutable packages.

A report is not a validation result. It records what an external factory said it
did, when it said it, and which imported version (if any) it refers to. The
platform validation report for that version remains authoritative; a later or
older authoring report never replaces it.

Storage is gateway-owned under ``<state>/authoring/reports``. Artifacts are
opaque references only: this module never opens a submitted path, stores an
attachment, or sends an external message.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from packages.library.common import LibraryError
from packages.library.reports import HASH
from packages.library.transactions import durable_json

AWARE = TypeAdapter(AwareDatetime)

SCHEMA = "lw.authoring-report/1"

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
SUMMARY_MAX_BYTES = 64 * 1024
GAPS_MAX = 100
GAP_MAX_BYTES = 2048
ARTIFACTS_MAX = 50
ARTIFACT_NAME_MAX = 128
ARTIFACT_REFERENCE_MAX = 1024
TEXT_MAX = 300
PREVIEW_CHARS = 2000
PREVIEW_GAPS = 5
LIST_MAX = 100
LIST_DEFAULT = 20

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def invalid_authoring(message):
    return LibraryError("invalid_authoring_report", message, 422)


def unknown_authoring():
    return LibraryError("unknown_authoring_report", "Unknown authoring report", 404)


def _utf8(value, name, limit):
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise invalid_authoring(f"{name} is not valid Unicode text") from None
    if len(encoded) > limit:
        raise invalid_authoring(f"{name} exceeds {limit} bytes")
    return len(encoded)


def _plain(value, name, *, limit=TEXT_MAX, allow_empty=False):
    if not isinstance(value, str) or _CONTROL.search(value) or "\x00" in value:
        raise invalid_authoring(f"{name} must be plain text without control characters")
    if not allow_empty and not value.strip():
        raise invalid_authoring(f"{name} must be nonempty")
    _utf8(value, name, limit)
    return value


class ObjectBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: StrictStr
    id: StrictStr
    contentHash: StrictStr

    @model_validator(mode="after")
    def exact_identity(self):
        if self.kind not in ("scene", "asset"):
            raise ValueError("object.kind must be scene or asset")
        _plain(self.id, "object.id")
        if not HASH.fullmatch(self.contentHash):
            raise ValueError("object.contentHash must be a sha256 hex digest")
        if not self.id.rpartition("@")[2].isdecimal():
            raise ValueError("object.id must name an exact @revision")
        return self


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: StrictStr
    reference: StrictStr

    @model_validator(mode="after")
    def bounded(self):
        _plain(self.name, "artifacts.name", limit=ARTIFACT_NAME_MAX)
        # Never resolved or read: only retained as the submitter's reference text.
        _plain(self.reference, "artifacts.reference", limit=ARTIFACT_REFERENCE_MAX)
        return self


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submissionId: StrictStr
    source: StrictStr
    sourceVersion: StrictStr
    reportedAt: AwareDatetime
    draftId: StrictStr | None = None
    object: ObjectBinding | None = None
    summary: StrictStr = ""
    gaps: list[StrictStr] = Field(default_factory=list)
    artifacts: list[ArtifactReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def bounded(self):
        if not ID.fullmatch(self.submissionId):
            raise ValueError("submissionId must be 1-100 letters, digits, '.', ':', '_' or '-'")
        _plain(self.source, "source")
        _plain(self.sourceVersion, "sourceVersion")
        if self.draftId is not None:
            _plain(self.draftId, "draftId")
        if self.draftId is None and self.object is None:
            raise ValueError("Bind the report to a draftId or an exact object version")
        _plain(self.summary, "summary", limit=SUMMARY_MAX_BYTES, allow_empty=True)
        if len(self.gaps) > GAPS_MAX:
            raise ValueError(f"gaps may list at most {GAPS_MAX} entries")
        for gap in self.gaps:
            _plain(gap, "gaps[]", limit=GAP_MAX_BYTES)
        if len(self.artifacts) > ARTIFACTS_MAX:
            raise ValueError(f"artifacts may list at most {ARTIFACTS_MAX} entries")
        return self


def _document(submission: Submission, received_at):
    return {
        "schema": SCHEMA,
        "submissionId": submission.submissionId,
        "source": submission.source,
        "sourceVersion": submission.sourceVersion,
        "reportedAt": submission.reportedAt.isoformat(),
        "receivedAt": received_at,
        "draftId": submission.draftId,
        "object": submission.object.model_dump() if submission.object else None,
        "summary": submission.summary,
        "summaryBytes": len(submission.summary.encode("utf-8")),
        "gaps": list(submission.gaps),
        "artifacts": [item.model_dump() for item in submission.artifacts],
    }


def _parse(document):
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        raise ValueError("Invalid authoring report document")
    report = dict(document)
    report.pop("schema", None)
    received_at = report.pop("receivedAt", None)
    report.pop("summaryBytes", None)
    submission = Submission.model_validate(report)
    if not isinstance(received_at, str):
        raise ValueError("Invalid authoring report receipt time")
    AWARE.validate_python(received_at)
    return _document(submission, received_at)


def _preview(report):
    summary = report["summary"]
    clipped = summary[:PREVIEW_CHARS]
    return {
        "schema": report["schema"],
        "submissionId": report["submissionId"],
        "source": report["source"],
        "sourceVersion": report["sourceVersion"],
        "reportedAt": report["reportedAt"],
        "receivedAt": report["receivedAt"],
        "draftId": report["draftId"],
        "object": report["object"],
        "summaryPreview": clipped,
        "summaryBytes": report["summaryBytes"],
        "summaryTruncated": len(summary) > len(clipped),
        "gapCount": len(report["gaps"]),
        "gapsPreview": report["gaps"][:PREVIEW_GAPS],
        "artifactCount": len(report["artifacts"]),
    }


def _matches_scene(report, selector):
    target = report.get("object")
    if not target or target.get("kind") != "scene":
        return False
    if "@" in selector:
        return target.get("id") == selector
    return target.get("id", "").partition("@")[0] == selector


class AuthoringReports:
    def __init__(self, state_root, assets, scenes, clock=None):
        self.root = Path(state_root) / "authoring" / "reports"
        self.assets = assets
        self.scenes = scenes
        self.clock = clock or (lambda: datetime.now(UTC).isoformat())
        self._lock = threading.Lock()

    def _path(self, submission_id):
        if not ID.fullmatch(submission_id):
            raise unknown_authoring()
        # The ID may contain ':' or differ only by case. Hashing gives every ID
        # a portable, case-sensitive storage name without changing the receipt.
        digest = hashlib.sha256(submission_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def _read(self, path):
        try:
            if path.is_symlink() or path.is_junction():
                raise ValueError("Authoring report storage cannot be a filesystem link")
            return _parse(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, ValidationError) as exc:
            raise LibraryError(
                "authoring_report_unavailable", "A stored authoring report is unreadable", 503
            ) from exc

    def _validate_binding(self, binding):
        if binding is None:
            return
        try:
            if binding.kind == "scene":
                library = self.scenes() if callable(self.scenes) else self.scenes
                version = library.resolve(binding.id)
            else:
                version = self.assets.resolve(binding.id)
        except LibraryError:
            raise invalid_authoring("Binding names no exact library version") from None
        if version.id != binding.id:
            raise invalid_authoring(
                "Binding must name the exact canonical version, not an alias revision"
            )
        if version.content_hash != binding.contentHash:
            raise invalid_authoring("Binding content hash does not match that library version")

    def submit(self, document):
        """Return the stored report and whether this call created it.

        ``submissionId`` is the idempotency key: a repeat never rewrites the
        first receipt, so a later platform product state cannot be replaced by
        replaying an old submission.
        """
        submission = Submission.model_validate(document)
        with self._lock:
            path = self._path(submission.submissionId)
            if path.is_file():
                report = self._read(path)
                if report["submissionId"] != submission.submissionId:
                    raise LibraryError(
                        "authoring_report_unavailable",
                        "A stored authoring report is unreadable",
                        503,
                    )
                return report, False
            self._validate_binding(submission.object)
            report = _document(submission, self.clock())
            self._write(path, report)
            return report, True

    def show(self, submission_id):
        path = self._path(submission_id)
        if not path.is_file():
            raise unknown_authoring()
        report = self._read(path)
        if report["submissionId"] != submission_id:
            raise LibraryError(
                "authoring_report_unavailable", "A stored authoring report is unreadable", 503
            )
        return report

    def list(self, *, scene=None, asset=None, draft=None, limit=LIST_DEFAULT):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            raise invalid_authoring("limit must be an integer from 1 to 100") from None
        if isinstance(limit, bool) or not 1 <= limit <= LIST_MAX:
            raise invalid_authoring("limit must be an integer from 1 to 100")
        for value, name in ((scene, "scene"), (asset, "asset"), (draft, "draft")):
            if value is not None:
                _plain(value, name)
        reports = []
        if self.root.is_dir():
            for path in sorted(self.root.glob("*.json")):
                if not re.fullmatch(r"[0-9a-f]{64}", path.stem):
                    continue
                if path.is_symlink() or path.is_junction():
                    continue
                report = self._read(path)
                if self._path(report["submissionId"]).name != path.name:
                    continue
                if scene is not None and not _matches_scene(report, scene):
                    continue
                if asset is not None:
                    target = report.get("object") or {}
                    if target.get("kind") != "asset" or (
                        target.get("id") != asset and "@" in asset
                    ) or ("@" not in asset and target.get("id", "").partition("@")[0] != asset):
                        continue
                if draft is not None and report.get("draftId") != draft:
                    continue
                reports.append(report)
        def sort_time(item):
            return (
                datetime.fromisoformat(item["reportedAt"]),
                datetime.fromisoformat(item["receivedAt"]),
            )

        reports.sort(key=sort_time, reverse=True)
        return [_preview(report) for report in reports[:limit]]

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.is_junction():
            raise invalid_authoring("Authoring report storage is unavailable")
        durable_json(path, value)

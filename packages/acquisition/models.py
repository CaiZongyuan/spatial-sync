"""Persistent acquisition records and the fixed SF2 limits.

The record schema is public (``lw.acquisition/1``): field names and statuses are
part of the frozen contract. Acquisition, import and usage stay separate facts:
``result`` describes the published local version, ``usage`` the bytes this
attempt moved, and the catalog keeps its own evidence.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA = "lw.acquisition/1"
BUDGET_SCHEMA = "lw.acquisition-budget/1"
RECIPE = "simready-newton-usdpreview-v1"

ID = re.compile(r"acq-[a-f0-9]{32}")
REF = re.compile(
    r"(?:catalog:)?[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*@[0-9a-f]{40}"
)
PACKAGE = re.compile(r"[a-z0-9][a-z0-9_.-]*")
REVISION = re.compile(r"[0-9a-f]{40}")
HASH = re.compile(r"[a-f0-9]{64}")

STATUSES = (
    "queued",
    "downloading",
    "preparing",
    "validating",
    "importing",
    "succeeded",
    "failed",
    "cancelled",
)
ACTIVE_STATUSES = frozenset(STATUSES[:5])
FINAL_STATUSES = frozenset(STATUSES[5:])
PHASES = ("queued", "downloading", "preparing", "validating", "importing")
ERROR_CODES = frozenset(
    {
        "source_invalid",
        "adaptation_gap",
        "import_invalid",
        "budget_exceeded",
        "source_unavailable",
        "lfs_pointer",
        "interrupted",
        "internal_error",
    }
)


def now():
    return datetime.now(UTC).isoformat()


def _mapping(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _string(value, name, *, pattern=None, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ValueError(f"{name} has an invalid spelling")
    return value


def _integer(value, name, *, nullable=False, minimum=0):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class Limits:
    """Platform defaults from the SF0 support boundary; never raised implicitly."""

    source_package_bytes: int = 512 * 1024 * 1024
    adapted_package_bytes: int = 512 * 1024 * 1024
    batch_bytes: int = 2 * 1024 * 1024 * 1024
    raw_cache_bytes: int = 10 * 1024 * 1024 * 1024
    free_disk_min_bytes: int = 10 * 1024 * 1024 * 1024
    concurrent_acquisitions: int = 2

    @classmethod
    def from_mapping(cls, value):
        if value is None:
            return cls()
        data = _mapping(value, "limits")
        fields = {
            "source_package_bytes": ("sourcePackageBytes", 1),
            "adapted_package_bytes": ("adaptedPackageBytes", 1),
            "batch_bytes": ("batchBytes", 1),
            "raw_cache_bytes": ("rawCacheBytes", 1),
            "free_disk_min_bytes": ("freeDiskMinBytes", 0),
            "concurrent_acquisitions": ("concurrentAcquisitions", 1),
        }
        values = {}
        for attribute, (key, minimum) in fields.items():
            if key in data:
                values[attribute] = _integer(data[key], f"limits.{key}", minimum=minimum)
        return cls(**values)

    def document(self):
        return {
            "sourcePackageBytes": self.source_package_bytes,
            "adaptedPackageBytes": self.adapted_package_bytes,
            "batchBytes": self.batch_bytes,
            "rawCacheBytes": self.raw_cache_bytes,
            "freeDiskMinBytes": self.free_disk_min_bytes,
        }

    def full_document(self):
        return self.document() | {"concurrentAcquisitions": self.concurrent_acquisitions}


def _validate_source(value):
    data = _mapping(value, "source")
    provider = _string(data.get("provider"), "source.provider")
    package = _string(data.get("packageId"), "source.packageId", pattern=PACKAGE)
    revision = _string(data.get("revision"), "source.revision", pattern=REVISION)
    recipe = _string(data.get("recipe"), "source.recipe")
    if recipe != RECIPE:
        raise ValueError("source.recipe is not the supported adaptation recipe")
    return {"provider": provider, "packageId": package, "revision": revision, "recipe": recipe}


def _validate_progress(value):
    data = _mapping(value, "progress")
    phase = _string(data.get("phase"), "progress.phase")
    if phase not in PHASES:
        raise ValueError("progress.phase is unknown")
    message = data.get("message")
    if message is not None and not isinstance(message, str):
        raise ValueError("progress.message must be a string or null")
    return {
        "phase": phase,
        "completedBytes": _integer(data.get("completedBytes", 0), "progress.completedBytes"),
        "totalBytes": _integer(data.get("totalBytes", 0), "progress.totalBytes"),
        "completedFiles": _integer(data.get("completedFiles", 0), "progress.completedFiles"),
        "totalFiles": _integer(data.get("totalFiles", 0), "progress.totalFiles"),
        "message": message,
        "updatedAt": _string(data.get("updatedAt"), "progress.updatedAt"),
    }


def _validate_usage(value):
    data = _mapping(value or {}, "usage")
    result = {}
    for key in ("sourceBytes", "transferBytes", "cacheReusedBytes", "adaptedBytes", "temporaryBytes"):
        result[key] = _integer(data.get(key), f"usage.{key}", nullable=True)
    return result


def _validate_error(value):
    data = _mapping(value, "error")
    code = _string(data.get("code"), "error.code")
    if code not in ERROR_CODES:
        raise ValueError("error.code is unknown")
    message = _string(data.get("message"), "error.message")
    retryable = data.get("retryable")
    if not isinstance(retryable, bool):
        raise ValueError("error.retryable must be a boolean")
    return {"code": code, "message": message, "retryable": retryable}


def _validate_result(value):
    data = _mapping(value, "result")
    asset_version = _string(data.get("assetVersion"), "result.assetVersion")
    content_hash = _string(data.get("contentHash"), "result.contentHash", pattern=HASH)
    deduplicated = data.get("deduplicated")
    if not isinstance(deduplicated, bool):
        raise ValueError("result.deduplicated must be a boolean")
    validation = _mapping(data.get("validation"), "result.validation")
    status = _string(validation.get("status"), "result.validation.status")
    if status not in ("launchable", "gaps"):
        raise ValueError("result.validation.status must be launchable or gaps")
    report_id = validation.get("reportId")
    if report_id is not None and (
        not isinstance(report_id, str) or not re.fullmatch(r"[a-f0-9]{32}", report_id)
    ):
        raise ValueError("result.validation.reportId is invalid")
    usage = _mapping(data.get("usage"), "result.usage")
    for name in ("dressing", "staticCollision"):
        if name in usage and usage[name] is not None and not isinstance(usage[name], bool):
            raise ValueError(f"result.usage.{name} must be a boolean or null")
    evidence = usage.get("evidence")
    if evidence is not None and not isinstance(evidence, str):
        raise ValueError("result.usage.evidence must be a string or null")
    return {
        "assetVersion": asset_version,
        "contentHash": content_hash,
        "deduplicated": deduplicated,
        "validation": {
            "status": status,
            "reportId": report_id,
            "gapCount": _integer(validation.get("gapCount", 0), "result.validation.gapCount"),
        },
        "usage": {
            "dressing": usage.get("dressing"),
            "staticCollision": usage.get("staticCollision"),
            "evidence": evidence,
        },
    }


@dataclass
class AcquisitionRecord:
    id: str
    catalog_ref: str
    status: str
    attempt: int
    source: dict
    estimate: dict
    budget: dict
    progress: dict
    usage: dict
    result: dict | None
    error: dict | None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    cancel_requested: bool = False

    def document(self):
        return {
            "schema": SCHEMA,
            "id": self.id,
            "catalogRef": self.catalog_ref,
            "status": self.status,
            "attempt": self.attempt,
            "source": dict(self.source),
            "estimate": self.estimate,
            "budget": self.budget,
            "progress": self.progress,
            "usage": self.usage,
            "result": self.result,
            "error": self.error,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "cancelRequested": self.cancel_requested,
        }

    @classmethod
    def from_document(cls, value):
        data = _mapping(value, "acquisition")
        if data.get("schema") != SCHEMA:
            raise ValueError(f"acquisition.schema must be {SCHEMA}")
        identifier = _string(data.get("id"), "id", pattern=ID)
        status = _string(data.get("status"), "status")
        if status not in STATUSES:
            raise ValueError("acquisition.status is unknown")
        attempt = _integer(data.get("attempt"), "attempt", minimum=1)
        source = _validate_source(data.get("source"))
        catalog_ref = _string(data.get("catalogRef"), "catalogRef")
        expected_ref = f"{source['provider']}/{source['packageId']}@{source['revision']}"
        if catalog_ref not in (expected_ref, f"catalog:{expected_ref}"):
            raise ValueError("catalogRef disagrees with the fixed source")
        estimate = _mapping(data.get("estimate"), "estimate")
        budget = _mapping(data.get("budget"), "budget")
        progress = _validate_progress(data.get("progress"))
        usage = _validate_usage(data.get("usage"))
        result = data.get("result")
        if result is not None:
            result = _validate_result(result)
        error = data.get("error")
        if error is not None:
            error = _validate_error(error)
        if (error is None) != (status != "failed"):
            raise ValueError("error is present exactly for failed acquisitions")
        started_at = data.get("startedAt")
        finished_at = data.get("finishedAt")
        if started_at is not None and not isinstance(started_at, str):
            raise ValueError("startedAt must be a string or null")
        if finished_at is not None and not isinstance(finished_at, str):
            raise ValueError("finishedAt must be a string or null")
        cancel_requested = data.get("cancelRequested", False)
        if not isinstance(cancel_requested, bool):
            raise ValueError("cancelRequested must be a boolean")
        return cls(
            id=identifier,
            catalog_ref=catalog_ref,
            status=status,
            attempt=attempt,
            source=source,
            estimate=estimate,
            budget=budget,
            progress=progress,
            usage=usage,
            result=result,
            error=error,
            created_at=_string(data.get("createdAt"), "createdAt"),
            updated_at=_string(data.get("updatedAt"), "updatedAt"),
            started_at=started_at,
            finished_at=finished_at,
            cancel_requested=cancel_requested,
        )

    @classmethod
    def create(cls, catalog_ref, source, estimate, *, timestamp=None):
        timestamp = timestamp or datetime.now(UTC).isoformat()
        return cls(
            id="acq-" + uuid.uuid4().hex,
            catalog_ref=catalog_ref,
            status="queued",
            attempt=1,
            source=source,
            estimate=estimate,
            budget={},
            progress={
                "phase": "queued",
                "completedBytes": 0,
                "totalBytes": estimate.get("source", {}).get("bytes", 0),
                "completedFiles": 0,
                "totalFiles": estimate.get("source", {}).get("files", 0),
                "message": None,
                "updatedAt": timestamp,
            },
            usage={
                "sourceBytes": None,
                "transferBytes": None,
                "cacheReusedBytes": None,
                "adaptedBytes": None,
                "temporaryBytes": None,
            },
            result=None,
            error=None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def touch(self, **changes):
        for key, value in changes.items():
            setattr(self, key, value)
        self.updated_at = now()
        return self


def source_document(entry):
    return {
        "provider": entry.provider,
        "packageId": entry.package_id,
        "revision": entry.revision,
        "recipe": RECIPE,
    }


def estimate_limits(limits):
    return limits.document()

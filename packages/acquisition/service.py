"""Persistent acquisition lifecycle, queue, cancellation and publication.

The service owns records under ``<state>/acquisitions/records`` and never
publishes a partial product: a version exists only after the shared immutable
import transaction commits. A cancel after that commit keeps the version.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from packages.acquisition import cache
from packages.acquisition.budget import BudgetExceeded, BudgetLedger
from packages.acquisition.models import (
    ACTIVE_STATUSES,
    ERROR_CODES,
    ID,
    PHASES,
    REF,
    STATUSES,
    AcquisitionRecord,
    Limits,
    source_document,
)
from packages.acquisition.runner import (
    AcquisitionCancelled,
    AcquisitionFailure,
    SubprocessRunner,
)
from packages.catalog.local import LocalAsset, local_usage, read_provenance
from packages.library.common import LibraryError
from packages.library.imports import INTERNAL_MAX_FILES, StagedLimits, confined_stage
from packages.library.reports import launchable
from packages.library.transactions import durable_json

LOGGER = logging.getLogger(__name__)

PROGRESS_KEYS = ("completedBytes", "totalBytes", "completedFiles", "totalFiles")


class AcquisitionService:
    def __init__(
        self,
        state_root,
        library_root,
        library,
        catalog,
        *,
        runner=None,
        limits=None,
        disk_usage=None,
        clock=time.monotonic,
        progress_interval=0.5,
        source_root=None,
    ):
        self.state_root = Path(state_root)
        self.library = library
        self.catalog = catalog
        self.root = self.state_root / "acquisitions"
        self.records_root = self.root / "records"
        self.cache_root = self.root / "cache"
        self.work_root = self.root / "work"
        self.library_root = Path(library_root)
        self.staging_root = self.library_root / ".acquisition-staging"
        self._limits = limits or Limits()
        self._limits_injected = limits is not None
        self._runner = runner or SubprocessRunner(
            self.state_root, self.library_root, catalog=catalog, source_root=source_root
        )
        self._disk_usage = disk_usage or self._free_disk
        self._clock = clock
        self._progress_interval = progress_interval
        self._records: dict[str, AcquisitionRecord] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancels: dict[str, asyncio.Event] = {}
        self._semaphore = asyncio.Semaphore(self._limits.concurrent_acquisitions)
        self._budget = self._ledger()
        self._closed = False

    def _free_disk(self):
        roots = [self.state_root, self.library_root]
        return min(shutil.disk_usage(root).free for root in roots if root.exists())

    @property
    def limits(self):
        return self._limits

    def _ledger(self):
        return BudgetLedger(
            self.root / "budget.json",
            self._limits,
            cache_root=self.cache_root,
            disk_usage=self._disk_usage,
        )

    async def startup(self):
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.library_root.mkdir(parents=True, exist_ok=True)
        for directory in (self.records_root, self.cache_root, self.work_root):
            directory.mkdir(parents=True, exist_ok=True)
        if (
            not self._limits_injected
            and self.catalog is not None
            and getattr(self.catalog, "available", False)
            and getattr(self.catalog, "snapshot", None) is not None
        ):
            self._limits = Limits.from_mapping(self.catalog.snapshot.limits)
            self._semaphore = asyncio.Semaphore(self._limits.concurrent_acquisitions)
            self._budget = self._ledger()
        self._load()
        for record in list(self._records.values()):
            if record.status not in ACTIVE_STATUSES:
                continue
            released = self._budget.release(record.id)
            record.touch(
                status="failed",
                error={
                    "code": "interrupted",
                    "message": "The gateway restarted before this acquisition finished; "
                    "retry is explicit.",
                    "retryable": True,
                },
                finished_at=self._now(),
                cancel_requested=False,
                budget=(record.budget or {}) | {"releasedAt": released},
            )
            record.progress = record.progress | {
                "message": "Interrupted by a gateway restart",
                "updatedAt": self._now(),
            }
            self._persist(record)
        self._clear_directory(self.staging_root)
        self._clear_directory(self.work_root)

    async def close(self):
        self._closed = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _now(self):
        return datetime.now(UTC).isoformat()

    def _load(self):
        for path in sorted(self.records_root.glob("*.json")):
            try:
                record = AcquisitionRecord.from_document(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, ValueError) as exc:
                LOGGER.warning("Ignoring unreadable acquisition record %s: %s", path, exc)
                continue
            if path.stem != record.id:
                LOGGER.warning("Ignoring acquisition record with a mismatched filename: %s", path)
                continue
            self._records[record.id] = record

    def _persist(self, record):
        durable_json(self.records_root / f"{record.id}.json", record.document())

    def _update(self, record, **changes):
        record.touch(**changes)
        self._persist(record)
        return record

    @staticmethod
    def _clear_directory(root):
        root = Path(root)
        if not root.is_dir() or root.is_symlink() or root.is_junction():
            return
        for child in root.iterdir():
            try:
                if child.is_dir() and not child.is_symlink() and not child.is_junction():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                LOGGER.warning("Could not clear acquisition scratch path %s", child)

    def _cleanup_stage(self, identifier):
        path = self.staging_root / identifier
        try:
            if path.is_dir() and not path.is_symlink() and not path.is_junction():
                shutil.rmtree(path)
        except OSError:
            LOGGER.warning("Could not remove acquisition staging directory %s", path)

    def _active_keys(self):
        return {
            (record.source["provider"], record.source["revision"], record.source["packageId"])
            for record in self._records.values()
            if record.status in ACTIVE_STATUSES
        }

    # Public API ---------------------------------------------------------

    def _entry(self, ref):
        if not isinstance(ref, str) or not REF.fullmatch(ref.strip()):
            raise LibraryError(
                "invalid_acquisition",
                "Provide a supported catalog entry ID; filesystem paths and URLs are not accepted",
                422,
            )
        value = ref.strip()
        if value.startswith("catalog:"):
            value = value[len("catalog:"):]
        provider, _, rest = value.partition("/")
        package, _, revision = rest.partition("@")
        self.catalog.require()
        return self.catalog.entry(provider, package, revision)

    async def start(self, ref):
        entry = self._entry(ref)
        if not entry.acquisition.eligible:
            raise LibraryError(
                "acquisition_ineligible",
                "This catalog entry is not eligible for acquisition",
                409,
                reasons=list(entry.acquisition.reasons),
                estimate=self.catalog.estimate(entry.provider, entry.package_id, entry.revision),
                limits=self._limits.full_document(),
            )
        estimate, reasons = self._budget.preflight(entry, active=self._active_keys())
        if reasons:
            raise LibraryError(
                "budget_exceeded",
                "The acquisition would exceed a platform budget",
                409,
                reasons=list(reasons),
                estimate=estimate,
                limits=self._limits.full_document(),
            )
        record = AcquisitionRecord.create(
            entry.ref, source_document(entry), estimate, timestamp=self._now()
        )
        cache.clean(
            self.cache_root,
            active=self._active_keys(),
            limit=self._limits.raw_cache_bytes,
            required=estimate["source"]["bytes"],
        )
        try:
            reservation = self._budget.reserve(
                record_id=record.id,
                attempt=record.attempt,
                source_bytes=estimate["source"]["bytes"],
                adapted_upper=estimate["adaptedPackage"]["upperBoundBytes"],
                active=self._active_keys(),
            )
        except BudgetExceeded as exc:
            raise LibraryError(
                "budget_exceeded",
                "The acquisition would exceed a platform budget",
                409,
                reasons=list(exc.reasons),
                estimate=estimate,
                limits=self._limits.full_document(),
            ) from exc
        record.budget = reservation
        self._records[record.id] = record
        self._persist(record)
        self._spawn(record.id)
        return record.document()

    def list(self, status=None, limit=50):
        if status is not None and status not in STATUSES:
            raise LibraryError("invalid_acquisition", "Unknown acquisition status", 422)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise LibraryError("invalid_acquisition", "limit must be between 1 and 50", 422)
        records = sorted(
            self._records.values(), key=lambda item: (item.created_at, item.id), reverse=True
        )
        if status is not None:
            records = [record for record in records if record.status == status]
        return {"acquisitions": [record.document() for record in records[:limit]]}

    def _record(self, identifier):
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise LibraryError(
                "invalid_acquisition", "Acquisition IDs look like acq-<32 hex>", 422
            )
        record = self._records.get(identifier)
        if record is None:
            raise LibraryError("unknown_acquisition", "Unknown acquisition", 404)
        return record

    def show(self, identifier):
        return self._record(identifier).document()

    async def cancel(self, identifier):
        record = self._record(identifier)
        if record.status not in ACTIVE_STATUSES:
            return record.document()
        record.cancel_requested = True
        if record.status == "queued":
            task = self._tasks.get(record.id)
            if task is not None and not task.done():
                task.cancel()
            self._finish_cancelled(record)
            return record.document()
        event = self._cancels.get(record.id)
        if event is not None:
            event.set()
        self._update(record)
        return record.document()

    async def retry(self, identifier):
        record = self._record(identifier)
        if record.status not in ("failed", "cancelled"):
            raise LibraryError(
                "invalid_acquisition",
                "Only failed or cancelled acquisitions can be retried",
                422,
            )
        if record.error and record.error.get("retryable") is False:
            raise LibraryError(
                "acquisition_not_retryable",
                "This failure is not retryable; change the source or request and acquire again",
                409,
                reasons=[record.error.get("message")] if record.error.get("message") else [],
            )
        reasons = self._budget.retry_reasons(record, active=self._active_keys())
        if reasons:
            raise LibraryError(
                "budget_exceeded",
                "The retry would exceed a platform budget",
                409,
                reasons=list(reasons),
                estimate=record.estimate,
                limits=self._limits.full_document(),
            )
        source = record.estimate.get("source", {}).get("bytes", 0)
        adapted = record.estimate.get("adaptedPackage", {}).get("upperBoundBytes") or (
            self._limits.adapted_package_bytes
        )
        cache.clean(
            self.cache_root,
            active=self._active_keys(),
            limit=self._limits.raw_cache_bytes,
            required=source,
        )
        try:
            reservation = self._budget.reserve(
                record_id=record.id,
                attempt=record.attempt + 1,
                source_bytes=source,
                adapted_upper=adapted,
                active=self._active_keys(),
            )
        except BudgetExceeded as exc:
            raise LibraryError(
                "budget_exceeded",
                "The retry would exceed a platform budget",
                409,
                reasons=list(exc.reasons),
                estimate=record.estimate,
                limits=self._limits.full_document(),
            ) from exc
        record.touch(
            attempt=record.attempt + 1,
            status="queued",
            error=None,
            result=None,
            usage={},
            started_at=None,
            finished_at=None,
            cancel_requested=False,
            budget=reservation,
            progress={
                "phase": "queued",
                "completedBytes": 0,
                "totalBytes": record.estimate.get("source", {}).get("bytes", 0),
                "completedFiles": 0,
                "totalFiles": record.estimate.get("source", {}).get("files", 0),
                "message": None,
                "updatedAt": self._now(),
            },
        )
        self._persist(record)
        self._spawn(record.id)
        return record.document()

    # Queue and lifecycle ------------------------------------------------

    def _spawn(self, identifier):
        task = asyncio.create_task(self._run(identifier), name=f"acquisition-{identifier}")
        self._tasks[identifier] = task

        def finished(_):
            if self._tasks.get(identifier) is task:
                self._tasks.pop(identifier, None)

        task.add_done_callback(finished)

    async def _run(self, identifier):
        try:
            await self._semaphore.acquire()
        except asyncio.CancelledError:
            return
        try:
            record = self._records.get(identifier)
            if record is None or record.status != "queued":
                return
            await self._execute(record)
        finally:
            self._semaphore.release()

    async def _execute(self, record):
        cancel = asyncio.Event()
        self._cancels[record.id] = cancel
        try:
            self._update(
                record,
                status="downloading",
                started_at=self._now(),
                progress=record.progress
                | {"phase": "downloading", "message": None, "updatedAt": self._now()},
            )
            last = [0.0]

            async def progress(update):
                clock = self._clock()
                phase = update.get("phase") if update.get("phase") in PHASES else None
                changed = phase is not None and phase != record.progress.get("phase")
                if not changed and clock - last[0] < self._progress_interval:
                    return
                last[0] = clock
                merged = dict(record.progress)
                for key in PROGRESS_KEYS:
                    value = update.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        merged[key] = value
                message = update.get("message")
                if message is not None:
                    merged["message"] = str(message)[:2000]
                if phase is not None:
                    merged["phase"] = phase
                merged["updatedAt"] = self._now()
                changes = {"progress": merged}
                if phase in ACTIVE_STATUSES:
                    changes["status"] = phase
                self._update(record, **changes)

            staged = await self._runner.run(record, progress, cancel)
            if record.cancel_requested or cancel.is_set():
                self._finish_cancelled(record)
                return
            adapted_bytes = self._require_staged(staged.path)
            self._update(
                record,
                status="validating",
                progress=record.progress
                | {"phase": "validating", "message": None, "updatedAt": self._now()},
            )
            if record.cancel_requested or cancel.is_set():
                self._finish_cancelled(record)
                return
            self._update(
                record,
                status="importing",
                progress=record.progress
                | {
                    "phase": "importing",
                    "message": "Publishing the immutable version",
                    "updatedAt": self._now(),
                },
            )
            limits = StagedLimits(
                max_bytes=self._limits.adapted_package_bytes, max_files=INTERNAL_MAX_FILES
            )
            job = await asyncio.to_thread(
                self.library.import_staged,
                staged.path,
                allowed_root=self.staging_root,
                limits=limits,
            )
            self._succeed(record, staged, job, adapted_bytes)
        except AcquisitionCancelled:
            self._finish_cancelled(record)
        except AcquisitionFailure as exc:
            if record.cancel_requested or cancel.is_set():
                self._finish_cancelled(record)
            else:
                self._fail(record, exc.code, exc.message, exc.retryable)
        except asyncio.CancelledError:
            raise
        except LibraryError as exc:
            code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
            if code not in ERROR_CODES:
                code = "import_invalid" if record.status == "importing" else "internal_error"
            self._fail(record, code, exc.detail.get("message", "The import failed"), True)
        except Exception:
            LOGGER.exception("Acquisition %s failed unexpectedly", record.id)
            self._fail(record, "internal_error", "The acquisition failed unexpectedly", True)
        finally:
            self._cancels.pop(record.id, None)
            self._cleanup_stage(record.id)

    def _require_staged(self, path):
        limits = StagedLimits(
            max_bytes=self._limits.adapted_package_bytes, max_files=INTERNAL_MAX_FILES
        )
        try:
            stage = confined_stage(path, self.staging_root, limits)
        except LibraryError as exc:
            problem = (exc.detail.get("problems") or [{}])[0].get("message", "invalid stage")
            raise AcquisitionFailure(
                "import_invalid", f"The worker staged an unusable package: {problem}", retryable=True
            ) from None
        return sum(
            item.stat().st_size
            for item in stage.rglob("*")
            if item.is_file() and not item.is_symlink()
        )

    def _catalog_entry(self, record):
        if self.catalog is None or not getattr(self.catalog, "available", False):
            return None
        return getattr(self.catalog, "by_package", {}).get(record.source["packageId"])

    def _succeed(self, record, staged, job, adapted_bytes):
        asset_version = job.get("assetVersion")
        if not isinstance(asset_version, str):
            raise AcquisitionFailure(
                "import_invalid", "The internal import did not publish an asset", retryable=False
            )
        result = {
            "assetVersion": asset_version,
            "contentHash": job.get("contentHash"),
            "deduplicated": bool(job.get("deduplicated")),
            "validation": {"status": "unknown", "reportId": None, "gapCount": 0},
            "usage": {"dressing": None, "staticCollision": None, "evidence": None},
        }
        try:
            version = self.library.resolve(asset_version)
            report = version.report() or {}
            result["validation"] = {
                "status": "launchable" if launchable(report) else "gaps",
                "reportId": report.get("reportId"),
                "gapCount": version.summary().get("gapCount", 0),
            }
            result["usage"] = local_usage(
                LocalAsset(version, read_provenance(version), self._catalog_entry(record))
            )
        except Exception:
            # The import already published an immutable version; post-publish
            # inspection failures must not rewrite that fact as a failed acquisition.
            LOGGER.exception("Acquisition %s published but its local details are unavailable", record.id)
        actual = {
            "sourceBytes": record.estimate.get("source", {}).get("bytes"),
            "transferBytes": record.estimate.get("transfer", {}).get("upperBoundBytes"),
            "cacheReusedBytes": record.estimate.get("cache", {}).get("reusedBytes"),
            "adaptedBytes": adapted_bytes,
            "temporaryBytes": None,
        }
        for key in actual:
            value = staged.usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                actual[key] = value
        try:
            cache.mark_complete(
                self.cache_root,
                record.source["provider"],
                record.source["revision"],
                record.source["packageId"],
            )
        except Exception:
            LOGGER.exception("Acquisition %s could not mark its raw cache complete", record.id)
        try:
            released = self._budget.release(record.id, release_charge=True)
        except Exception:
            LOGGER.exception("Acquisition %s published but its budget ledger could not settle", record.id)
            released = None
        message = f"Published {asset_version}"
        if record.cancel_requested:
            message = (
                f"{message}; cancellation arrived after the atomic import and the "
                "published version is kept"
            )
        self._update(
            record,
            status="succeeded",
            result=result,
            error=None,
            finished_at=self._now(),
            usage=actual,
            budget=(record.budget or {}) | {"releasedAt": released},
        )
        record.progress = record.progress | {"message": message, "updatedAt": self._now()}
        self._persist(record)

    def _fail(self, record, code, message, retryable):
        released = self._budget.release(record.id, release_charge=not retryable)
        record.touch(
            status="failed",
            error={"code": code, "message": message, "retryable": bool(retryable)},
            finished_at=self._now(),
            budget=(record.budget or {}) | {"releasedAt": released},
        )
        record.progress = record.progress | {"message": message, "updatedAt": self._now()}
        self._persist(record)

    def _finish_cancelled(self, record):
        released = self._budget.release(record.id, release_charge=True)
        record.touch(
            status="cancelled",
            error=None,
            finished_at=self._now(),
            cancel_requested=True,
            budget=(record.budget or {}) | {"releasedAt": released},
        )
        record.progress = record.progress | {"message": "Cancelled", "updatedAt": self._now()}
        self._persist(record)

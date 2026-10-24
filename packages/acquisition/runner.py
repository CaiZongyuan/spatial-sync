"""The worker boundary: a runner protocol plus the default subprocess runner.

The gateway itself never imports ``factory``. The default runner executes the
acquisition worker as a subprocess and only exchanges JSON documents with it.
Tests inject a deterministic fake runner at this seam.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Protocol

from packages.acquisition.models import ERROR_CODES
from packages.library.common import REPO_ROOT
from packages.library.transactions import durable_json

ProgressCallback = Callable[[dict], Awaitable[None]]


class AcquisitionCancelled(Exception):
    """The attempt was cancelled before any version was published."""


class AcquisitionFailure(Exception):
    def __init__(self, code, message, retryable=True):
        if code not in ERROR_CODES:
            code = "internal_error"
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class StagedResult:
    """The worker produced a server-staged package and its reported evidence."""

    path: Path
    source_report: dict | None = None
    adaptation_report: dict | None = None
    usage: dict = field(default_factory=dict)


class AcquisitionRunner(Protocol):
    async def run(self, record, progress: ProgressCallback, cancel: asyncio.Event) -> StagedResult:
        ...


class SubprocessRunner:
    def __init__(
        self,
        state_root,
        library_root,
        *,
        catalog=None,
        python=None,
        timeout=3600,
        poll_interval=0.25,
        source_root=None,
    ):
        root = Path(state_root).resolve() / "acquisitions"
        self.work_root = root / "work"
        self.cache_root = root / "cache"
        self.staging_root = Path(library_root).resolve() / ".acquisition-staging"
        self.catalog = catalog
        self.python = python or sys.executable
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.source_root = Path(source_root).resolve() if source_root else None

    def _entry(self, record):
        if self.catalog is None or not getattr(self.catalog, "available", False):
            return None
        return getattr(self.catalog, "by_package", {}).get(record.source["packageId"])

    def job_document(self, record):
        provider = record.source["provider"]
        revision = record.source["revision"]
        stage = self.staging_root / record.id
        entry = self._entry(record)
        expected = {
            "files": record.estimate.get("source", {}).get("files"),
            "bytes": record.estimate.get("source", {}).get("bytes"),
            "sourceFiles": record.estimate.get("source", {}).get("files"),
            "sourceBytes": record.estimate.get("source", {}).get("bytes"),
            "bomNotInBom": record.estimate.get("source", {}).get("bomNotInBom", []),
        }
        if entry is not None:
            expected |= {
                "files": entry.source.files,
                "bytes": entry.source.bytes,
                "license": entry.license.document(),
                "licenseSha256": entry.license.evidence.sha256,
                "localAssetId": entry.local_asset_id,
                "title": entry.names["en"],
                "description": entry.descriptions["en"],
                "role": "dressing and static collision prop",
            }
        return {
            "schema": "lw.acquisition.worker-job/1",
            "acquisitionId": record.id,
            "provider": provider,
            "packageId": record.source["packageId"],
            "revision": revision,
            "recipe": record.source["recipe"],
            # The worker owns <cacheRoot>/<package>; the provider/revision prefix
            # is the LRU unit this gateway accounts and cleans.
            "cacheRoot": str(self.cache_root / provider / revision),
            "stagingDir": str(stage),
            "progressFile": str(self.work_root / f"{record.id}.progress.json"),
            "resultFile": str(self.work_root / f"{record.id}.result.json"),
            "limits": record.estimate.get("limits", {}),
            "expected": expected,
        } | ({"sourceRoot": str(self.source_root)} if self.source_root else {})

    @staticmethod
    async def _terminate(process):
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except (TimeoutError, asyncio.TimeoutError):
            with contextlib.suppress(ProcessLookupError, OSError):
                process.kill()
            await process.wait()

    async def run(self, record, progress: ProgressCallback, cancel: asyncio.Event) -> StagedResult:
        self.work_root.mkdir(parents=True, exist_ok=True)
        job_path = self.work_root / f"{record.id}.json"
        progress_path = self.work_root / f"{record.id}.progress.json"
        result_path = self.work_root / f"{record.id}.result.json"
        for path in (progress_path, result_path):
            path.unlink(missing_ok=True)
        durable_json(job_path, self.job_document(record))
        process = None
        try:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.python,
                    "-m",
                    "factory.acquisition_worker",
                    "--job",
                    str(job_path),
                    cwd=str(REPO_ROOT),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as exc:
                raise AcquisitionFailure(
                    "internal_error", "The acquisition worker could not start", retryable=True
                ) from exc
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.timeout
            seen = None
            while True:
                if cancel.is_set():
                    await self._terminate(process)
                    raise AcquisitionCancelled()
                if progress_path.is_file():
                    stamp = progress_path.stat().st_mtime_ns
                    if stamp != seen:
                        seen = stamp
                        try:
                            update = json.loads(progress_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            update = None
                        if isinstance(update, dict):
                            await progress(update)
                if process.returncode is not None:
                    break
                if loop.time() > deadline:
                    await self._terminate(process)
                    raise AcquisitionFailure(
                        "source_unavailable", "The acquisition worker timed out", retryable=True
                    )
                await asyncio.sleep(self.poll_interval)
            if cancel.is_set():
                raise AcquisitionCancelled()
            result = {}
            if result_path.is_file():
                try:
                    value = json.loads(result_path.read_text(encoding="utf-8"))
                    result = value if isinstance(value, dict) else {}
                except (OSError, ValueError):
                    result = {}
            error = result.get("error")
            if process.returncode != 0 or error is not None:
                error = error if isinstance(error, dict) else {}
                code = error.get("code") if error.get("code") in ERROR_CODES else "internal_error"
                message = error.get("message") or "The acquisition worker failed"
                retryable = error.get("retryable", True)
                raise AcquisitionFailure(code, str(message), retryable=bool(retryable))
            staged = result.get("stagedPackage") or str(self.staging_root / record.id / "package")
            usage = result.get("usage")
            return StagedResult(
                Path(staged),
                result.get("source") if isinstance(result.get("source"), dict) else None,
                result.get("adaptation") if isinstance(result.get("adaptation"), dict) else None,
                usage if isinstance(usage, dict) else {},
            )
        except asyncio.CancelledError:
            if process is not None:
                await self._terminate(process)
            raise
        finally:
            for path in (job_path, progress_path, result_path):
                with contextlib.suppress(OSError):
                    path.unlink()

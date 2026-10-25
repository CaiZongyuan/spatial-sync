"""One owned, resumable catalog synchronizer and atomic offline publication."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path

from packages.catalog.catalog import AssetCatalog
from packages.catalog.models import SYNCED_CATALOG_SCHEMA
from packages.catalog.source import DATASET, SimReadySource, SourceError, SyncCancelled, image_extension
from packages.library.common import LibraryError
from packages.library.transactions import durable_json

MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024
WORKERS = 8
FINAL = {"succeeded", "failed", "cancelled", "interrupted"}
IDENTIFIER = re.compile(r"[0-9a-f]{32}")
IMAGE = re.compile(r"[0-9a-f]{64}\.(?:png|jpg|webp)")


def now():
    return datetime.now(UTC).isoformat()


def publish_file(operation, *args):
    """Windows indexers can briefly hold a destination during atomic replace."""
    for attempt in range(5):
        try:
            return operation(*args)
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.05 * (attempt + 1))


class CatalogSync:
    def __init__(self, state_root, catalog, *, source_factory=None):
        self.root = Path(state_root) / "catalog"
        self.cache = self.root / "cache"
        self.snapshots = self.root / "snapshots"
        self.catalog = catalog
        self.source_factory = source_factory or SimReadySource
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._task = None
        self._current = None
        self._cache_bytes = 0
        self._published = []
        self._persist_at = 0

    def startup(self):
        self.root.mkdir(parents=True, exist_ok=True)
        pointer = self.root / "current.json"
        if pointer.exists():
            value = json.loads(pointer.read_text(encoding="utf-8"))
            name = value.get("file", "")
            history = value.get("history", [])
            if not isinstance(history, list) or any(
                not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{40}-[0-9a-f]{32}\.json", item)
                for item in [name, *history]
            ):
                raise ValueError("Invalid catalog publication pointer")
            candidate = AssetCatalog(self.snapshots / name, embedder=self.catalog.embedder)
            candidate.startup()
            if not candidate.available:
                raise ValueError("Published catalog is unreadable: " + str(candidate.error))
            self._published = list(dict.fromkeys([*history, name]))
            self.catalog.activate(
                candidate, archive_paths=[self.snapshots / item for item in self._published]
            )
        latest = self.root / "latest.json"
        if latest.exists():
            self._current = json.loads(latest.read_text(encoding="utf-8"))
            if self._current["status"] not in FINAL:
                self._current.update(status="interrupted", stage="done", finishedAt=now(), message=None)
                self._current["errors"].append({"package": None, "message": "Gateway restarted during sync"})
                self._persist()
        self._cache_bytes = sum(p.stat().st_size for p in self.cache.rglob("*") if p.is_file())

    def _persist(self, *, force=True):
        if not force and time.monotonic() - self._persist_at < 1:
            return
        publish_file(durable_json, self.root / "latest.json", self._current)
        if force:
            publish_file(durable_json, self.root / "jobs" / (self._current["id"] + ".json"), self._current)
        self._persist_at = time.monotonic()

    def status(self):
        with self._lock:
            return {
                "current": copy.deepcopy(self._current),
                "snapshot": self.catalog.require().document() if self.catalog.available else None,
                "limits": {"workers": WORKERS, "cacheBytes": MAX_CACHE_BYTES,
                           "transferBytes": MAX_CACHE_BYTES, "responseBytes": 2 * 1024 * 1024},
            }

    def show(self, identifier):
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise LibraryError("unknown_catalog_sync", "Unknown catalog sync", 404)
        with self._lock:
            if self._current and identifier == self._current["id"]:
                return copy.deepcopy(self._current)
            path = self.root / "jobs" / (identifier + ".json")
            if not path.is_file():
                raise LibraryError("unknown_catalog_sync", "Unknown catalog sync", 404)
            return json.loads(path.read_text(encoding="utf-8"))

    async def start(self, retry_of=None):
        with self._lock:
            if self._task is not None and not self._task.done():
                raise LibraryError("catalog_sync_running", "A catalog sync is already running", 409)
            if retry_of is not None and self.show(retry_of)["status"] not in FINAL:
                raise LibraryError("catalog_sync_running", "The previous sync has not finished", 409)
            self._cancel = threading.Event()
            self._current = {
                "schema": "lw.catalog-sync/1", "id": uuid.uuid4().hex,
                "status": "running", "stage": "enumerating", "retryOf": retry_of,
                "startedAt": now(), "finishedAt": None, "provider": "simready",
                "dataset": DATASET, "revision": None, "discovered": 0, "indexed": 0,
                "metadataReused": 0, "metadataFailed": 0, "previewsCached": 0,
                "previewsReused": 0, "previewsMissing": 0, "previewsFailed": 0,
                "networkBytes": 0, "cacheBytes": self._cache_bytes,
                "errors": [], "snapshotId": None,
                "message": None,
            }
            self._persist()
            document = copy.deepcopy(self._current)
            self._task = asyncio.create_task(asyncio.to_thread(self._run))
            return document

    async def cancel(self, identifier):
        with self._lock:
            document = self.show(identifier)
            if document["status"] not in FINAL:
                self._cancel.set()
                self._current["status"] = "cancelling"
                self._persist()
                document = copy.deepcopy(self._current)
            return document

    async def close(self):
        self._cancel.set()
        if self._task is not None:
            await self._task

    def preview_path(self, filename):
        if not IMAGE.fullmatch(filename):
            raise LibraryError("unknown_catalog_preview", "Unknown cached catalog preview", 404)
        path = self.cache / "previews" / filename
        if not path.is_file() or path.is_symlink():
            raise LibraryError("unknown_catalog_preview", "Cached preview is unavailable", 404)
        return path

    def _progress(self, size):
        with self._lock:
            self._current["networkBytes"] += size

    def _notice(self, message):
        with self._lock:
            if self._current["message"] != message:
                self._current["message"] = message
                self._persist(force=False)

    def _store(self, path, data):
        with self._lock:
            previous = path.stat().st_size if path.is_file() else 0
            if self._cache_bytes - previous + len(data) > MAX_CACHE_BYTES:
                raise SourceError("The independent catalog cache exceeds 2 GiB")
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".part-" + uuid.uuid4().hex)
            try:
                with temporary.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                publish_file(os.replace, temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            self._cache_bytes += len(data) - previous
            self._current["cacheBytes"] = self._cache_bytes

    def _package(self, source, revision, package, oid, curated):
        source.check()
        key = hashlib.sha256(("2/" + package + "/" + oid).encode()).hexdigest()
        cache_file = self.cache / "metadata" / (key + ".json")
        cached = None
        if cache_file.is_file():
            try:
                value = json.loads(cache_file.read_text(encoding="utf-8"))
                if value.get("treeOid") == oid and value.get("packageId") == package:
                    cached = value
            except (OSError, ValueError):
                pass
        error = None
        try:
            metadata = copy.deepcopy(cached) if cached else (
                source.metadata(revision, package) | {"packageId": package, "treeOid": oid}
            )
            if metadata.get("errors"):
                error = "; ".join(metadata["errors"])
        except SourceError as exc:
            error = str(exc)
            metadata = {"source": {"files": None, "bytes": None, "rootUsd": None, "bomItems": None},
                        "declaredLicense": None, "thumbnailPath": None, "errors": [error]}
        preview = {"url": None, "reason": "The upstream package has no preview image."}
        preview_state = "missing" if metadata["source"]["files"] is not None else "failed"
        if preview_state == "failed":
            preview["reason"] = "Preview availability is unknown because package metadata could not be read."
        filename = metadata.get("previewFile")
        if isinstance(filename, str) and IMAGE.fullmatch(filename):
            path = self.cache / "previews" / filename
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == filename.partition(".")[0]:
                preview = {"url": "/api/v1/library/catalog/previews/" + filename,
                           "reason": "Cached upstream preview; not a platform physics validation."}
                preview_state = "reused"
        if preview_state != "reused" and metadata.get("thumbnailPath"):
            try:
                image = source.file(revision, metadata["thumbnailPath"])
                filename = hashlib.sha256(image).hexdigest() + "." + image_extension(image)
                self._store(self.cache / "previews" / filename, image)
                metadata["previewFile"] = filename
                preview = {"url": "/api/v1/library/catalog/previews/" + filename,
                           "reason": "Cached upstream preview; not a platform physics validation."}
                preview_state = "cached"
            except SourceError as exc:
                preview = {"url": None, "reason": str(exc)[:2000]}
                preview_state = "failed"
        if not error and metadata != cached:
            try:
                self._store(cache_file, json.dumps(metadata, ensure_ascii=False, allow_nan=False).encode())
            except SourceError as exc:
                error = str(exc)
        entry = {
            "packageId": package, "title": package.replace("_", " ").replace("-", " "),
            "source": metadata["source"] | {"url":
                f"https://huggingface.co/datasets/{DATASET}/tree/{revision}/{package}"},
            "declaredLicense": metadata.get("declaredLicense"), "preview": preview,
            "discovery": {"treeOid": oid, "nameBasis": "package-id", "metadataComplete": error is None,
                          "metadataError": error, "previewState": preview_state,
                          "licenseBasis": "upstream packaging declaration; not reviewed"},
        }
        if package in curated:
            entry["curated"] = curated[package]
        with self._lock:
            record = self._current
            record["indexed"] += 1
            record["metadataReused"] += int(cached is not None)
            record["metadataFailed"] += int(error is not None)
            record[{"reused": "previewsReused", "cached": "previewsCached",
                    "missing": "previewsMissing", "failed": "previewsFailed"}[preview_state]] += 1
            if error and len(record["errors"]) < 10:
                record["errors"].append({"package": package, "message": error[:1000]})
            elif preview_state == "failed" and len(record["errors"]) < 10:
                record["errors"].append({"package": package, "message": preview["reason"][:1000]})
            self._persist(force=False)
        return entry

    def _run(self):
        source = None
        try:
            source = self.source_factory(cancel=self._cancel, progress=self._progress, notice=self._notice)
            revision = source.revision()
            packages = source.packages(revision)
            with self._lock:
                self._current.update(revision=revision, discovered=len(packages), stage="indexing")
                self._persist()
            seed = json.loads(self.catalog.path.read_text(encoding="utf-8"))
            curated = {e["packageId"]: e for e in seed.get("entries", [])} if (
                seed.get("revision") == revision and seed.get("schema") == "lw.asset-catalog/1"
            ) else {}
            entries = []
            remaining = iter(sorted(packages.items()))
            with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="catalog-sync") as pool:
                pending = set()
                for package, oid in islice(remaining, WORKERS):
                    pending.add(pool.submit(self._package, source, revision, package, oid, curated))
                while pending:
                    source.check()
                    done, pending = wait(pending, timeout=.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        entries.append(future.result())
                        package, oid = next(remaining, (None, None))
                        if package:
                            pending.add(pool.submit(self._package, source, revision, package, oid, curated))
            source.check()
            with self._lock:
                self._current["stage"] = "publishing"
                self._persist()
                metrics = {key: self._current[key] for key in (
                    "discovered", "indexed", "metadataFailed", "previewsCached",
                    "previewsReused", "previewsMissing", "previewsFailed",
                )}
            document = {
                "schema": SYNCED_CATALOG_SCHEMA, "provider": "simready", "dataset": DATASET,
                "revision": revision, "indexedAt": now(),
                "coverage": "Complete package directory at the fixed source revision; "
                            "metadata and preview gaps are reported separately.",
                "entries": sorted(entries, key=lambda entry: entry["packageId"]), "sync": metrics,
                "limits": dict(self.catalog.require().limits),
            }
            filename = revision + "-" + self._current["id"] + ".json"
            path = self.snapshots / filename
            publish_file(durable_json, path, document)
            candidate = AssetCatalog(path, embedder=self.catalog.embedder)
            candidate.startup()
            if not candidate.available:
                raise SourceError("Cannot build the complete catalog: " + str(candidate.error))
            with self._lock:
                source.check()
                publish_file(durable_json, self.root / "current.json", {
                    "file": filename, "history": self._published,
                })
                self._published.append(filename)
                self.catalog.activate(
                    candidate, archive_paths=[self.snapshots / item for item in self._published]
                )
                self._current.update(status="succeeded", stage="done", finishedAt=now(),
                                     snapshotId=candidate.snapshot.id, message=None)
                self._persist()
        except SyncCancelled:
            with self._lock:
                self._current.update(status="cancelled", stage="done", finishedAt=now(), message=None)
                self._persist()
        except Exception as exc:
            with self._lock:
                self._current.update(status="failed", stage="done", finishedAt=now(), message=None)
                self._current["errors"].append({"package": None, "message": str(exc)[:1000]})
                self._persist()
        finally:
            if source is not None:
                source.close()


__all__ = ["CatalogSync"]

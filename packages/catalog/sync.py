"""One owned, resumable catalog synchronizer and atomic offline publication."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from packages.catalog.catalog import AssetCatalog
from packages.catalog.models import COLLECTION_SCHEMA, DEFAULT_LIMITS, SYNCED_CATALOG_SCHEMA, parse_catalog
from packages.catalog.source import MAX_TRANSFER_BYTES, PACKAGE, REVISION, SourceError, SyncCancelled
from packages.catalog.sources import CatalogSources
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
            time.sleep(0.05 * (attempt + 1))


class CatalogSync:
    def __init__(self, state_root, catalog, *, source_factory=None, sources=None):
        self.root = Path(state_root) / "catalog"
        self.cache = self.root / "cache"
        self.snapshots = self.root / "snapshots"
        self.catalog = catalog
        self.sources = sources or CatalogSources(simready_factory=source_factory)
        self._documents = {}
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
        path = self.snapshots / self._published[-1] if self._published else self.catalog.path
        if path and path.is_file() and self.catalog.available:
            document = json.loads(path.read_text(encoding="utf-8"))
            groups = document["sources"] if document.get("schema") == COLLECTION_SCHEMA else [document]
            self._documents = {group["provider"]: group for group in groups}
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
                "sources": [
                    {
                        "provider": source.provider,
                        "dataset": source.dataset,
                        "entryCount": len(self._documents.get(source.provider, {}).get("entries", [])),
                        "coverage": self._documents.get(source.provider, {}).get("coverage"),
                    }
                    for source in self.sources.registrations.values()
                ],
                "snapshot": self.catalog.require().document() if self.catalog.available else None,
                "limits": {
                    "workers": WORKERS,
                    "cacheBytes": MAX_CACHE_BYTES,
                    "transferBytes": MAX_TRANSFER_BYTES,
                    "responseBytes": 2 * 1024 * 1024,
                },
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

    async def start(self, retry_of=None, provider=None):
        with self._lock:
            if self._task is not None and not self._task.done():
                raise LibraryError("catalog_sync_running", "A catalog sync is already running", 409)
            if retry_of is not None and self.show(retry_of)["status"] not in FINAL:
                raise LibraryError("catalog_sync_running", "The previous sync has not finished", 409)
            previous = self.show(retry_of) if retry_of is not None else None
            if previous:
                selected = previous.get("providers")
                if not selected and previous.get("provider"):
                    selected = [previous["provider"]]
            else:
                selected = [provider] if provider is not None else None
            selected = selected or list(self.sources.registrations)
            for name in selected:
                self.sources.require(name)
            self._cancel = threading.Event()
            self._current = {
                "schema": "lw.catalog-sync/1",
                "id": uuid.uuid4().hex,
                "status": "running",
                "stage": "enumerating",
                "retryOf": retry_of,
                "startedAt": now(),
                "finishedAt": None,
                "provider": provider,
                "providers": selected,
                "sources": [],
                "dataset": None,
                "revision": None,
                "discovered": 0,
                "indexed": 0,
                "metadataReused": 0,
                "metadataFailed": 0,
                "previewsCached": 0,
                "previewsReused": 0,
                "previewsMissing": 0,
                "previewsFailed": 0,
                "networkBytes": 0,
                "cacheBytes": self._cache_bytes,
                "errors": [],
                "snapshotId": None,
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
            if self._current["networkBytes"] > MAX_TRANSFER_BYTES:
                raise SourceError("Catalog update exceeded the shared transfer budget")

    def _notice(self, message):
        with self._lock:
            if self._current["message"] != message:
                self._current["message"] = message
                self._persist(force=False)

    def _directory(self, provider):
        with self._lock:
            if self._current["networkBytes"] >= MAX_TRANSFER_BYTES:
                raise SourceError("Catalog update exhausted the shared transfer budget")
        registration = self.sources.require(provider)
        source = self.sources.open(
            provider, cancel=self._cancel, progress=self._progress, notice=self._notice
        )
        try:
            revision = source.revision()
            packages = source.packages(revision)
            if not REVISION.fullmatch(revision) or not packages:
                raise SourceError("Source must enumerate a nonempty fixed-revision directory")
            try:
                seed = json.loads(self.catalog.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                seed = {}
            curated = (
                {e["packageId"]: e for e in seed.get("entries", [])}
                if (
                    seed.get("schema") == "lw.asset-catalog/1"
                    and seed.get("provider") == provider
                    and seed.get("revision") == revision
                    and seed.get("dataset") == registration.dataset
                )
                else {}
            )
            with self._lock:
                self._current["discovered"] += len(packages)
                self._current.update(revision=revision, stage="indexing")
                self._persist()
            entries = []
            for package, oid in sorted(packages.items()):
                source.check()
                if not PACKAGE.fullmatch(package) or package in (".", "..") or not REVISION.fullmatch(oid):
                    raise SourceError("Source returned an invalid package identity or tree hash")
                entry = {
                    "packageId": package,
                    "title": package.replace("_", " ").replace("-", " "),
                    "source": {
                        "url": source.package_url(revision, package),
                        "files": None,
                        "bytes": None,
                        "rootUsd": None,
                        "bomItems": None,
                    },
                    "declaredLicense": None,
                    "preview": {
                        "url": f"/api/v1/library/catalog/entries/{provider}/{package}/{revision}/preview",
                        "reason": "Preview loads when visible; package details load when opened.",
                    },
                    "discovery": {
                        "treeOid": oid,
                        "nameBasis": "package-id",
                        "metadataComplete": False,
                        "metadataError": None,
                        "previewState": "on-demand",
                        "licenseBasis": "unknown until requested; not reviewed",
                    },
                }
                if package in curated:
                    entry["curated"] = curated[package]
                entries.append(entry)
            with self._lock:
                self._current["indexed"] += len(entries)
            return {
                "schema": SYNCED_CATALOG_SCHEMA,
                "provider": provider,
                "dataset": registration.dataset,
                "revision": revision,
                "indexedAt": now(),
                "coverage": "Complete lightweight directory; details and previews load on demand.",
                "entries": entries,
                "sync": {
                    "discovered": len(entries),
                    "indexed": len(entries),
                    "metadataFailed": 0,
                    "previewsCached": 0,
                    "previewsReused": 0,
                    "previewsMissing": 0,
                    "previewsFailed": 0,
                },
                "limits": dict(self.catalog.require().limits)
                if self.catalog.available
                else dict(DEFAULT_LIMITS),
            }
        finally:
            source.close()

    def _run(self):
        try:
            documents = copy.deepcopy(self._documents)
            completed = 0
            for provider in self._current["providers"]:
                try:
                    document = self._directory(provider)
                    parse_catalog(document)  # A malformed adapter cannot poison another source's update.
                    documents[provider] = document
                    result = {
                        "provider": provider,
                        "status": "succeeded",
                        "indexed": len(document["entries"]),
                    }
                    completed += 1
                except SyncCancelled:
                    raise
                except Exception as exc:
                    result = {"provider": provider, "status": "failed", "message": str(exc)[:1000]}
                    with self._lock:
                        self._current["errors"].append(
                            {"package": None, "provider": provider, "message": str(exc)[:1000]}
                        )
                with self._lock:
                    self._current["sources"].append(result)
                    self._persist()
            if not completed:
                raise SourceError("No source directory could be updated; existing search is preserved")
            if self._cancel.is_set():
                raise SyncCancelled()
            with self._lock:
                self._current["stage"] = "publishing"
                self._persist()
            document = {
                "schema": COLLECTION_SCHEMA,
                "sources": [documents[key] for key in sorted(documents)],
                "limits": dict(self.catalog.require().limits)
                if self.catalog.available
                else dict(DEFAULT_LIMITS),
            }
            revision = hashlib.sha1(json.dumps(document, sort_keys=True).encode()).hexdigest()
            filename = revision + "-" + self._current["id"] + ".json"
            path = self.snapshots / filename
            publish_file(durable_json, path, document)
            candidate = AssetCatalog(path, embedder=self.catalog.embedder)
            candidate.startup()
            if not candidate.available:
                raise SourceError("Cannot build the catalog: " + str(candidate.error))
            with self._lock:
                if self._cancel.is_set():
                    raise SyncCancelled()
                publish_file(
                    durable_json, self.root / "current.json", {"file": filename, "history": self._published}
                )
                self._published.append(filename)
                self.catalog.activate(
                    candidate, archive_paths=[self.snapshots / item for item in self._published]
                )
                self._documents = documents
                self._current.update(
                    status="succeeded",
                    stage="done",
                    finishedAt=now(),
                    snapshotId=candidate.snapshot.id,
                    message=None,
                )
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


__all__ = ["CatalogSync"]

"""Demand-loaded source facts and previews; never changes the search snapshot."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from packages.catalog.models import Source
from packages.catalog.source import (
    DATASET,
    SimReadySource,
    SourceError,
    SyncCancelled,
    image_extension,
    object_preview_path,
)
from packages.library.common import LibraryError

MAX_CACHE_BYTES = 2 * 1024 * 1024 * 1024
WORKERS = 8


class CatalogResources:
    def __init__(self, state_root, sources, *, max_cache_bytes=MAX_CACHE_BYTES):
        self.root = Path(state_root) / "catalog" / "cache"
        self.sources = sources
        self.max_cache_bytes = max_cache_bytes
        self._shutdown = threading.Event()
        self._slots = threading.BoundedSemaphore(WORKERS)
        self._locks = [threading.Lock() for _ in range(64)]
        self._cache_lock = threading.RLock()
        self._cached = None
        self._cache_bytes = 0
        self._failures = {}
        self.limiter = None

    def close(self):
        self._shutdown.set()

    def _cache_index(self):
        if self._cached is None:
            files = [(p, p.stat()) for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()]
            self._cached = OrderedDict(
                (p, stat.st_size) for p, stat in sorted(files, key=lambda item: item[1].st_mtime_ns)
            )
            self._cache_bytes = sum(self._cached.values())

    def _key(self, entry):
        source = self.sources.require(entry.provider)
        if (entry.discovery or {}).get("dataset", source.dataset) != source.dataset:
            raise LibraryError(
                "catalog_source_changed", "This version belongs to a different source dataset", 409
            )
        tree = (entry.discovery or {}).get("treeOid") or entry.revision
        return hashlib.sha256(
            f"3/{entry.provider}/{source.dataset}/{entry.package_id}/{tree}".encode()
        ).hexdigest()

    def _lock(self, key):
        return self._locks[int(key[:8], 16) % len(self._locks)]

    def _read(self, path):
        with self._cache_lock:
            self._cache_index()
            if not path.is_file() or path.is_symlink():
                return None
            try:
                data = path.read_bytes()
                os.utime(path, None)
                if path in self._cached:
                    self._cached.move_to_end(path)
                return data
            except OSError:
                return None

    def _store(self, path, data):
        with self._cache_lock:
            self._cache_index()
            if len(data) > self.max_cache_bytes:
                raise SourceError("Requested content exceeds the catalog cache limit")
            previous = self._cached.get(path, 0)
            while self._cache_bytes - previous + len(data) > self.max_cache_bytes:
                victim = next(p for p in self._cached if p != path)
                victim.unlink(missing_ok=True)
                self._cache_bytes -= self._cached.pop(victim)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + ".part-" + uuid.uuid4().hex)
            try:
                temporary.write_bytes(data)
                for attempt in range(5):
                    try:
                        os.replace(temporary, path)
                        break
                    except PermissionError:
                        if attempt == 4:
                            raise
                        time.sleep(0.05 * (attempt + 1))
            finally:
                temporary.unlink(missing_ok=True)
            self._cache_bytes += len(data) - previous
            self._cached[path] = len(data)
            self._cached.move_to_end(path)

    def _metadata(self, entry, source, key, *, full=True):
        path = self.root / "metadata" / (key + ".json")
        raw = self._read(path)
        # Upgrade previously downloaded metadata without fetching it again.
        if (
            raw is None
            and entry.provider == "simready"
            and (self.sources.require(entry.provider).dataset == DATASET)
            and (entry.discovery or {}).get("treeOid")
        ):
            old_key = hashlib.sha256(
                ("2/" + entry.package_id + "/" + entry.discovery["treeOid"]).encode()
            ).hexdigest()
            raw = self._read(self.root / "metadata" / (old_key + ".json"))
        inventory = None
        if raw is not None:
            try:
                value = json.loads(raw)
                if (
                    isinstance(value, dict)
                    and isinstance(value.get("source"), dict)
                    and (not value.get("errors") or time.time() - value.get("cachedAt", 0) < 30)
                ):
                    preview_valid = (not isinstance(source, SimReadySource)
                                     or value.get("thumbnailPath") is None
                                     or object_preview_path(entry.package_id, value["thumbnailPath"]))
                    if (not full and preview_valid) or (full and value.get("detailsLoaded", True)):
                        return value
                    if full or preview_valid:
                        inventory = value
            except (ValueError, TypeError):
                pass
        value = (
            source.metadata(entry.revision, entry.package_id, inventory=inventory)
            if full
            else source.preview_info(entry.revision, entry.package_id)
        )
        value["cachedAt"] = time.time()
        self._store(path, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        return value

    def _request(self, entry, operation, *, kind, retry=False):
        key = self._key(entry)
        failure_key = (key, kind)
        with self._lock(key):
            if retry:
                self._failures.pop(failure_key, None)
            failure = self._failures.get(failure_key)
            if failure and failure[0] > time.monotonic():
                raise LibraryError("catalog_content_unavailable", failure[1], 503)
            source = None
            try:
                with self._slots:
                    if self._shutdown.is_set():
                        raise SyncCancelled()
                    source = self.sources.open(entry.provider, cancel=self._shutdown)
                    return operation(source, key)
            except (SourceError, SyncCancelled, OSError) as exc:
                message = str(exc)[:1000] or "Catalog content request was interrupted"
                # Striped locking and bounded failure memory prevent an unbounded key registry.
                if len(self._failures) >= 1024:
                    self._failures.clear()
                self._failures[failure_key] = (time.monotonic() + 3, message)
                raise LibraryError("catalog_content_unavailable", message, 503) from exc
            finally:
                if source is not None:
                    source.close()

    def details(self, entry):
        if not entry.license.unknown or entry.provider not in self.sources.registrations:
            return entry

        def load(source, key):
            metadata = self._metadata(entry, source, key)
            facts = metadata["source"]
            return replace(
                entry,
                source=Source(
                    entry.source.url,
                    facts["files"],
                    facts["bytes"],
                    facts["rootUsd"],
                    facts.get("bomItems"),
                    (),
                ),
                license=replace(entry.license, name=metadata.get("declaredLicense")),
                discovery=copy.deepcopy(entry.discovery or {})
                | {
                    "metadataComplete": not bool(metadata.get("errors")),
                    "metadataError": "; ".join(metadata.get("errors", [])) or None,
                },
            )

        try:
            return self._request(entry, load, kind="details")
        except LibraryError as exc:
            if exc.detail["code"] != "catalog_content_unavailable":
                raise
            return replace(
                entry,
                discovery=copy.deepcopy(entry.discovery or {})
                | {
                    "metadataComplete": False,
                    "metadataError": str(exc),
                },
            )

    def preview(self, entry, *, retry=False):
        def load(source, key):
            pointer = self.root / "previews" / (key + ".json")
            raw = self._read(pointer)
            metadata = None
            filename = None
            if raw:
                try:
                    saved = json.loads(raw)
                    if not isinstance(saved, dict):
                        saved = {}
                    filename = saved.get("file")
                    if isinstance(source, SimReadySource):
                        selected_path = saved.get("path")
                        if selected_path is None:
                            # Old pointers carried only a digest. Their cached
                            # inventory still establishes which image it was.
                            cached = self._read(self.root / "metadata" / (key + ".json"))
                            cached_document = json.loads(cached) if cached else None
                            selected_path = (cached_document.get("thumbnailPath")
                                             if isinstance(cached_document, dict) else None)
                        if not object_preview_path(entry.package_id, selected_path):
                            filename = None
                except ValueError:
                    pass
            if not filename:
                metadata = self._metadata(entry, source, key, full=False)
                filename = metadata.get("previewFile")
            if isinstance(filename, str) and re.fullmatch(r"[0-9a-f]{64}\.(png|jpg|webp)", filename):
                data = self._read(self.root / "previews" / filename)
                if data and hashlib.sha256(data).hexdigest() == filename.split(".")[0]:
                    return data, image_extension(data)
            metadata = metadata or self._metadata(entry, source, key, full=False)
            thumbnail = metadata.get("thumbnailPath")
            if not thumbnail:
                raise LibraryError("catalog_preview_missing", "This source has no preview image", 404)
            data = source.file(entry.revision, thumbnail)
            extension = image_extension(data)
            filename = hashlib.sha256(data).hexdigest() + "." + extension
            self._store(self.root / "previews" / filename, data)
            self._store(pointer, json.dumps({"file": filename, "path": thumbnail, "role": "object"}).encode())
            return data, extension

        return self._request(entry, load, kind="preview", retry=retry)

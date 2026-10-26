"""Bounded, read-only SimReady discovery; never downloads a model or texture."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlparse

import httpx

from packages.catalog.models import PACKAGE, REVISION

DATASET = "nvidia/simready-assets"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TRANSFER_BYTES = 2 * 1024 * 1024 * 1024
MAX_PACKAGES = 100000
MAX_PAGES = 10000


def object_preview_path(package, path):
    from packages.catalog_metadata.recipes import preview_choice
    return (isinstance(path, str) and path.startswith(package + "/simready_usd/.thumbs/")
            and preview_choice([{"path": path, "size": 0}]) is not None)


class SyncCancelled(Exception):
    pass


class SourceError(Exception):
    pass


class RateState:
    """A provider's backoff is shared by indexing and demand reads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.until = 0


def image_extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise SourceError("Source preview is not a PNG, JPEG or WebP image")


class SimReadySource:
    def __init__(self, *, client=None, cancel=None, progress=None, notice=None, rate_state=None):
        self.client = client or httpx.Client(timeout=20, follow_redirects=True)
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda size: None)
        self.notice = notice or (lambda message: None)
        self.transferred = 0
        self._rate = rate_state or RateState()
        self._lock = self._rate.lock
        self.origin = "https://huggingface.co"

    @property
    def _rate_until(self):
        return self._rate.until

    @_rate_until.setter
    def _rate_until(self, value):
        self._rate.until = value

    def package_url(self, revision, package):
        return f"{self.origin}/datasets/{DATASET}/tree/{revision}/{quote(package, safe='')}"

    def close(self):
        self.client.close()

    def check(self):
        if self.cancel.is_set():
            raise SyncCancelled()

    def _read(self, url):
        last = None
        for attempt in range(3):
            while True:
                self.check()
                with self._lock:
                    if self.transferred >= MAX_TRANSFER_BYTES:
                        raise SourceError("Catalog sync transfer exceeds 2 GiB")
                    pause = max(0, self._rate_until - time.monotonic())
                if not pause:
                    break
                if self.cancel.wait(pause):
                    raise SyncCancelled()
            retry_after = 1 + attempt
            try:
                with self.client.stream("GET", url, headers={
                    "User-Agent": "spatial-sync-catalog/1", "Accept-Encoding": "identity",
                }) as response:
                    if response.status_code == 429:
                        header = response.headers.get("retry-after", "2")
                        try:
                            retry_after = float(header)
                        except ValueError:
                            try:
                                resume_at = parsedate_to_datetime(header)
                                retry_after = (resume_at - datetime.now(UTC)).total_seconds()
                            except (TypeError, ValueError, OverflowError):
                                retry_after = 2
                        if not math.isfinite(retry_after):
                            raise SourceError("Source returned a non-finite retry interval")
                        retry_after = max(1, retry_after)
                        reset = re.search(r"(?:^|;)\s*t=(\d+)", response.headers.get("ratelimit", ""))
                        if reset:
                            retry_after = max(retry_after, int(reset[1]) + 1)
                        with self._lock:
                            self._rate_until = max(self._rate_until, time.monotonic() + retry_after)
                        self.notice(f"来源限流，等待上游复位（约 {retry_after:.0f} 秒）；可取消本次同步。")
                    response.raise_for_status()
                    with self._lock:
                        waiting = self._rate_until > time.monotonic()
                    if not waiting:
                        self.notice(None)
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        self.check()
                        with self._lock:
                            self.transferred += len(chunk)
                            exceeded = self.transferred > MAX_TRANSFER_BYTES
                        self.progress(len(chunk))
                        if exceeded:
                            raise SourceError("Catalog sync transfer exceeds 2 GiB")
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE_BYTES:
                            raise SourceError("A metadata document or preview exceeds 2 MiB")
                    return bytes(chunks), response.links.get("next", {}).get("url")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (408, 429, 500, 502, 503, 504):
                    raise SourceError(f"Source returned HTTP {exc.response.status_code}") from exc
                last = exc
            except httpx.HTTPError as exc:
                last = exc
            if attempt < 2 and self.cancel.wait(retry_after):
                raise SyncCancelled()
        raise SourceError(f"Source request failed after 3 attempts: {type(last).__name__}") from last

    def _json(self, url):
        data, next_url = self._read(url)
        try:
            return json.loads(data), next_url
        except (UnicodeError, ValueError) as exc:
            raise SourceError("Source returned invalid JSON") from exc

    def revision(self):
        value, _ = self._json(
            f"{self.origin}/api/datasets/{DATASET}/revision/main?expand%5B%5D=sha"
        )
        revision = value.get("sha") if isinstance(value, dict) else None
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise SourceError("Source did not return a fixed Git revision")
        return revision

    def tree(self, revision, package=None):
        prefix = f"{self.origin}/api/datasets/{DATASET}/tree/{revision}"
        path = "/" + quote(package, safe="") if package else ""
        url = prefix + path + "?limit=1000" + ("&recursive=true" if package else "")
        seen = set()
        for _ in range(MAX_PAGES):
            self.check()
            if url in seen:
                raise SourceError("Source repeated a pagination cursor")
            seen.add(url)
            values, next_url = self._json(url)
            if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
                raise SourceError("Source tree is not a list of entries")
            yield from values
            if not next_url:
                return
            parsed = urlparse(next_url)
            expected = urlparse(prefix + path)
            if (parsed.scheme, parsed.netloc, parsed.path) != (
                expected.scheme, expected.netloc, expected.path
            ):
                raise SourceError("Source pagination left the fixed dataset/revision")
            url = next_url
        raise SourceError("Source tree pagination exceeds 10000 pages")

    def packages(self, revision):
        packages = {}
        for item in self.tree(revision):
            if item.get("type") != "directory":
                continue
            name, oid = item.get("path"), item.get("oid")
            if not isinstance(name, str) or not PACKAGE.fullmatch(name) or name in (".", ".."):
                raise SourceError("Source has an unsupported package directory identity")
            if not isinstance(oid, str) or not REVISION.fullmatch(oid):
                raise SourceError("Source package tree hash is missing")
            if name in packages:
                raise SourceError("Source repeated a package identity")
            packages[name] = oid
            if len(packages) > MAX_PACKAGES:
                raise SourceError("Source exceeds the 100000-package catalog bound")
        if not packages:
            raise SourceError("Source enumeration was empty; refusing to replace the catalog")
        return packages

    def file(self, revision, path):
        return self._read(
            f"{self.origin}/datasets/{DATASET}/resolve/{revision}/{quote(path, safe='/')}"
        )[0]

    def preview_info(self, revision, package):
        files = [item for item in self.tree(revision, package) if item.get("type") == "file"]
        if not files or any(
            not isinstance(item.get("path"), str)
            or not item["path"].startswith(package + "/")
            or ".." in item["path"].split("/")
            or type(item.get("size")) is not int or item["size"] < 0
            for item in files
        ):
            raise SourceError("Source package file inventory is incomplete or invalid")
        paths = {item["path"] for item in files}
        manifest = package + "/com.nvidia.simready.packaging.json"
        root_usds = sorted(path for path in paths if re.fullmatch(
            re.escape(package) + r"/simready_usd/[^/]+\.usd[ac]?", path
        ))
        from packages.catalog_metadata.recipes import preview_choice
        # Only the package model's thumbnail directory represents the object.
        # A smaller normal/material preview under Textures or source_assets is
        # not an object image. Maintenance and legacy lazy reads share policy.
        preview = preview_choice([item for item in files if object_preview_path(package, item["path"])])
        return {
            "source": {
                "files": len(files), "bytes": sum(item["size"] for item in files),
                "rootUsd": root_usds[0].removeprefix(package + "/") if root_usds else None,
                "bomItems": None,
            },
            "declaredLicense": None,
            "packagingPath": manifest if manifest in paths else None,
            "detailsLoaded": False,
            "thumbnailPath": preview["path"] if preview else None,
            "errors": [],
        }

    def metadata(self, revision, package, *, inventory=None):
        value = dict(inventory) if inventory is not None else self.preview_info(revision, package)
        errors = []
        declaration = None
        manifest = value.get("packagingPath")
        if manifest:
            try:
                data = json.loads(self.file(revision, manifest))
                license_ = data.get("license") if isinstance(data, dict) else None
                if isinstance(license_, str) and 0 < len(license_) <= 1000:
                    declaration = license_
            except (SourceError, ValueError, UnicodeError) as exc:
                errors.append(str(exc)[:300])
        else:
            errors.append("Source packaging manifest is missing")
        return value | {"declaredLicense": declaration, "detailsLoaded": True, "errors": errors}


__all__ = ["SimReadySource", "SourceError", "SyncCancelled", "image_extension"]

"""Shared, bounded Hugging Face transport; primary model files are never read."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from copy import deepcopy
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urljoin, urlparse

import httpx

from .models import MetadataError, canonical, digest, revision, source_profile

DEFAULT_BUDGETS = {
    "requests": 10000,
    "networkBytes": 256 * 1024 * 1024,
    "metadataBytes": 2 * 1024 * 1024,
    "tableBytes": 32 * 1024 * 1024,
    "previewBytes": 0,
    "imageCacheBytes": 2 * 1024 * 1024 * 1024,
    "seconds": 1800,
    "timeout": 20,
    "attempts": 3,
}
METADATA_SUFFIXES = frozenset((".json", ".jsonl", ".csv", ".tsv", ".parquet", ".md", ".txt", ".yaml", ".yml"))
IMAGE_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp"))


def safe_path(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or any(part in (".", "..") for part in value.split("/"))
        or any(ord(char) < 32 for char in value)
    ):
        raise MetadataError("unsafe_source_path", "Source path must stay inside its fixed dataset")
    return value


def image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise MetadataError("invalid_preview", "The source preview is not a supported image")


class HFHubReader:
    def __init__(
        self,
        *,
        cache_root=None,
        client=None,
        cancel=None,
        budgets=None,
        progress=None,
        origin=None,
    ):
        self.budgets = {**DEFAULT_BUDGETS, **(budgets or {})}
        for key in DEFAULT_BUDGETS:
            value = self.budgets[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise MetadataError("invalid_budget", f"{key} must be a finite non-negative budget")
        if self.budgets["attempts"] < 1 or self.budgets["attempts"] > 3:
            raise MetadataError("invalid_budget", "Source requests permit 1..3 attempts")
        self.client = client or httpx.Client(timeout=self.budgets["timeout"], follow_redirects=False)
        self.cancel = cancel or threading.Event()
        self.progress = progress or (lambda usage: None)
        self.origin = (origin or os.environ.get("LW_CATALOG_HF_ENDPOINT") or "https://huggingface.co").rstrip(
            "/"
        )
        endpoint = urlparse(self.origin)
        if (
            endpoint.username
            or endpoint.password
            or endpoint.path
            or endpoint.query
            or endpoint.fragment
            or endpoint.scheme not in ("http", "https")
            or (endpoint.scheme == "http" and endpoint.hostname not in ("localhost", "127.0.0.1", "::1"))
        ):
            raise MetadataError(
                "unsafe_source_url", "Hub endpoint must be HTTPS or an explicit loopback test endpoint"
            )
        self.started = time.monotonic()
        self.cache_root = Path(cache_root) if cache_root is not None else None
        if self.cache_root is not None:
            self.cache_root.mkdir(parents=True, exist_ok=True)
            (self.cache_root / "locators").mkdir(exist_ok=True)
            (self.cache_root / "previews").mkdir(exist_ok=True)
        self.usage = {
            "requests": 0,
            "networkBytes": 0,
            "previewBytes": 0,
            "embeddedPreviewBytes": 0,
            "embeddedPreviewDecodedBytes": 0,
            "previewBytesScope": "parquet-compressed-preview-columns-and-explicit-image-bodies",
            "cacheBytes": 0,
            "seconds": 0,
            "modelInputTokens": None,
            "modelOutputTokens": None,
        }
        self._external_preview_bytes = 0

    def close(self):
        self.client.close()

    def check(self):
        self.usage["seconds"] = time.monotonic() - self.started
        if self.cancel.is_set():
            raise MetadataError("maintenance_cancelled", "Source reading was cancelled", 409)
        if self.usage["seconds"] >= self.budgets["seconds"]:
            raise MetadataError(
                "budget_exhausted", "Source runtime budget was exhausted", 409, usage=deepcopy(self.usage)
            )

    def _allowed_url(self, url):
        parsed, origin = urlparse(url), urlparse(self.origin)
        if parsed.username or parsed.password or parsed.fragment:
            raise MetadataError(
                "unsafe_source_url", "Source URL contains unsupported credentials or fragments"
            )
        if (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc):
            return
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not (parsed.hostname.endswith(".hf.co") or parsed.hostname.endswith(".huggingface.co"))
        ):
            raise MetadataError("unsafe_source_url", "Source redirect left the Hugging Face resource hosts")

    def _cached(self, locator):
        if self.cache_root is None:
            return None
        index = self.cache_root / "locators" / (digest(locator)[7:] + ".json")
        try:
            document = json.loads(index.read_text("utf-8"))
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", document["sha256"]):
                return None
            directory = self.cache_root / "previews" if document.get("kind") == "preview" else self.cache_root
            path = directory / document["sha256"][7:]
            data = path.read_bytes()
            if "sha256:" + hashlib.sha256(data).hexdigest() != document["sha256"]:
                return None
            path.touch()
        except (OSError, KeyError, ValueError, TypeError):
            return None
        self.usage["cacheBytes"] += len(data)
        return data, document.get("next")

    def cache(self, data, *, locator=None, next_url=None, kind="metadata"):
        key = "sha256:" + hashlib.sha256(data).hexdigest()
        if self.cache_root is not None:
            directory = self.cache_root / "previews" if kind == "preview" else self.cache_root
            path = directory / key[7:]
            if not path.exists():
                temporary = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
            if locator is not None:
                index = self.cache_root / "locators" / (digest(locator)[7:] + ".json")
                temporary = index.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
                temporary.write_text(canonical({"sha256": key, "next": next_url, "kind": kind}), "utf-8")
                temporary.replace(index)
            if kind == "preview":
                self._trim_preview_cache()
        return key

    def _trim_preview_cache(self):
        candidates = []
        for path in (self.cache_root / "previews").iterdir():
            if not re.fullmatch(r"[0-9a-f]{64}", path.name):
                continue
            try:
                stat = path.stat()
                candidates.append((stat.st_mtime_ns, path, stat.st_size))
            except FileNotFoundError:
                pass
        size = sum(item[2] for item in candidates)
        for _, path, length in sorted(candidates):
            if size <= self.budgets["imageCacheBytes"]:
                break
            try:
                path.unlink()
                size -= length
            except FileNotFoundError:
                size -= length

    def _pause(self, seconds):
        until = time.monotonic() + max(0, seconds)
        while time.monotonic() < until:
            self.check()
            self.cancel.wait(min(0.25, until - time.monotonic()))

    @staticmethod
    def _retry_after(response, attempt):
        value = response.headers.get("retry-after")
        seconds = 1 + attempt
        if value:
            try:
                seconds = float(value)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    seconds = 2
        reset = re.search(r"(?:^|;)\s*t=(\d+)", response.headers.get("ratelimit", ""))
        if reset:
            seconds = max(seconds, int(reset[1]) + 1)
        if not math.isfinite(seconds):
            raise MetadataError("invalid_rate_limit", "Source returned a non-finite retry interval")
        return max(0, seconds)

    def _fetch(self, url, *, limit=None, kind="metadata", cached=True):
        self.check()
        self._allowed_url(url)
        limit = self.budgets["metadataBytes"] if limit is None else limit
        locator = {"url": url, "kind": kind}
        found = self._cached(locator) if cached else None
        if found is not None:
            if len(found[0]) > limit:
                raise MetadataError("source_document_limit", "Cached source exceeds this read limit")
            return found
        last_error = None
        for attempt in range(int(self.budgets["attempts"])):
            target = url
            try:
                for _ in range(8):
                    self.check()
                    self._allowed_url(target)
                    if self.usage["requests"] >= self.budgets["requests"]:
                        raise MetadataError(
                            "budget_exhausted",
                            "Source request budget was exhausted",
                            409,
                            usage=deepcopy(self.usage),
                        )
                    self.usage["requests"] += 1
                    self.progress(deepcopy(self.usage))
                    with self.client.stream(
                        "GET",
                        target,
                        follow_redirects=False,
                        headers={
                            "User-Agent": "spatial-sync-metadata/1",
                            "Accept-Encoding": "identity",
                        },
                        timeout=self.budgets["timeout"],
                    ) as response:
                        data = bytearray()
                        for chunk in response.iter_bytes():
                            self.check()
                            self.usage["networkBytes"] += len(chunk)
                            if kind == "preview":
                                self._external_preview_bytes += len(chunk)
                                self.usage["previewBytes"] += len(chunk)
                            self.progress(deepcopy(self.usage))
                            if self.usage["networkBytes"] > self.budgets["networkBytes"]:
                                raise MetadataError(
                                    "budget_exhausted",
                                    "Source network budget was exhausted",
                                    409,
                                    usage=deepcopy(self.usage),
                                )
                            if (
                                kind == "preview"
                                and self._external_preview_bytes > self.budgets["previewBytes"]
                            ):
                                raise MetadataError(
                                    "budget_exhausted",
                                    "Preview body budget was exhausted",
                                    409,
                                    usage=deepcopy(self.usage),
                                )
                            data.extend(chunk)
                            if len(data) > limit:
                                raise MetadataError(
                                    "source_document_limit", "Source document exceeds its bounded read limit"
                                )
                        if response.is_redirect:
                            target = urljoin(target, response.headers.get("location", ""))
                            continue
                        if response.status_code in (401, 403):
                            raise MetadataError(
                                "source_access_restricted", "Dataset resource requires additional access", 403
                            )
                        if response.status_code == 404:
                            raise MetadataError("source_missing", "Pinned source resource is missing", 404)
                        if response.status_code in (408, 429, 500, 502, 503, 504):
                            last_error = f"HTTP {response.status_code}"
                            if attempt + 1 < self.budgets["attempts"]:
                                self._pause(self._retry_after(response, attempt))
                            break
                        response.raise_for_status()
                        next_url = response.links.get("next", {}).get("url")
                        if cached:
                            self.cache(bytes(data), locator=locator, next_url=next_url, kind=kind)
                        return bytes(data), next_url
                else:
                    raise MetadataError("source_redirect_limit", "Source exceeded the redirect bound")
            except httpx.HTTPError as exc:
                last_error = type(exc).__name__
                if attempt + 1 < self.budgets["attempts"]:
                    self._pause(1 + attempt)
        raise MetadataError(
            "source_request_failed", f"Source failed after bounded attempts: {last_error}", 502
        )

    def _json(self, url, *, cached=True):
        data, next_url = self._fetch(url, cached=cached)
        try:
            return json.loads(data), next_url
        except (ValueError, UnicodeError) as exc:
            raise MetadataError("invalid_source_json", "Source returned invalid JSON") from exc

    def pin(self, profile, requested=None):
        profile = source_profile(profile)
        requested = requested or profile.get("revision")
        if requested:
            return revision(requested)
        value, _ = self._json(
            f"{self.origin}/api/datasets/{profile['dataset']}/revision/main?expand%5B%5D=sha", cached=False
        )
        if not isinstance(value, dict):
            raise MetadataError("invalid_revision", "Dataset did not return a fixed revision")
        return revision(value.get("sha"))

    def tree(self, profile, source_revision, path=None, *, recursive=False):
        profile = source_profile(profile)
        revision(source_revision)
        prefix = f"{self.origin}/api/datasets/{profile['dataset']}/tree/{source_revision}"
        if path:
            prefix += "/" + quote(safe_path(path), safe="/")
        url = prefix + "?limit=1000" + ("&recursive=true" if recursive else "")
        seen, paths = set(), set()
        for _ in range(10000):
            self.check()
            if url in seen:
                raise MetadataError("source_pagination_cycle", "Source repeated its pagination cursor")
            seen.add(url)
            values, next_url = self._json(url)
            if not isinstance(values, list):
                raise MetadataError("invalid_source_tree", "Dataset tree must be a list")
            for value in values:
                if not isinstance(value, dict) or value.get("type") not in ("file", "directory"):
                    raise MetadataError("invalid_source_tree", "Dataset tree has an invalid entry")
                name = safe_path(value.get("path"))
                if name in paths or (path and not name.startswith(path.rstrip("/") + "/")):
                    raise MetadataError("invalid_source_tree", "Dataset tree repeated or escaped its path")
                paths.add(name)
                if len(paths) > 1000000:
                    raise MetadataError("source_inventory_limit", "Dataset tree exceeds one million paths")
                yield value
            if not next_url:
                return
            expected, actual = urlparse(prefix), urlparse(next_url)
            if (actual.scheme, actual.netloc, actual.path) != (
                expected.scheme,
                expected.netloc,
                expected.path,
            ):
                raise MetadataError(
                    "source_revision_mismatch", "Pagination left its fixed dataset/revision/path"
                )
            url = next_url
        raise MetadataError("source_page_limit", "Dataset tree exceeded the pagination bound")

    def read_file(self, profile, source_revision, path, *, kind="metadata", retry=False, content_digest=None):
        profile = source_profile(profile)
        revision(source_revision)
        path = safe_path(path)
        suffix = PurePosixPath(path).suffix.lower()
        allowed = IMAGE_SUFFIXES if kind == "preview" else METADATA_SUFFIXES
        if suffix not in allowed:
            raise MetadataError(
                "primary_download_forbidden", "Metadata maintenance does not read primary model files", 403
            )
        limit = self.budgets["tableBytes"] if suffix == ".parquet" else self.budgets["metadataBytes"]
        if kind == "preview":
            limit = min(2 * 1024 * 1024, self.budgets["previewBytes"])
            if not limit:
                raise MetadataError("budget_exhausted", "Preview body reading is disabled for this run", 409)
        url = f"{self.origin}/datasets/{profile['dataset']}/resolve/{source_revision}/{quote(path, safe='/')}"
        content_locator = {"dataset": profile["dataset"], "path": path, "contentDigest": content_digest}
        if content_digest and not retry:
            found = self._cached(content_locator)
            if found is not None:
                if len(found[0]) > limit:
                    raise MetadataError("source_document_limit", "Cached source exceeds this read limit")
                return found[0]
        data, _ = self._fetch(url, limit=limit, kind=kind, cached=not retry)
        if content_digest:
            self.cache(data, locator=content_locator, kind=kind)
        return data

    def read_preview(self, profile, entry, retry=False):
        preview = entry.get("preview", {})
        if preview.get("role") != "object":
            raise MetadataError("preview_unavailable", "No object preview is available", 404)
        key = preview.get("cachedSha256") or preview.get("sha256")
        if key and not re.fullmatch(r"sha256:[0-9a-f]{64}", key):
            raise MetadataError("invalid_digest", "Preview content digest must be SHA-256")
        if key and self.cache_root is not None and not retry:
            for directory in (self.cache_root / "previews", self.cache_root):
                try:
                    path = directory / key.removeprefix("sha256:")
                    data = path.read_bytes()
                    if "sha256:" + hashlib.sha256(data).hexdigest() == key:
                        path.touch()
                        self.usage["cacheBytes"] += len(data)
                        return data, image_type(data)
                except OSError:
                    pass
        if not preview.get("path"):
            raise MetadataError(
                "preview_unavailable", "Object preview is not cached and has no stable source path", 404
            )
        data = self.read_file(
            profile,
            preview.get("revision") or entry["revision"],
            preview["path"],
            kind="preview",
            retry=retry,
        )
        source_key = preview.get("sha256")
        if source_key and "sha256:" + hashlib.sha256(data).hexdigest() != source_key:
            raise MetadataError(
                "preview_digest_mismatch", "Source preview changed from its pinned content digest", 409
            )
        return data, image_type(data)

    def enumerate(self, profile, revision=None):
        from .recipes import enumerate_collection

        profile = source_profile(profile)
        source_revision = self.pin(profile, revision)
        try:
            result = enumerate_collection(self, profile, source_revision)
        except MetadataError as exc:
            exc.details.setdefault("usage", deepcopy(self.usage))
            self.progress(deepcopy(self.usage))
            raise
        self.check()
        result["usage"] = deepcopy(self.usage)
        return result

    def discover(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MetadataError("invalid_limit", "Discovery limit must be 1..50")
        values, _ = self._json(
            f"{self.origin}/api/datasets?filter=modality%3A3d&limit={limit}&sort=lastModified&direction=-1",
            cached=False,
        )
        if not isinstance(values, list):
            raise MetadataError("invalid_discovery", "HF discovery response must be a list")
        candidates = []
        for value in values[:limit]:
            if not isinstance(value, dict) or not isinstance(value.get("id"), str):
                continue
            candidates.append(
                {
                    "dataset": value["id"],
                    "publisher": value["id"].split("/")[0],
                    "enabled": False,
                    "access": "restricted" if value.get("gated") else "public",
                    "recipe": None,
                    "tags": value.get("tags", [])[:32],
                    "url": f"{self.origin}/datasets/{value['id']}",
                    "requiresRecipe": True,
                }
            )
        return candidates

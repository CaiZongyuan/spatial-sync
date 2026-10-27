"""Bounded PNG derivatives for browsing; images are not validation evidence."""

import hashlib
import json
import struct
import tempfile
import threading
from pathlib import Path

from packages.library.common import LibraryError
from packages.library.read_cache import ReadCache, stamps
from packages.library.transactions import durable_json

MAX_PNG_BYTES = 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 128


class ThumbnailStore:
    def __init__(self, state_root):
        self.root = Path(state_root) / "library-thumbnails"
        self.cache = ReadCache()
        self.lock = threading.RLock()

    def key(self, resource, version, theme):
        return hashlib.sha256(f"{resource}:{version.id}:{version.content_hash}:{theme}".encode()).hexdigest()

    def url(self, resource, version, theme="light"):
        key = self.key(resource, version, theme)
        value = self.cache.get(key)
        if value is None:
            path = self.root / (key + ".json")
            try:
                if path.is_symlink() or self.root.is_symlink() or self.root.is_junction():
                    return None
                value = json.loads(path.read_bytes())
                if (value["key"] != key or len(value["image"]) != 64
                        or any(char not in "0123456789abcdef" for char in value["image"])):
                    return None
                paths = tuple(Path(item) for item in value["paths"])
                recorded = tuple(tuple(item) if item is not None else None for item in value["stamps"])
                if stamps(paths) != recorded:
                    return None
                watched = tuple(sorted({*paths, path, self.root / (value["image"] + ".png")}))
                expected = dict(zip(paths, recorded, strict=True))
                expected.update(zip((path, self.root / (value["image"] + ".png")),
                                    stamps((path, self.root / (value["image"] + ".png"))), strict=True))
                self.cache.put(key, value, watched, tuple(expected[item] for item in watched))
            except (OSError, ValueError, KeyError, TypeError):
                return None
        return "/api/v1/library/thumbnails/" + value["image"] + ".png"

    def put(self, resource, version, theme, payload, paths, recorded):
        with self.lock:
            return self._put(resource, version, theme, payload, paths, recorded)

    def _put(self, resource, version, theme, payload, paths, recorded):
        if (len(payload) < 33 or len(payload) > MAX_PNG_BYTES
                or payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR"):
            raise LibraryError("invalid_thumbnail", "Provide a PNG no larger than 1 MiB", 422)
        width, height = struct.unpack(">II", payload[16:24])
        if not (1 <= width <= 2048 and 1 <= height <= 2048):
            raise LibraryError("invalid_thumbnail", "PNG dimensions must be between 1 and 2048", 422)
        if self.root.is_symlink() or self.root.is_junction():
            raise LibraryError("thumbnail_unavailable", "Thumbnail storage is unavailable", 503)
        self.root.mkdir(parents=True, exist_ok=True)
        paths = tuple(sorted(set(paths)))
        if stamps(paths) != recorded:
            raise LibraryError("thumbnail_changed", "Preview inputs changed; reload before capturing", 409)
        image = hashlib.sha256(payload).hexdigest()
        destination = self.root / (image + ".png")
        if destination.is_symlink() or destination.is_junction():
            raise LibraryError("thumbnail_unavailable", "Thumbnail storage is unavailable", 503)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
            temporary.replace(destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        key = self.key(resource, version, theme)
        durable_json(self.root / (key + ".json"), {
            "schema": "lw.library-thumbnail/1", "key": key, "image": image,
            "paths": [str(path) for path in paths], "stamps": recorded,
        })
        self.cache.clear()
        self._prune()
        return {"thumbnailUrl": "/api/v1/library/thumbnails/" + image + ".png"}

    def _prune(self):
        indexes = sorted(self.root.glob("*.json"), key=lambda path: path.stat().st_mtime_ns)
        while True:
            live = set()
            for path in indexes:
                try:
                    live.add(json.loads(path.read_bytes())["image"] + ".png")
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            for path in self.root.glob("*.png"):
                if path.name not in live:
                    path.unlink(missing_ok=True)
            total = sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())
            if len(indexes) <= MAX_ENTRIES and total <= MAX_CACHE_BYTES:
                break
            if not indexes:
                break
            indexes.pop(0).unlink(missing_ok=True)
        self.cache.clear()

    def read(self, identifier):
        if len(identifier) != 64 or any(char not in "0123456789abcdef" for char in identifier):
            raise LibraryError("unknown_thumbnail", "Unknown thumbnail")
        path = self.root / (identifier + ".png")
        if path.is_symlink() or path.is_junction() or self.root.is_symlink() or self.root.is_junction():
            raise LibraryError("unknown_thumbnail", "Unknown thumbnail")
        try:
            payload = path.read_bytes()
        except OSError:
            raise LibraryError("unknown_thumbnail", "Unknown thumbnail") from None
        if len(payload) > MAX_PNG_BYTES or hashlib.sha256(payload).hexdigest() != identifier:
            raise LibraryError("unknown_thumbnail", "Thumbnail bytes changed")
        return payload

"""Repository-tracked lightweight asset catalog backed by JSONL, WebP and SQLite.

Three versioned JSONL files (NVIDIA SimReady, Scenario 3D, ChemLab Assets), a
sources manifest, and WebP object thumbnails are tracked in Git under catalog/.
A query cache (catalog/local/catalog.sqlite3) is built automatically on demand
from the versioned data files.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from packages.catalog.models import (
    DEFAULT_LIMITS,
    Acquisition,
    Adaptation,
    CatalogEntry,
    CatalogFormatError,
    CatalogSnapshot,
    License,
    LicenseEvidence,
    Source,
    UpstreamValidation,
    UsageFact,
)
from packages.catalog_metadata.models import DEFAULT_PROFILES, MetadataError
from packages.library.common import LibraryError

LOGGER = logging.getLogger(__name__)

CATALOG_SOURCES_SCHEMA = "lw.catalog-sources/1"
LEGACY_SIZE_PROBE_SCHEMA = "catalog-size-probe/1"


def _pair(value, fallback=""):
    value = value if isinstance(value, dict) else {}
    return {language: value.get(language) or fallback for language in ("en", "zh")}


def _tags(document):
    result = _pair(document.get("aliases"), [])
    shared = []
    for field in ("tags", "domains", "uses"):
        values = document.get(field, [])
        if isinstance(values, list):
            shared.extend(value for value in values if isinstance(value, str))
    return {language: tuple(dict.fromkeys([*result[language], *shared])) for language in ("en", "zh")}


def resolve_catalog_root(root: Path | str | None = None) -> Path:
    """Resolve the catalog root directory without hardcoding machine paths."""
    if root is not None:
        candidate = Path(root).resolve()
        if candidate.is_file():
            candidate = candidate.parent
        if (candidate / "data" / "sources.json").is_file():
            return candidate
        if (candidate / "sources.json").is_file() and candidate.name == "data":
            return candidate.parent
        return candidate

    env = os.environ.get("SPATIAL_CATALOG_ROOT") or os.environ.get("LW_CATALOG_ROOT")
    if env:
        candidate = Path(env).resolve()
        if candidate.is_file():
            candidate = candidate.parent
        if (candidate / "data" / "sources.json").is_file():
            return candidate
        return candidate

    # Search upwards from this file
    current = Path(__file__).resolve().parent
    for ancestor in [current, *current.parents]:
        probe = ancestor / "catalog"
        if (probe / "data" / "sources.json").is_file():
            return probe

    # Fallback to current working directory
    cwd_probe = Path.cwd() / "catalog"
    if (cwd_probe / "data" / "sources.json").is_file():
        return cwd_probe

    return cwd_probe


class LightweightCatalogStore:
    """Read-only and rebuildable catalog store backed by catalog/data/ and catalog/thumbnails/."""

    def __init__(self, root: Path | str | None = None):
        self.root = resolve_catalog_root(root)
        self.data_dir = self.root / "data"
        self.thumbnails_dir = self.root / "thumbnails"
        self.local_dir = self.root / "local"
        self.db_path = self.local_dir / "catalog.sqlite3"
        self.sources_path = self.data_dir / "sources.json"
        self._lock = threading.RLock()
        self._local_conn = None
        self._meta: dict[str, str] = {}
        self._sources: dict[str, dict] = {}
        self._collection_counts: dict[str, int] = {}
        self.release_id: str = ""
        self.built_at: str = ""
        self.entry_count: int = 0
        self._verified: bool = False
        # Cheap fingerprint of the database and versioned inputs. A different
        # value means another process rebuilt or replaced them, so the warm
        # cache must be verified again instead of serving stale release state.
        self._generation: tuple | None = None
        self._updating = False

    @classmethod
    def is_available_at(cls, root: Path | str | None = None) -> bool:
        resolved = resolve_catalog_root(root)
        return (resolved / "data" / "sources.json").is_file()

    def compute_data_digest(self) -> str:
        """Compute SHA-256 digest over sources.json and each collection JSONL file."""
        if not self.sources_path.is_file():
            raise FileNotFoundError(f"Missing sources manifest at {self.sources_path}")
        hasher = hashlib.sha256()
        hasher.update(b"sources.json\0")
        hasher.update(self.sources_path.read_bytes())
        jsonl_files = sorted(self.data_dir.glob("*.jsonl"))
        for jsonl_file in jsonl_files:
            hasher.update(jsonl_file.name.encode("utf-8") + b"\0")
            hasher.update(jsonl_file.read_bytes())
        return hasher.hexdigest()

    def _generation_token(self) -> tuple | None:
        """Identify the committed query database, never intermediate source files."""
        try:
            stat = self.db_path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size, stat.st_ino

    @contextlib.contextmanager
    def update_lock(self):
        """Serialize activation and cold cache verification across local processes."""
        with self._lock:
            if self._updating:
                yield
                return
            self.local_dir.mkdir(parents=True, exist_ok=True)
            with (self.local_dir / "catalog.lock").open("a+b") as lock:
                if os.name == "nt":
                    import msvcrt

                    if not lock.tell():
                        lock.write(b"0")
                        lock.flush()
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    self._updating = True
                    yield
                finally:
                    self._updating = False
                    if os.name == "nt":
                        lock.seek(0)
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock, fcntl.LOCK_UN)

    def ensure_db(self, force: bool = False) -> Path:
        """Follow committed replacements; verify source files only on startup/rebuild."""
        with self._lock:
            if not force and self._verified and self.db_path.is_file():
                token = self._generation_token()
                if token is not None and token == self._generation:
                    return self.db_path
                try:
                    self.refresh()
                    return self.db_path
                except (sqlite3.Error, ValueError, KeyError):
                    self._verified = False
            return self._verify_or_rebuild(force)

    def _verify_or_rebuild(self, force: bool) -> Path:
        with self.update_lock():
            if not self.sources_path.is_file():
                raise FileNotFoundError(f"Cannot initialize lightweight catalog: missing {self.sources_path}")

            expected_digest = self.compute_data_digest()
            if not force and self.db_path.is_file():
                db = None
                try:
                    db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
                    db.execute("PRAGMA query_only=ON")
                    integrity = db.execute("PRAGMA integrity_check").fetchone()
                    if integrity and integrity[0] == "ok":
                        meta_rows = dict(db.execute("SELECT key, value FROM meta").fetchall())
                        if meta_rows.get("data_digest") == expected_digest:
                            self._meta = meta_rows
                            self._load_cached_state(db)
                            self._verified = True
                            self._generation = self._generation_token()
                            return self.db_path
                except Exception as exc:
                    LOGGER.warning(
                        "Existing catalog database at %s failed verification (%s); rebuilding.",
                        self.db_path,
                        exc,
                    )
                finally:
                    if db is not None:
                        try:
                            db.close()
                        except Exception:
                            pass

            self._rebuild_db(expected_digest)
            self._verified = True
            self._generation = self._generation_token()
            return self.db_path

    def _load_cached_state(self, db: sqlite3.Connection):
        self.release_id = self._meta.get("release_id", "")
        self.built_at = self._meta.get("built_at", "")
        self.entry_count = int(self._meta.get("entry_count", 0))
        self._sources = {
            row[0]: json.loads(row[1])
            for row in db.execute("SELECT id, document FROM sources ORDER BY id").fetchall()
        }
        self._collection_counts = {
            row[0]: row[1]
            for row in db.execute("SELECT collection, COUNT(*) FROM entries GROUP BY collection").fetchall()
        }

    def _align_with_connection(self, db: sqlite3.Connection):
        """Serve release state from the exact database generation this connection sees.

        Another process can replace the SQLite file between ensure_db() and the
        connection opening. Reading the release row from the connection itself
        keeps one query on one release instead of mixing state and rows.
        """
        try:
            row = db.execute("SELECT value FROM meta WHERE key='release_id'").fetchone()
        except sqlite3.Error:
            self._verified = False
            self._generation = None
            raise
        if row is None or row[0] == self.release_id:
            return
        with self._lock:
            if row[0] == self.release_id:
                return
            self._meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
            self._load_cached_state(db)
            # The file moved under this reader: recompute the data digest on the
            # next ensure_db instead of trusting the warm generation token.
            self._verified = False
            self._generation = None
            LOGGER.info("Catalog database changed on disk; serving release %s", self.release_id)

    def _release_document(self) -> dict | None:
        if not self.release_id:
            return None
        return {
            "id": self.release_id,
            "createdAt": self.built_at,
            "entryCount": self.entry_count,
            "collections": [
                {
                    "id": coll,
                    "dataset": src["dataset"],
                    "revision": src["revision"],
                    "entryCount": self._collection_counts.get(coll, 0),
                }
                for coll, src in sorted(self._sources.items())
            ],
        }

    def release_from_connection(self, db: sqlite3.Connection) -> dict:
        """Return the release of the database this connection is reading."""
        with self._lock:
            self._align_with_connection(db)
            latest = self._release_document()
        if latest is None:
            raise LibraryError("catalog_unavailable", "Catalog is unavailable", 503)
        return latest

    def refresh(self) -> None:
        """Reload cached release state from the database currently on disk."""
        with self._lock:
            self._verified = False
            self._generation = None
            if not self.db_path.is_file():
                self.release_id = ""
                self.built_at = ""
                self.entry_count = 0
                self._meta = {}
                self._sources = {}
                self._collection_counts = {}
                return
            generation = self._generation_token()
            db = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            try:
                db.execute("PRAGMA query_only=ON")
                self._meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
                self._load_cached_state(db)
            finally:
                db.close()
            self._generation = generation
            self._verified = generation is not None and generation == self._generation_token()

    def _rebuild_db(self, data_digest: str):
        self.local_dir.mkdir(parents=True, exist_ok=True)
        raw_sources = json.loads(self.sources_path.read_text(encoding="utf-8"))
        schema = raw_sources.get("schema")
        if schema not in (CATALOG_SOURCES_SCHEMA, LEGACY_SIZE_PROBE_SCHEMA):
            raise CatalogFormatError(f"Unsupported sources schema: {schema}")
        sources = raw_sources.get("sources", {})

        tmp_db = self.db_path.with_name(f"catalog.sqlite3.tmp.{os.getpid()}")
        if tmp_db.is_file():
            try:
                tmp_db.unlink()
            except OSError:
                pass

        db = sqlite3.connect(tmp_db)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript("""
                CREATE TABLE meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                ) WITHOUT ROWID;

                CREATE TABLE sources (
                    id TEXT PRIMARY KEY,
                    document TEXT NOT NULL
                ) WITHOUT ROWID;

                CREATE TABLE entries (
                    release_id TEXT NOT NULL,
                    collection TEXT NOT NULL,
                    id TEXT NOT NULL,
                    category TEXT,
                    document TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    PRIMARY KEY(collection, id)
                ) WITHOUT ROWID;

                CREATE INDEX entries_by_category ON entries(category, collection, id);
                CREATE VIEW release_entries AS SELECT * FROM entries;
            """)

            db.executemany(
                "INSERT INTO sources VALUES (?, ?)",
                [
                    (k, json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                    for k, v in sorted(sources.items())
                ],
            )

            release_id = f"sha256:{data_digest}"
            total_entries = 0
            collection_counts = {}

            for coll, source in sorted(sources.items()):
                jsonl_path = self.data_dir / f"{coll}.jsonl"
                if not jsonl_path.is_file():
                    LOGGER.warning("Source collection %s missing JSONL file at %s", coll, jsonl_path)
                    continue

                batch = []
                coll_count = 0
                with jsonl_path.open("r", encoding="utf-8") as fp:
                    for line in fp:
                        line = line.strip()
                        if not line:
                            continue
                        item = json.loads(line)
                        raw_id = item["id"]
                        quoted_id = quote(raw_id, safe="._-")
                        full_id = f"{coll}/{quoted_id}@{source['revision']}"
                        doc = {
                            **item,
                            "id": full_id,
                            "collectionId": coll,
                            "upstreamId": raw_id,
                            "revision": source["revision"],
                            "source": {
                                "dataset": source["dataset"],
                                "url": source.get("url")
                                or f"https://huggingface.co/datasets/{source['dataset']}",
                            },
                        }
                        searchable = {
                            k: item[k]
                            for k in ("name", "summary", "aliases", "category", "tags", "domains", "uses")
                            if k in item
                        }
                        doc_json = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                        search_text = json.dumps(searchable, ensure_ascii=False, sort_keys=True).casefold()
                        batch.append((release_id, coll, full_id, item.get("category"), doc_json, search_text))
                        coll_count += 1
                        if len(batch) >= 1000:
                            db.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)", batch)
                            batch.clear()

                if batch:
                    db.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?)", batch)
                collection_counts[coll] = coll_count
                total_entries += coll_count

            built_at = datetime.now(UTC).isoformat()
            meta_entries = [
                ("data_digest", data_digest),
                ("release_id", release_id),
                ("entry_count", str(total_entries)),
                ("built_at", built_at),
            ]
            db.executemany("INSERT INTO meta VALUES (?, ?)", meta_entries)
            db.commit()

            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise CatalogFormatError(f"Built SQLite failed integrity check: {integrity}")

            db.execute("VACUUM")
        finally:
            db.close()

        try:
            # Atomically replace destination (os.replace / Path.replace is atomic)
            tmp_db.replace(self.db_path)

            self.release_id = release_id
            self.built_at = built_at
            self.entry_count = total_entries
            self._sources = dict(sources)
            self._collection_counts = collection_counts
            self._meta = {k: v for k, v in meta_entries}
            self._verified = True
            LOGGER.info(
                "Lightweight catalog SQLite built at %s (%d entries, release %s)",
                self.db_path,
                total_entries,
                release_id,
            )
        finally:
            if tmp_db.is_file():
                try:
                    tmp_db.unlink()
                except OSError:
                    pass

    @contextlib.contextmanager
    def transaction(self, write: bool = False):
        self.ensure_db()
        mode = "rw" if write else "ro"
        conn = sqlite3.connect(f"file:{self.db_path}?mode={mode}", uri=True)
        try:
            if not write:
                conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            self._align_with_connection(conn)
            yield conn
            if write:
                conn.commit()
        except Exception:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    def latest_release(self) -> dict | None:
        with self._lock:
            self.ensure_db()
            return self._release_document()

    def release(self, identifier: str | None = None) -> dict:
        latest = self.latest_release()
        if latest is None:
            raise LibraryError("catalog_unavailable", "Catalog is unavailable", 503)
        if identifier is not None and identifier != latest["id"]:
            raise LibraryError("release_not_found", f"Unknown release {identifier}", 404)
        return latest

    def collections(self) -> list[dict]:
        with self._lock:
            self.ensure_db()
            return [
                {
                    "id": coll,
                    "dataset": src["dataset"],
                    "revision": src["revision"],
                    "url": src.get("url") or f"https://huggingface.co/datasets/{src['dataset']}",
                    "enabled": True,
                    "entryCount": self._collection_counts.get(coll, 0),
                }
                for coll, src in sorted(self._sources.items())
            ]

    def collection(self, collection_id: str) -> dict:
        with self._lock:
            self.ensure_db()
            if collection_id not in self._sources:
                raise MetadataError("unknown_collection", f"Unknown collection {collection_id}", 404)
            src = self._sources[collection_id]
            return {
                "id": collection_id,
                "dataset": src["dataset"],
                "recipe": next(
                    (profile["recipe"] for profile in DEFAULT_PROFILES if profile["id"] == collection_id),
                    None,
                ),
                "revision": src["revision"],
                "url": src.get("url") or f"https://huggingface.co/datasets/{src['dataset']}",
                "enabled": True,
                "entryCount": self._collection_counts.get(collection_id, 0),
            }

    def register_collection(self, profile: dict) -> dict:
        raise MetadataError(
            "not_supported",
            "Lightweight collections come from catalog/data/sources.json",
            409,
        )

    @property
    def blob_root(self) -> Path:
        path = self.local_dir / "blobs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def thumbnail_root(self) -> Path:
        return self.thumbnails_dir

    def entry_preview(self, entry: dict) -> dict | None:
        preview = entry.get("preview", {})
        thumbnail = preview.get("thumbnail")
        if isinstance(thumbnail, dict) and thumbnail.get("state") == "available":
            return thumbnail
        if isinstance(thumbnail, str):
            sha = thumbnail.removesuffix(".webp").removeprefix("sha256:")
            return {"sha256": f"sha256:{sha}", "state": "available"}
        return None

    def save_preview(self, entry: dict, data: bytes, expected_output: str | None = None) -> tuple[dict, bool]:
        from packages.catalog_metadata.thumbnails import create_thumbnail

        encoded, thumbnail = create_thumbnail(data)
        key = thumbnail["sha256"]
        digest = key.removeprefix("sha256:")
        if expected_output and expected_output != key:
            raise MetadataError("thumbnail_corrupt", "Thumbnail hash did not match expected hash", 409)
        directory = self.thumbnails_dir if expected_output else self.local_dir / "thumbnails"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{digest}.webp"
        added = not target.is_file()
        if added or target.read_bytes() != encoded:
            target.write_bytes(encoded)
        return thumbnail, added

    def vocabulary(self, version: str | None = None) -> dict:
        from packages.catalog_metadata.models import default_vocabulary

        doc = default_vocabulary()
        if version is not None and version != doc.get("version"):
            raise MetadataError("unknown_vocabulary", f"Unknown vocabulary version {version}", 404)
        return doc

    def export(self) -> dict:
        raise MetadataError(
            "not_supported", "Export is not supported for lightweight catalog; data is Git versioned", 400
        )

    def correct(self, *args, **kwargs):
        raise MetadataError("not_supported", "Corrections are not supported for the lightweight catalog", 409)

    def retract(self, *args, **kwargs):
        raise MetadataError("not_supported", "Corrections are not supported for the lightweight catalog", 409)

    def select_organization(self, *args, **kwargs):
        raise MetadataError(
            "not_supported", "Maintenance organization is not supported for the lightweight catalog", 409
        )

    def organization_history(self, *args, **kwargs):
        raise MetadataError(
            "not_supported", "Maintenance organization is not supported for the lightweight catalog", 409
        )

    def set_vocabulary(self, *args, **kwargs):
        raise MetadataError("not_supported", "The lightweight catalog serves a fixed vocabulary", 409)

    @staticmethod
    def _entry_row(db: sqlite3.Connection, identity: str):
        clean_id = identity.removeprefix("catalog:")
        row = db.execute("SELECT document FROM entries WHERE id=?", (clean_id,)).fetchone()
        if row is None:
            # If an explicit revision was requested in identity, it must match that exact revision
            if "@" in clean_id:
                raise MetadataError("unknown_entry", f"Unknown catalog entry {identity}", 404)
            # Try finding by upstreamId (bare ID without revision)
            row = db.execute(
                "SELECT document FROM entries WHERE json_extract(document, '$.upstreamId')=?",
                (clean_id,),
            ).fetchone()
        if row is None and "/" in clean_id and "@" not in clean_id:
            coll, bare = clean_id.split("/", 1)
            sql = (
                "SELECT document FROM entries WHERE collection=? "
                "AND json_extract(document, '$.upstreamId')=?"
            )
            row = db.execute(sql, (coll, bare)).fetchone()
        return row

    def read_entry(self, identity: str, release_id: str | None = None) -> tuple[dict, dict]:
        """Read one entry and the release of the same database generation."""
        with self.transaction(write=False) as db:
            latest = self.release_from_connection(db)
            if release_id is not None and release_id != latest["id"]:
                raise MetadataError("release_not_found", f"Unknown release {release_id}", 404)
            row = self._entry_row(db, identity)
            if row is None:
                raise MetadataError("unknown_entry", f"Unknown catalog entry {identity}", 404)
            doc = json.loads(row[0])
        # Ensure preview thumbnail has dict format matching queries.py contract
        preview = doc.get("preview", {})
        thumbnail = preview.get("thumbnail")
        if isinstance(thumbnail, str):
            sha = thumbnail.removesuffix(".webp").removeprefix("sha256:")
            preview["thumbnail"] = {"sha256": f"sha256:{sha}", "state": "available"}
            doc["preview"] = preview
        return latest, doc

    def entry(self, identity: str, release_id: str | None = None) -> dict:
        return self.read_entry(identity, release_id)[1]

    def thumbnail(self, key: str) -> bytes:
        digest = key.removeprefix("sha256:").removesuffix(".webp")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise MetadataError("invalid_digest", f"Invalid thumbnail digest {key}", 400)
        image_path = self.thumbnails_dir / f"{digest}.webp"
        if not image_path.is_file():
            image_path = self.local_dir / "thumbnails" / f"{digest}.webp"
        if not image_path.is_file():
            raise MetadataError("thumbnail_unavailable", "Thumbnail not found", 404)
        data = image_path.read_bytes()
        if len(data) < 12 or not data.startswith(b"RIFF") or data[8:12] != b"WEBP":
            raise MetadataError("thumbnail_corrupt", "Thumbnail file is corrupt", 409)
        if hashlib.sha256(data).hexdigest() != digest:
            raise MetadataError("thumbnail_corrupt", "Thumbnail digest does not match its identity", 409)
        return data

    def load_entries(self) -> tuple[CatalogSnapshot, tuple[CatalogEntry, ...]]:
        with self.transaction(write=False) as db:
            latest = self.release_from_connection(db)
            rows = db.execute("SELECT collection, document FROM entries ORDER BY collection, id").fetchall()
        release_id, built_at = latest["id"], latest["createdAt"]
        entries_list: list[CatalogEntry] = []

        for coll, doc_json in rows:
            doc = json.loads(doc_json)
            raw_id = doc["upstreamId"]
            package_id = quote(raw_id, safe="._-")
            revision = doc["revision"]
            facts = doc.get("facts", {})
            aliases = _pair(doc.get("aliases"), [])
            names = _pair(doc.get("name"), doc.get("upstreamId", package_id))
            descriptions = _pair(doc.get("summary"))
            tags = _tags(doc)

            license_fact = facts.get("license")
            license_name = license_fact.get("name") if isinstance(license_fact, dict) else license_fact
            license = License(license_name, False, (), None, LicenseEvidence(None, None), (), True)
            usage = {
                name: UsageFact(None, True, "Metadata maintenance does not establish physical use.")
                for name in ("dressing", "staticCollision")
            }
            dimensions = facts.get("dimensionsM")
            adaptation = Adaptation(
                "gap",
                ("No Lab asset adaptation is verified.",),
                tuple(dimensions) if dimensions is not None else None,
                None,
                None,
                "unknown",
                (),
            )
            upstream_validation = UpstreamValidation("unknown", "unknown", "unknown", (), "publisher")
            acquisition = Acquisition(False, ("Source discovery does not grant acquisition admission.",))
            source = Source(
                doc.get("source", {}).get("url")
                or f"https://huggingface.co/datasets/{doc.get('source', {}).get('dataset', '')}",
                facts.get("fileCount"),
                facts.get("sourceBytes"),
                None,
                None,
                (),
            )

            old_preview = doc.get("preview", {})
            thumbnail = old_preview.get("thumbnail")
            state = old_preview.get("state", "not_collected")
            path = old_preview.get("path")
            usable = old_preview.get("role") == "object" and path and state not in ("missing", "suspect")

            if thumbnail:
                sha = thumbnail if isinstance(thumbnail, str) else thumbnail.get("sha256", "")
                sha = sha.removesuffix(".webp").removeprefix("sha256:")
                preview_document = {
                    "kind": "local",
                    "state": "available",
                    "role": old_preview.get("role", "object"),
                    "url": f"/api/v1/library/catalog/maintenance/thumbnails/sha256:{sha}.webp",
                    "reason": "Local WebP thumbnail bound to this published source input.",
                }
            elif usable:
                preview_entry = quote(doc["id"], safe="._-")
                preview_url = (
                    f"/api/v1/library/catalog/maintenance/preview?entry={preview_entry}"
                    f"&release={release_id}"
                )
                preview_document = {
                    "kind": "on-demand",
                    "state": state,
                    "role": old_preview.get("role"),
                    "url": preview_url,
                    "reason": old_preview.get("reason")
                    or "Object preview has not been read; it loads when visible.",
                }
            else:
                preview_document = {
                    "kind": "unavailable",
                    "state": state if state in ("missing", "suspect", "failed") else "missing",
                    "role": old_preview.get("role"),
                    "url": None,
                    "reason": old_preview.get("reason") or "No object preview is supplied by this source.",
                }

            doc["release"] = release_id
            entry = CatalogEntry(
                provider=coll,
                revision=revision,
                package_id=package_id,
                local_asset_id=None,
                kind="unknown",
                source=source,
                names=names,
                aliases={k: tuple(v) for k, v in aliases.items()},
                descriptions=descriptions,
                tags=tags,
                license=license,
                usage=usage,
                adaptation=adaptation,
                upstream_validation=upstream_validation,
                acquisition=acquisition,
                preview=preview_document,
                metadata=doc,
            )
            entries_list.append(entry)

        built_at_dt = datetime.fromisoformat(built_at) if built_at else datetime.now(UTC)
        snapshot = CatalogSnapshot(
            id=release_id,
            provider="multiple",
            dataset="multiple",
            revision=release_id.removeprefix("sha256:")[:40],
            indexed_at=built_at_dt,
            indexed_at_text=built_at,
            coverage="Repository-backed lightweight catalog (SimReady, Scenario, ChemLab).",
            entry_count=len(entries_list),
            limits=DEFAULT_LIMITS,
            sync={
                "discovered": len(entries_list),
                "indexed": len(entries_list),
                "metadataReused": len(entries_list),
                "metadataFailed": 0,
                "previewsCached": sum(
                    1 for e in entries_list if e.preview and e.preview.get("state") == "available"
                ),
                "previewsReused": sum(
                    1 for e in entries_list if e.preview and e.preview.get("state") == "available"
                ),
                "previewsMissing": sum(
                    1 for e in entries_list if e.preview and e.preview.get("state") == "missing"
                ),
                "previewsFailed": sum(
                    1 for e in entries_list if e.preview and e.preview.get("state") == "failed"
                ),
            },
            sources=tuple(
                {
                    "provider": value["id"],
                    "dataset": value["dataset"],
                    "revision": value["revision"],
                    "entryCount": value["entryCount"],
                    "indexedAt": built_at,
                    "coverage": f"{value['id']} snapshot",
                }
                for value in latest["collections"]
            ),
        )

        return snapshot, tuple(entries_list)

    def close(self):
        pass


class LightweightMaintenance:
    """Thin compatibility layer for catalog metadata routes without supervisor/worker machinery.

    The lightweight catalog has no maintenance runs: data comes from versioned
    source files and a separate sync command. Mutating maintenance routes answer
    with a structured ``not_supported`` instead of failing inside the router.
    """

    MESSAGE = "Lightweight catalog has no maintenance runs; update catalog/data and run tools/sync_catalog.py"

    def __init__(self, store: LightweightCatalogStore):
        self.store = store
        self.release_preparer = None

    async def startup(self):
        self.store.ensure_db()

    async def close(self):
        self.store.close()

    def status(self) -> dict:
        release = self.store.latest_release()
        return {
            "schema": "lw.catalog-maintenance-status/1",
            "status": "idle",
            "release": release,
            "entryCount": release["entryCount"] if release else 0,
            "collections": self.store.collections(),
            "runs": [],
            "runtime": {"configured": False, "profile": None},
            "capabilities": {
                "maintenance": False,
                "externalOrganization": False,
                "localThumbnails": True,
                "nativeQueries": True,
            },
            "workerEnabled": False,
        }

    def callback_url(self, request) -> str | None:
        return None

    def _unsupported(self):
        raise MetadataError("not_supported", self.MESSAGE, 409)

    def start(self, *args, **kwargs):
        self._unsupported()

    def show(self, *args, **kwargs):
        raise MetadataError("run_not_found", "Lightweight catalog keeps no maintenance run history", 404)

    def cancel(self, *args, **kwargs):
        self._unsupported()

    def resume(self, *args, **kwargs):
        self._unsupported()

    def phase(self, *args, **kwargs):
        self._unsupported()

    def checkpoint(self, *args, **kwargs):
        self._unsupported()

    def ingest(self, *args, **kwargs):
        self._unsupported()

    def complete_inventory(self, *args, **kwargs):
        self._unsupported()

    def ingest_extracted(self, *args, **kwargs):
        self._unsupported()

    def publish_inventory(self, *args, **kwargs):
        self._unsupported()

    def claim(self, *args, **kwargs):
        self._unsupported()

    def await_agent(self, *args, **kwargs):
        self._unsupported()

    def preview_claim(self, *args, **kwargs):
        self._unsupported()

    def preview_submit(self, *args, **kwargs):
        self._unsupported()

    def submit(self, *args, **kwargs):
        self._unsupported()

    def execution_start(self, *args, **kwargs):
        self._unsupported()

    def batch_error(self, *args, **kwargs):
        self._unsupported()

    def finish(self, *args, **kwargs):
        self._unsupported()

    def fail(self, *args, **kwargs):
        self._unsupported()

    def publish(self, *args, **kwargs):
        self._unsupported()

    def restore(self, *args, **kwargs):
        self._unsupported()

    def authorize_worker(self, path: str, token: str) -> bool:
        return False

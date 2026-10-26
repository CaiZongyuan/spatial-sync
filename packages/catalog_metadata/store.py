"""Transactional local metadata, immutable releases and independent corrections.

No worker is given a database connection. ``transaction`` is an internal host
seam so the maintenance service can persist its own queue in the same SQLite
file without inventing another state store.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from .models import (
    DEFAULT_PROFILES,
    SCHEMA,
    MetadataError,
    apply_fields,
    canonical,
    default_vocabulary,
    digest,
    normalized_entry,
    now,
    organization_fields,
    revision,
    source_profile,
    text,
    vocabulary_document,
)

TABLES = (
    "collections",
    "inventories",
    "inventory_entries",
    "organizations",
    "organization_heads",
    "corrections",
    "correction_heads",
    "submissions",
    "releases",
    "release_entries",
    "metadata_state",
)


class MetadataStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "metadata.sqlite3"
        self.blob_root = self.root / "blobs"
        self.blob_root.mkdir(exist_ok=True)
        (self.blob_root / "previews").mkdir(exist_ok=True)
        with self.transaction() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise MetadataError("metadata_version", "Unsupported metadata database version", 409)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inventories(id TEXT PRIMARY KEY, collection_id TEXT NOT NULL,
                    revision TEXT NOT NULL, complete INTEGER NOT NULL, document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS inventory_entries(inventory_id TEXT NOT NULL,
                    upstream_id TEXT NOT NULL, input_digest TEXT NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(inventory_id,upstream_id));
                CREATE TABLE IF NOT EXISTS organizations(collection_id TEXT NOT NULL,
                    upstream_id TEXT NOT NULL,
                    input_digest TEXT NOT NULL, version INTEGER NOT NULL, document TEXT NOT NULL,
                    PRIMARY KEY(collection_id,upstream_id,input_digest,version));
                CREATE TABLE IF NOT EXISTS organization_heads(collection_id TEXT NOT NULL,
                    upstream_id TEXT NOT NULL,input_digest TEXT NOT NULL,version INTEGER NOT NULL,
                    selection_version INTEGER NOT NULL,
                    PRIMARY KEY(collection_id,upstream_id,input_digest));
                CREATE TABLE IF NOT EXISTS corrections(id TEXT PRIMARY KEY,collection_id TEXT NOT NULL,
                    upstream_id TEXT NOT NULL,version INTEGER NOT NULL,
                    document TEXT NOT NULL,active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS correction_heads(collection_id TEXT NOT NULL,
                    upstream_id TEXT NOT NULL,version INTEGER NOT NULL,
                    PRIMARY KEY(collection_id,upstream_id));
                CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY,payload_digest TEXT NOT NULL,
                    document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,document TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS release_entries(release_id TEXT NOT NULL,id TEXT NOT NULL,
                    document TEXT NOT NULL,search_text TEXT NOT NULL,PRIMARY KEY(release_id,id));
                CREATE INDEX IF NOT EXISTS inventory_by_collection ON inventories(collection_id);
                CREATE TABLE IF NOT EXISTS metadata_state(key TEXT PRIMARY KEY,document TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(organization_heads)")}
            if "selection_version" not in columns:
                db.execute(
                    "ALTER TABLE organization_heads ADD COLUMN selection_version INTEGER NOT NULL DEFAULT 0"
                )
                db.execute("UPDATE organization_heads SET selection_version=version")
            for profile in DEFAULT_PROFILES:
                db.execute(
                    "INSERT OR IGNORE INTO collections VALUES(?,?)",
                    (profile["id"], canonical(source_profile(profile))),
                )
            db.execute(
                "INSERT OR IGNORE INTO metadata_state VALUES('vocabulary',?)",
                (canonical(default_vocabulary()),),
            )

    @contextmanager
    def transaction(self, *, write=True):
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def close(self):
        """Connections are operation-scoped; retained stores own no process handles."""

    def collections(self):
        with self.transaction(write=False) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT document FROM collections ORDER BY id")]

    def collection(self, identifier):
        with self.transaction(write=False) as db:
            row = db.execute("SELECT document FROM collections WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise MetadataError("unknown_collection", "Unknown source collection", 404)
        return json.loads(row[0])

    def register_collection(self, profile):
        profile = source_profile(profile)
        with self.transaction() as db:
            current = db.execute("SELECT document FROM collections WHERE id=?", (profile["id"],)).fetchone()
            if current is not None and json.loads(current[0])["dataset"] != profile["dataset"]:
                raise MetadataError(
                    "collection_identity_conflict",
                    "A registered collection cannot change dataset identity",
                    409,
                )
            for row in db.execute("SELECT id,document FROM collections WHERE id!=?", (profile["id"],)):
                if json.loads(row[1])["dataset"].casefold() == profile["dataset"].casefold():
                    raise MetadataError(
                        "collection_identity_conflict",
                        "This HF dataset already has a collection identity",
                        409,
                    )
            db.execute(
                "INSERT INTO collections VALUES(?,?) ON CONFLICT(id) "
                "DO UPDATE SET document=excluded.document",
                (profile["id"], canonical(profile)),
            )
        return profile

    def vocabulary(self, version=None):
        with self.transaction(write=False) as db:
            row = db.execute(
                "SELECT document FROM metadata_state WHERE key=?",
                ("vocabulary" if version is None else "vocabulary:" + version,),
            ).fetchone()
            if row is None and version is not None:
                row = db.execute("SELECT document FROM metadata_state WHERE key='vocabulary'").fetchone()
                if row is not None and json.loads(row[0])["version"] != version:
                    row = None
        if row is None:
            raise MetadataError("unknown_vocabulary", "Unknown vocabulary version", 404)
        return json.loads(row[0])

    def set_vocabulary(self, document, *, base_version=None):
        document = vocabulary_document(document)
        with self.transaction() as db:
            current = json.loads(
                db.execute("SELECT document FROM metadata_state WHERE key='vocabulary'").fetchone()[0]
            )
            if base_version is not None and base_version != current["version"]:
                raise MetadataError("vocabulary_conflict", "Vocabulary changed; reload before editing", 409)
            for value in (current, document):
                key = "vocabulary:" + value["version"]
                row = db.execute("SELECT document FROM metadata_state WHERE key=?", (key,)).fetchone()
                if row is not None and row[0] != canonical(value):
                    raise MetadataError("vocabulary_conflict", "A vocabulary version is immutable", 409)
                db.execute("INSERT OR IGNORE INTO metadata_state VALUES(?,?)", (key, canonical(value)))
            db.execute("UPDATE metadata_state SET document=? WHERE key='vocabulary'", (canonical(document),))
        return deepcopy(document)

    def stage_inventory(
        self, collection_id, source_revision, entries, *, complete, counts=None, evidence=None
    ):
        self.collection(collection_id)
        revision(source_revision)
        if type(complete) is not bool or not isinstance(entries, list) or len(entries) > 100000:
            raise MetadataError(
                "invalid_inventory", "Inventory must be a bounded entry list with explicit completeness"
            )
        normalized = [normalized_entry(collection_id, source_revision, value) for value in entries]
        vocabulary = self.vocabulary()
        for entry in normalized:
            self._validate_category(entry, vocabulary=vocabulary)
        identities = [value["upstreamId"] for value in normalized]
        if len(set(identities)) != len(identities):
            raise MetadataError("duplicate_identity", "An inventory repeats an upstream identity")
        counts = deepcopy(counts or {})
        counts.setdefault("entries", len(normalized))
        if counts["entries"] != len(normalized):
            raise MetadataError(
                "inventory_count_mismatch", "Entry count differs from the actual identity list"
            )
        expected = counts.get("expectedEntries")
        if complete and (not normalized or (expected is not None and expected != len(normalized))):
            raise MetadataError(
                "inventory_incomplete", "Complete inventory must reconcile all expected identities", 409
            )
        normalized.sort(key=lambda value: value["upstreamId"])
        key = digest(
            {
                "collectionId": collection_id,
                "revision": source_revision,
                "complete": complete,
                "entries": normalized,
                "counts": counts,
                "evidence": evidence or [],
            }
        )
        document = {
            "id": key,
            "collectionId": collection_id,
            "revision": source_revision,
            "complete": complete,
            "counts": counts,
            "evidence": evidence or [],
            "createdAt": now(),
        }
        with self.transaction() as db:
            prior = db.execute("SELECT document FROM inventories WHERE id=?", (key,)).fetchone()
            if prior is not None:
                return json.loads(prior[0])
            db.execute(
                "INSERT INTO inventories VALUES(?,?,?,?,?)",
                (key, collection_id, source_revision, int(complete), canonical(document)),
            )
            db.executemany(
                "INSERT INTO inventory_entries VALUES(?,?,?,?)",
                ((key, v["upstreamId"], v["inputDigest"], canonical(v)) for v in normalized),
            )
        return document

    def inventory(self, identifier):
        with self.transaction(write=False) as db:
            row = db.execute("SELECT document FROM inventories WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise MetadataError("unknown_inventory", "Unknown inventory", 404)
        return json.loads(row[0])

    @staticmethod
    def _compose(db, entry):
        result = deepcopy(entry)
        result.setdefault("fieldProvenance", {})
        keys = (entry["collectionId"], entry["upstreamId"], entry["inputDigest"])
        row = db.execute(
            "SELECT version,selection_version FROM organization_heads "
            "WHERE collection_id=? AND upstream_id=? AND input_digest=?",
            keys,
        ).fetchone()
        result["organization"] = {"version": 0, "status": "pending"}
        if row is not None and row[0]:
            data = json.loads(
                db.execute(
                    "SELECT document FROM organizations WHERE collection_id=? AND upstream_id=? "
                    "AND input_digest=? AND version=?",
                    (*keys, row[0]),
                ).fetchone()[0]
            )
            apply_fields(result, data["fields"])
            for field in data["fields"]:
                result["fieldProvenance"][field] = {
                    "kind": "organization",
                    "resultVersion": data["version"],
                    "versions": data["versions"],
                    "receipt": data["receipt"],
                }
            result["organization"] = {key: deepcopy(value) for key, value in data.items() if key != "fields"}
            result["organization"]["resultVersion"] = row[0]
        if row is not None:
            result["organization"]["version"] = row[1]
        correction_key = keys[:2]
        row = db.execute(
            "SELECT version FROM correction_heads WHERE collection_id=? AND upstream_id=?", correction_key
        ).fetchone()
        result["correctionVersion"] = row[0] if row is not None else 0
        result["corrections"] = []
        for row in db.execute(
            "SELECT document FROM corrections WHERE collection_id=? AND upstream_id=? AND active=1 "
            "ORDER BY version",
            correction_key,
        ):
            correction = json.loads(row[0])
            apply_fields(result, correction["fields"])
            for field in correction["fields"]:
                result["fieldProvenance"][field] = {
                    "kind": "manual",
                    "correctionId": correction["id"],
                    "reason": correction["reason"],
                }
            result["corrections"].append(correction)
        return result

    def inventory_entries(self, identifier):
        self.inventory(identifier)
        with self.transaction(write=False) as db:
            return [
                self._compose(db, json.loads(row[0]))
                for row in db.execute(
                    "SELECT document FROM inventory_entries WHERE inventory_id=? ORDER BY upstream_id",
                    (identifier,),
                )
            ]

    def inventory_entry(self, identifier, upstream_id):
        with self.transaction(write=False) as db:
            row = db.execute(
                "SELECT document FROM inventory_entries WHERE inventory_id=? AND upstream_id=?",
                (identifier, upstream_id),
            ).fetchone()
            if row is None:
                raise MetadataError("unknown_entry", "Unknown inventory entry", 404)
            return self._compose(db, json.loads(row[0]))

    def pending(self, identifier, limit=24):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MetadataError("invalid_limit", "Batch limit must be 1..50")
        return [v for v in self.inventory_entries(identifier) if v["organization"]["status"] != "organized"][
            :limit
        ]

    def submit_organization(
        self,
        inventory_id,
        upstream_id,
        result,
        *,
        input_digest,
        base_version=0,
        correction_version=None,
        versions=None,
        receipt=None,
        submission_key=None,
    ):
        fields = organization_fields(result)
        self._validate_category(fields)
        payload = {
            "inventoryId": inventory_id,
            "upstreamId": upstream_id,
            "inputDigest": input_digest,
            "fields": fields,
            "baseVersion": base_version,
            "correctionVersion": correction_version,
            "versions": versions,
            "receipt": receipt,
        }
        payload_digest = digest(payload)
        with self.transaction() as db:
            if submission_key:
                previous = db.execute(
                    "SELECT payload_digest,document FROM submissions WHERE id=?", (submission_key,)
                ).fetchone()
                if previous is not None:
                    if previous[0] != payload_digest:
                        raise MetadataError(
                            "submission_conflict", "Submission key was already used for different data", 409
                        )
                    return json.loads(previous[1])
            row = db.execute(
                "SELECT document FROM inventory_entries WHERE inventory_id=? AND upstream_id=?",
                (inventory_id, upstream_id),
            ).fetchone()
            if row is None:
                raise MetadataError("unknown_entry", "Unknown inventory entry", 404)
            entry = self._compose(db, json.loads(row[0]))
            if input_digest != entry["inputDigest"]:
                raise MetadataError("input_conflict", "Organization input no longer matches", 409)
            if base_version != entry["organization"]["version"]:
                raise MetadataError(
                    "organization_conflict", "Organization changed; reload before submitting", 409
                )
            if correction_version is not None and correction_version != entry["correctionVersion"]:
                raise MetadataError(
                    "correction_conflict", "Manual correction changed; reload before submitting", 409
                )
            keys = (entry["collectionId"], upstream_id, input_digest)
            next_version = db.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM organizations "
                "WHERE collection_id=? AND upstream_id=? AND input_digest=?",
                keys,
            ).fetchone()[0]
            data = {
                "version": next_version,
                "status": "organized",
                "fields": fields,
                "versions": versions or {},
                "receipt": receipt,
                "createdAt": now(),
            }
            db.execute("INSERT INTO organizations VALUES(?,?,?,?,?)", (*keys, next_version, canonical(data)))
            db.execute(
                "INSERT INTO organization_heads VALUES(?,?,?,?,?) "
                "ON CONFLICT(collection_id,upstream_id,input_digest) "
                "DO UPDATE SET version=excluded.version,selection_version=excluded.selection_version",
                (*keys, next_version, entry["organization"]["version"] + 1),
            )
            response = self._compose(db, json.loads(row[0]))
            if submission_key:
                db.execute(
                    "INSERT INTO submissions VALUES(?,?,?)",
                    (submission_key, payload_digest, canonical(response)),
                )
        return response

    def organization_history(self, collection_id, upstream_id, input_digest):
        with self.transaction(write=False) as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT document FROM organizations "
                    "WHERE collection_id=? AND upstream_id=? AND input_digest=? ORDER BY version",
                    (collection_id, upstream_id, input_digest),
                )
            ]

    def select_organization(self, collection_id, upstream_id, input_digest, version, *, base_version=None):
        if type(version) is not int or version < 0:
            raise MetadataError("invalid_version", "Organization selection must be a non-negative version")
        keys = (collection_id, upstream_id, input_digest)
        with self.transaction() as db:
            head = db.execute(
                "SELECT version,selection_version FROM organization_heads WHERE collection_id=? "
                "AND upstream_id=? AND input_digest=?",
                keys,
            ).fetchone()
            current = head[1] if head else 0
            if base_version is not None and base_version != current:
                raise MetadataError(
                    "organization_conflict", "Organization selection changed; reload before selecting", 409
                )
            if (
                version
                and db.execute(
                    "SELECT 1 FROM organizations WHERE collection_id=? AND upstream_id=? "
                    "AND input_digest=? AND version=?",
                    (*keys, version),
                ).fetchone()
                is None
            ):
                raise MetadataError("unknown_organization", "Unknown organization result version", 404)
            selection = current if head is not None and head[0] == version else current + 1
            db.execute(
                "INSERT INTO organization_heads VALUES(?,?,?,?,?) "
                "ON CONFLICT(collection_id,upstream_id,input_digest) "
                "DO UPDATE SET version=excluded.version,selection_version=excluded.selection_version",
                (*keys, version, selection),
            )
        return {
            "collectionId": collection_id,
            "upstreamId": upstream_id,
            "inputDigest": input_digest,
            "version": selection,
            "resultVersion": version,
            "status": "organized" if version else "pending",
        }

    def correct(self, collection_id, upstream_id, fields, *, base_version=None, reason):
        self.collection(collection_id)
        fields = organization_fields(fields)
        self._validate_category(fields)
        text(upstream_id, "upstreamId", 512)
        text(reason, "reason", 2048)
        with self.transaction() as db:
            row = db.execute(
                "SELECT version FROM correction_heads WHERE collection_id=? AND upstream_id=?",
                (collection_id, upstream_id),
            ).fetchone()
            current = row[0] if row else 0
            if base_version is not None and base_version != current:
                raise MetadataError(
                    "correction_conflict", "Manual correction changed; reload before editing", 409
                )
            version = current + 1
            key = digest(
                {
                    "collectionId": collection_id,
                    "upstreamId": upstream_id,
                    "version": version,
                    "fields": fields,
                    "reason": reason,
                }
            )
            value = {
                "id": key,
                "collectionId": collection_id,
                "upstreamId": upstream_id,
                "version": version,
                "fields": fields,
                "reason": reason,
                "createdAt": now(),
            }
            db.execute(
                "INSERT INTO corrections VALUES(?,?,?,?,?,1)",
                (key, collection_id, upstream_id, version, canonical(value)),
            )
            db.execute(
                "INSERT INTO correction_heads VALUES(?,?,?) ON CONFLICT(collection_id,upstream_id) "
                "DO UPDATE SET version=excluded.version",
                (collection_id, upstream_id, version),
            )
        return value

    def _validate_category(self, fields, *, vocabulary=None):
        category = fields.get("category")
        vocabulary = vocabulary or self.vocabulary()
        if category is not None and category not in {value["id"] for value in vocabulary["categories"]}:
            raise MetadataError("unknown_category", "Category is absent from the current vocabulary", 409)

    def corrections(self, collection_id=None, upstream_id=None):
        with self.transaction(write=False) as db:
            rows = db.execute(
                "SELECT document,active FROM corrections ORDER BY collection_id,upstream_id,version"
            )
            return [
                {**json.loads(row[0]), "active": bool(row[1])}
                for row in rows
                if (collection_id is None or json.loads(row[0])["collectionId"] == collection_id)
                and (upstream_id is None or json.loads(row[0])["upstreamId"] == upstream_id)
            ]

    def retract(self, correction_id, *, base_version=None):
        with self.transaction() as db:
            row = db.execute(
                "SELECT document,active FROM corrections WHERE id=?", (correction_id,)
            ).fetchone()
            if row is None:
                raise MetadataError("unknown_correction", "Unknown correction", 404)
            result = json.loads(row[0])
            keys = (result["collectionId"], result["upstreamId"])
            head = db.execute(
                "SELECT version FROM correction_heads WHERE collection_id=? AND upstream_id=?", keys
            ).fetchone()[0]
            if base_version is not None and base_version != head:
                raise MetadataError(
                    "correction_conflict", "Manual correction changed; reload before retracting", 409
                )
            if row[1]:
                db.execute("UPDATE corrections SET active=0 WHERE id=?", (correction_id,))
                db.execute(
                    "UPDATE correction_heads SET version=? WHERE collection_id=? AND upstream_id=?",
                    (head + 1, *keys),
                )
            return {**result, "active": False, "correctionVersion": head + int(bool(row[1]))}

    def publish(self, inventory_ids=None, *, index_builder=None, release_preparer=None):
        with self.transaction() as db:
            selected = {}
            latest = db.execute("SELECT document FROM metadata_state WHERE key='latestRelease'").fetchone()
            if latest is not None:
                release = json.loads(
                    db.execute(
                        "SELECT document FROM releases WHERE id=?", (json.loads(latest[0]),)
                    ).fetchone()[0]
                )
                selected = {v["id"]: v["inventoryId"] for v in release["collections"]}
            if inventory_ids is None:
                rows = db.execute("SELECT id,collection_id FROM inventories WHERE complete=1 ORDER BY rowid")
                selected.update({row[1]: row[0] for row in rows})
            else:
                for key in inventory_ids:
                    row = db.execute(
                        "SELECT collection_id,complete FROM inventories WHERE id=?", (key,)
                    ).fetchone()
                    if row is None:
                        raise MetadataError("unknown_inventory", "Unknown publication inventory", 404)
                    if not row[1]:
                        raise MetadataError(
                            "inventory_incomplete", "Incomplete enumeration cannot replace a collection", 409
                        )
                    selected[row[0]] = key
            if not selected:
                raise MetadataError("no_inventory", "There is no complete inventory to publish", 409)
            entries, collections = [], []
            for collection_id, inventory_id in sorted(selected.items()):
                inv = json.loads(
                    db.execute("SELECT document FROM inventories WHERE id=?", (inventory_id,)).fetchone()[0]
                )
                values = [
                    self._compose(db, json.loads(row[0]))
                    for row in db.execute(
                        "SELECT document FROM inventory_entries WHERE inventory_id=? ORDER BY upstream_id",
                        (inventory_id,),
                    )
                ]
                entries.extend(values)
                collections.append(
                    {
                        "id": collection_id,
                        "revision": inv["revision"],
                        "inventoryId": inventory_id,
                        "coverage": {
                            **inv["counts"],
                            "inventoryComplete": True,
                            "organized": sum(v["organization"]["status"] == "organized" for v in values),
                        },
                    }
                )
            vocabulary = json.loads(
                db.execute("SELECT document FROM metadata_state WHERE key='vocabulary'").fetchone()[0]
            )
            for entry in entries:
                self._validate_category(entry, vocabulary=vocabulary)
                entry["vocabularyVersion"] = vocabulary["version"]
            # Build every index before moving the active pointer. A callback may
            # perform a consumer's index validation; failures roll back activation.
            index = [
                {
                    "id": value["id"],
                    "text": canonical(
                        {
                            key: value.get(key)
                            for key in ("name", "aliases", "summary", "category", "tags", "domains", "uses")
                        }
                    ),
                }
                for value in entries
            ]
            index_version = digest(index)
            if index_builder is not None:
                index_builder(deepcopy(entries))
            content = {
                "schema": SCHEMA,
                "collections": collections,
                "entries": entries,
                "vocabularyVersion": vocabulary["version"],
                "vocabularyDigest": digest(vocabulary),
                "indexVersion": index_version,
            }
            identifier = digest(content)
            prior = db.execute("SELECT document FROM releases WHERE id=?", (identifier,)).fetchone()
            document = (
                json.loads(prior[0])
                if prior
                else {
                    "id": identifier,
                    "schema": SCHEMA,
                    "createdAt": now(),
                    "collections": collections,
                    "vocabularyVersion": vocabulary["version"],
                    "vocabularyDigest": digest(vocabulary),
                    "indexVersion": index_version,
                    "entryCount": len(entries),
                }
            )
            if release_preparer is not None:
                release_preparer(deepcopy(document), deepcopy(entries), vocabulary=deepcopy(vocabulary))
            if prior is None:
                db.execute("INSERT INTO releases VALUES(?,?)", (identifier, canonical(document)))
                db.executemany(
                    "INSERT INTO release_entries VALUES(?,?,?,?)",
                    (
                        (identifier, value["id"], canonical(value), item["text"])
                        for value, item in zip(entries, index, strict=True)
                    ),
                )
            db.execute(
                "INSERT INTO metadata_state VALUES('latestRelease',?) ON CONFLICT(key) "
                "DO UPDATE SET document=excluded.document",
                (canonical(identifier),),
            )
        return document

    def release(self, identifier=None):
        with self.transaction(write=False) as db:
            if identifier is None:
                row = db.execute("SELECT document FROM metadata_state WHERE key='latestRelease'").fetchone()
                identifier = json.loads(row[0]) if row else None
            row = db.execute("SELECT document FROM releases WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise MetadataError(
                "unknown_release", "Catalog release is unavailable; start a fresh query", 409, restart=True
            )
        return json.loads(row[0])

    def latest_release(self):
        try:
            return self.release()
        except MetadataError as exc:
            if exc.code == "unknown_release":
                return None
            raise

    def entries(self, release_id=None):
        release = self.release(release_id)
        with self.transaction(write=False) as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT document FROM release_entries WHERE release_id=? ORDER BY id", (release["id"],)
                )
            ]

    def entry(self, identity, release_id=None):
        with self.transaction(write=False) as db:
            if release_id is None:
                row = db.execute(
                    "SELECT document FROM release_entries WHERE id=? ORDER BY rowid DESC LIMIT 1", (identity,)
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT document FROM release_entries WHERE release_id=? AND id=?", (release_id, identity)
                ).fetchone()
        if row is None:
            raise MetadataError("unknown_entry", "Unknown exact catalog entry", 404)
        return json.loads(row[0])

    def put_blob(self, data, *, kind="metadata", **metadata):
        if not isinstance(data, bytes) or len(data) > 32 * 1024 * 1024:
            raise MetadataError("blob_limit", "A metadata blob must be at most 32 MiB")
        key = "sha256:" + hashlib.sha256(data).hexdigest()
        self.blob_path(key)
        path = (self.blob_root / "previews" if kind == "preview" else self.blob_root) / key[7:]
        if not path.exists():
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            temporary.write_bytes(data)
            temporary.replace(path)
        return {"sha256": key, "kind": kind, "bytes": len(data), **metadata}

    def blob_path(self, key):
        if not isinstance(key, str) or len(key) != 71 or not key.startswith("sha256:"):
            raise MetadataError("invalid_digest", "A SHA-256 evidence digest is required")
        name = key[7:]
        if any(char not in "0123456789abcdef" for char in name):
            raise MetadataError("invalid_digest", "Invalid evidence digest")
        path = self.blob_root / name
        preview_path = self.blob_root / "previews" / name
        return preview_path if not path.exists() and preview_path.exists() else path

    def blob(self, key):
        try:
            data = self.blob_path(key).read_bytes()
        except FileNotFoundError as exc:
            raise MetadataError("evidence_unavailable", "This evidence is not cached locally", 404) from exc
        if "sha256:" + hashlib.sha256(data).hexdigest() != key:
            raise MetadataError("evidence_corrupt", "Cached evidence failed its content digest", 409)
        return data

    def export(self):
        with self.transaction(write=False) as db:
            tables = {table: [dict(row) for row in db.execute("SELECT * FROM " + table)] for table in TABLES}
        blobs, kinds, total = {}, {}, 0
        paths = [*self.blob_root.iterdir(), *(self.blob_root / "previews").iterdir()]
        for path in sorted(paths):
            if path.is_file() and len(path.name) == 64:
                if "sha256:" + path.name in blobs:
                    continue
                data = self.blob("sha256:" + path.name)
                total += len(data)
                if total > 256 * 1024 * 1024:
                    raise MetadataError("export_limit", "Exported evidence exceeds 256 MiB")
                blobs["sha256:" + path.name] = base64.b64encode(data).decode("ascii")
                if path.parent.name == "previews":
                    kinds["sha256:" + path.name] = "preview"
        return {"schema": SCHEMA, "tables": tables, "blobs": blobs, "blobKinds": kinds}

    def restore(self, document, *, release_preparer=None):
        if (
            not isinstance(document, dict)
            or document.get("schema") != SCHEMA
            or set(document.get("tables", {})) != set(TABLES)
        ):
            raise MetadataError("invalid_backup", "Unsupported metadata backup")
        decoded = {}
        for key, value in document.get("blobs", {}).items():
            self.blob_path(key)
            try:
                data = base64.b64decode(value, validate=True)
            except (TypeError, ValueError) as exc:
                raise MetadataError("invalid_backup", "Backup contains invalid evidence") from exc
            if len(data) > 32 * 1024 * 1024 or "sha256:" + hashlib.sha256(data).hexdigest() != key:
                raise MetadataError("invalid_backup", "Backup evidence does not match its digest")
            decoded[key] = data
        if sum(map(len, decoded.values())) > 256 * 1024 * 1024:
            raise MetadataError("export_limit", "Backup evidence exceeds 256 MiB")
        # Unreferenced blobs are harmless if a later transaction fails. Writing
        # first prevents an active release from ever referring to unwritten data.
        for key, data in decoded.items():
            kind = document.get("blobKinds", {}).get(key, "metadata")
            if kind not in ("metadata", "preview"):
                raise MetadataError("invalid_backup", "Backup has an unsupported blob role")
            self.put_blob(data, kind=kind)
            self.blob(key)
        with self.transaction() as db:
            # Restoring replaces only this module's metadata, never local assets,
            # recordings, maintenance jobs or any other host-owned table.
            for table in reversed(TABLES):
                db.execute("DELETE FROM " + table)
            for table in TABLES:
                columns = [row[1] for row in db.execute("PRAGMA table_info(" + table + ")")]
                rows = document["tables"][table]
                if not isinstance(rows, list) or len(rows) > 1000000:
                    raise MetadataError("invalid_backup", "Backup table is invalid or too large")
                for row in rows:
                    if not isinstance(row, dict) or set(row) != set(columns):
                        raise MetadataError("invalid_backup", "Backup row differs from the metadata schema")
                    db.execute(
                        "INSERT INTO " + table + " VALUES(" + ",".join("?" for _ in columns) + ")",
                        tuple(row[column] for column in columns),
                    )
            latest = db.execute("SELECT document FROM metadata_state WHERE key='latestRelease'").fetchone()
            if (
                latest
                and db.execute("SELECT 1 FROM releases WHERE id=?", (json.loads(latest[0]),)).fetchone()
                is None
            ):
                raise MetadataError("invalid_backup", "Backup active release is absent")
            self._validate_restored(db)
            if latest is not None and release_preparer is not None:
                identifier = json.loads(latest[0])
                release = json.loads(
                    db.execute("SELECT document FROM releases WHERE id=?", (identifier,)).fetchone()[0]
                )
                entries = [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT document FROM release_entries WHERE release_id=? ORDER BY id", (identifier,)
                    )
                ]
                row = db.execute(
                    "SELECT document FROM metadata_state WHERE key=?",
                    ("vocabulary:" + release["vocabularyVersion"],),
                ).fetchone()
                vocabulary = (
                    json.loads(row[0])
                    if row is not None
                    else json.loads(
                        db.execute("SELECT document FROM metadata_state WHERE key='vocabulary'").fetchone()[0]
                    )
                )
                release_preparer(deepcopy(release), deepcopy(entries), vocabulary=deepcopy(vocabulary))
        return {"schema": SCHEMA, "restored": True, "release": self.latest_release(), "blobs": len(decoded)}

    def _validate_restored(self, db):
        profiles = [
            source_profile(json.loads(row[0])) for row in db.execute("SELECT document FROM collections")
        ]
        datasets = [value["dataset"].casefold() for value in profiles]
        if len(set(datasets)) != len(datasets):
            raise MetadataError("invalid_backup", "Backup duplicates a dataset identity")
        collection_ids = {value["id"] for value in profiles}
        vocabularies = {}
        for row in db.execute(
            "SELECT key,document FROM metadata_state WHERE key='vocabulary' OR key LIKE 'vocabulary:%'"
        ):
            value = vocabulary_document(json.loads(row[1]))
            if value["version"] in vocabularies and vocabularies[value["version"]] != value:
                raise MetadataError("invalid_backup", "Backup changes an immutable vocabulary version")
            vocabularies[value["version"]] = value
        inventories = {}
        inputs = set()
        for row in db.execute("SELECT * FROM inventories"):
            value = json.loads(row["document"])
            values = [
                json.loads(item[0])
                for item in db.execute(
                    "SELECT document FROM inventory_entries WHERE inventory_id=? ORDER BY upstream_id",
                    (row["id"],),
                )
            ]
            if (
                value.get("id") != row["id"]
                or value.get("collectionId") != row["collection_id"]
                or row["collection_id"] not in collection_ids
                or value.get("revision") != row["revision"]
                or value.get("complete") is not bool(row["complete"])
            ):
                raise MetadataError("invalid_backup", "Backup inventory identity is inconsistent")
            revision(row["revision"])
            normalized = [normalized_entry(row["collection_id"], row["revision"], item) for item in values]
            expected = digest(
                {
                    "collectionId": row["collection_id"],
                    "revision": row["revision"],
                    "complete": bool(row["complete"]),
                    "entries": normalized,
                    "counts": value["counts"],
                    "evidence": value["evidence"],
                }
            )
            if expected != row["id"] or value["counts"]["entries"] != len(values):
                raise MetadataError("invalid_backup", "Backup inventory digest or count is inconsistent")
            inventories[row["id"]] = {item["id"]: item for item in values}
            inputs.update((item["collectionId"], item["upstreamId"], item["inputDigest"]) for item in values)
        for row in db.execute("SELECT * FROM inventory_entries"):
            value = json.loads(row["document"])
            if (
                row["inventory_id"] not in inventories
                or row["upstream_id"] != value["upstreamId"]
                or row["input_digest"] != value["inputDigest"]
            ):
                raise MetadataError("invalid_backup", "Backup inventory lookup keys are inconsistent")
        for row in db.execute("SELECT * FROM organizations"):
            value = json.loads(row["document"])
            organization_fields(value["fields"])
            if (row["collection_id"], row["upstream_id"], row["input_digest"]) not in inputs or value.get(
                "version"
            ) != row["version"]:
                raise MetadataError(
                    "invalid_backup", "Backup organization result has an invalid input/version"
                )
        for row in db.execute("SELECT * FROM organization_heads"):
            keys = (row["collection_id"], row["upstream_id"], row["input_digest"])
            selected = db.execute(
                "SELECT 1 FROM organizations WHERE collection_id=? AND upstream_id=? "
                "AND input_digest=? AND version=?",
                (*keys, row["version"]),
            ).fetchone()
            if (
                keys not in inputs
                or (row["version"] and selected is None)
                or row["selection_version"] < row["version"]
            ):
                raise MetadataError("invalid_backup", "Backup organization selection is inconsistent")
        for row in db.execute("SELECT * FROM corrections"):
            value = json.loads(row["document"])
            organization_fields(value["fields"])
            key = digest(
                {
                    field: value[field]
                    for field in ("collectionId", "upstreamId", "version", "fields", "reason")
                }
            )
            if (
                value.get("id") != row["id"]
                or key != row["id"]
                or value["collectionId"] != row["collection_id"]
                or value["upstreamId"] != row["upstream_id"]
                or value["version"] != row["version"]
                or row["collection_id"] not in collection_ids
                or row["active"] not in (0, 1)
            ):
                raise MetadataError("invalid_backup", "Backup manual correction is inconsistent")
        for row in db.execute("SELECT id,document FROM releases"):
            value = json.loads(row[1])
            entries = [
                json.loads(item[0])
                for item in db.execute("SELECT document FROM release_entries WHERE release_id=?", (row[0],))
            ]
            entries.sort(key=lambda item: (item["collectionId"], item["upstreamId"]))
            if value.get("id") != row[0] or value.get("entryCount") != len(entries):
                raise MetadataError("invalid_backup", "Backup release identity or count is inconsistent")
            vocabulary = vocabularies.get(value.get("vocabularyVersion"))
            if vocabulary is None or digest(vocabulary) != value.get("vocabularyDigest"):
                raise MetadataError(
                    "invalid_backup", "Backup release vocabulary differs from its pinned digest"
                )
            for entry in entries:
                self._validate_category(entry, vocabulary=vocabulary)
                if entry.get("vocabularyVersion") != vocabulary["version"]:
                    raise MetadataError("invalid_backup", "Backup entry has a different vocabulary version")
            expected_entries = {}
            for collection in value["collections"]:
                inventory_id = collection["inventoryId"]
                inv = db.execute(
                    "SELECT collection_id,revision,complete FROM inventories WHERE id=?", (inventory_id,)
                ).fetchone()
                if (
                    inv is None
                    or not inv[2]
                    or inv[0] != collection["id"]
                    or inv[1] != collection["revision"]
                ):
                    raise MetadataError("invalid_backup", "Backup release has an invalid inventory reference")
                expected_entries.update(inventories[inventory_id])
                if collection["coverage"]["entries"] != len(inventories[inventory_id]):
                    raise MetadataError("invalid_backup", "Backup release coverage is inconsistent")
            if set(expected_entries) != {entry["id"] for entry in entries}:
                raise MetadataError("invalid_backup", "Backup release inventory identities are inconsistent")
            if any(entry["inputDigest"] != expected_entries[entry["id"]]["inputDigest"] for entry in entries):
                raise MetadataError("invalid_backup", "Backup release changed a source input identity")
            index = [
                {
                    "id": entry["id"],
                    "text": canonical(
                        {
                            key: entry.get(key)
                            for key in ("name", "aliases", "summary", "category", "tags", "domains", "uses")
                        }
                    ),
                }
                for entry in entries
            ]
            content = {
                "schema": SCHEMA,
                "collections": value["collections"],
                "entries": entries,
                "vocabularyVersion": value["vocabularyVersion"],
                "vocabularyDigest": value["vocabularyDigest"],
                "indexVersion": digest(index),
            }
            if value["indexVersion"] != content["indexVersion"] or row[0] != digest(content):
                raise MetadataError("invalid_backup", "Backup release or index digest is inconsistent")
            actual_index = {
                item[0]: item[1]
                for item in db.execute(
                    "SELECT id,search_text FROM release_entries WHERE release_id=?", (row[0],)
                )
            }
            if actual_index != {item["id"]: item["text"] for item in index}:
                raise MetadataError("invalid_backup", "Backup index differs from its release")

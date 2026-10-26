"""Adapt released metadata into the established Lab catalog consumption model.

The metadata core knows no Lab admission rules. This consumer preserves reviewed
admission only for an identical legacy source identity, and retains immutable
query views so an in-progress page chain cannot observe a new publication.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from collections import OrderedDict
from dataclasses import asdict, replace
from datetime import UTC, datetime
from urllib.parse import urlencode

from packages.catalog.models import (
    DEFAULT_LIMITS,
    Acquisition,
    Adaptation,
    CatalogEntry,
    CatalogSnapshot,
    License,
    LicenseAttachment,
    LicenseEvidence,
    Source,
    UpstreamValidation,
    UsageFact,
)
from packages.catalog.search import _decode_cursor
from packages.catalog.semantic import build_semantic_index
from packages.catalog.views import canonical_json
from packages.library.common import LibraryError

LOGGER = logging.getLogger(__name__)


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


def adapt_entry(document, legacy=None, *, release_id=None, vocabulary=None):
    """Facts stay source facts; organization never grants Lab eligibility."""
    document = copy.deepcopy(document)
    provider = document["collectionId"]
    identity = document["id"]
    package = identity[len(provider) + 1:].rsplit("@", 1)[0]
    facts = document.get("facts", {})
    aliases = _pair(document.get("aliases"), [])
    names = _pair(document.get("name"), document.get("upstreamId", package))
    descriptions = _pair(document.get("summary"))
    if vocabulary:
        category = document.get("category")
        terms = [term for term in vocabulary.get("categories", []) if term.get("id") == category]
        tag_ids = set(document.get("tags", []))
        terms.extend(term for term in vocabulary.get("tags", []) if term.get("id") in tag_ids)
        for term in terms:
            for language in ("en", "zh"):
                aliases[language] = [*aliases[language], *term.get("aliases", {}).get(language, [])]
                label = term.get("name", {}).get(language)
                if label:
                    aliases[language].append(label)
    document["release"] = release_id
    preview = document.get("preview", {})
    state = preview.get("state", "not_collected")
    path = preview.get("path")
    preview_document = {
        "kind": "on-demand" if path and state not in ("missing", "suspect") else "unavailable",
        "state": state,
        "role": preview.get("role"),
        "url": "/api/v1/library/catalog/maintenance/preview?" + urlencode({
            "entry": identity, "release": release_id,
        }) if path and state not in ("missing", "suspect") else None,
        "reason": preview.get("reason") or {
            "not_collected": "Object preview has not been read; it loads when visible.",
            "missing": "No object preview is supplied by this source.",
            "failed": "The object preview could not be read; retry explicitly.",
            "suspect": "The selected image needs review before it can represent this object.",
        }.get(state),
    }
    if legacy is not None:
        return replace(
            legacy, names=names, aliases={key: tuple(value) for key, value in aliases.items()},
            descriptions=descriptions, tags=_tags(document), metadata=document,
            preview=preview_document,
        )
    license_fact = facts.get("license")
    license_name = license_fact.get("name") if isinstance(license_fact, dict) else license_fact
    dimensions = facts.get("dimensionsM")
    return CatalogEntry(
        provider=provider, revision=document["revision"], package_id=package,
        local_asset_id=None, kind="unknown",
        source=Source(document.get("source", {}).get("url") or "https://huggingface.co/datasets/"
                      + document.get("source", {}).get("dataset", ""),
                      facts.get("fileCount"), facts.get("sourceBytes"), None, None, ()),
        names=names, aliases={key: tuple(value) for key, value in aliases.items()},
        descriptions=descriptions, tags=_tags(document),
        license=License(license_name, False, (), None, LicenseEvidence(None, None), (), True),
        usage={name: UsageFact(None, True, "Metadata maintenance does not establish physical use.")
               for name in ("dressing", "staticCollision")},
        adaptation=Adaptation("gap", ("No Lab asset adaptation is verified.",),
                              tuple(dimensions) if dimensions is not None else None,
                              None, None, "unknown", ()),
        upstream_validation=UpstreamValidation("unknown", "unknown", "unknown", (), "publisher"),
        acquisition=Acquisition(False, ("Source discovery does not grant acquisition admission.",)),
        preview=preview_document, metadata=document,
    )


def _restore_entry(value):
    value = dict(value)
    value["source"] = Source(**value["source"])
    license_ = dict(value["license"])
    license_["evidence"] = LicenseEvidence(**license_["evidence"])
    license_["attachments"] = tuple(LicenseAttachment(**item) for item in license_["attachments"])
    value["license"] = License(**license_)
    value["usage"] = {name: UsageFact(**fact) for name, fact in value["usage"].items()}
    value["adaptation"] = Adaptation(**value["adaptation"])
    value["upstream_validation"] = UpstreamValidation(**value["upstream_validation"])
    value["acquisition"] = Acquisition(**value["acquisition"])
    return CatalogEntry(**value)


class ReleasedViews:
    """Immutable read models cached locally, independently of mutable work."""

    def __init__(self, catalog, store):
        self.catalog = catalog
        self.store = store
        self.root = store.root / "query-views"
        self.root.mkdir(parents=True, exist_ok=True)
        self._views = OrderedDict()
        self._active = None

    def _remember(self, value):
        identifier = value.snapshot.id
        self._views[identifier] = value
        self._views.move_to_end(identifier)
        while len(self._views) > 4:
            self._views.popitem(last=False)
        return value

    def select(self, release=None, cursor=None):
        pinned = _decode_cursor(cursor)["snapshot"] if cursor else None
        if release and pinned and release != pinned:
            raise LibraryError("cursor_mismatch", "Cursor belongs to a different released view", 400)
        identifier = release or pinned
        if identifier:
            if identifier in self._views:
                return self._views[identifier]
            return self._read(identifier)
        return self._current()

    def _clone(self, snapshot, entries):
        # A clone has no maintenance hook, so all established search/detail
        # behavior operates against this single immutable version.
        from packages.catalog.catalog import AssetCatalog
        value = AssetCatalog(self.catalog.path, embedder=self.catalog.embedder)
        value.available = True
        value.snapshot = snapshot
        value.entries = tuple(entries)
        value.by_id = {entry.id: entry for entry in entries}
        counts = {}
        for entry in entries:
            counts[entry.package_id] = counts.get(entry.package_id, 0) + 1
        value.by_package = {entry.package_id: entry for entry in entries if counts[entry.package_id] == 1}
        value._historical = {**self.catalog._historical, **self.catalog.by_id}
        value.archive_paths = self.catalog.archive_paths
        value.resources = self.catalog.resources
        value.metadata_store = self.store
        value.semantic = build_semantic_index(entries, snapshot.id, embedder=value.embedder)
        return value

    def _current(self):
        release = self.store.latest_release()
        if release is None:
            identifier = self.catalog.snapshot.id if self.catalog.snapshot else None
        else:
            identifier = "sha256:" + hashlib.sha256(canonical_json({
                "metadata": release["id"],
                "legacy": self.catalog.snapshot.id if self.catalog.snapshot else None,
            }).encode()).hexdigest()
        if identifier in self._views:
            value = self._views[identifier]
        else:
            try:
                value = self._build(release, identifier)
            except Exception as exc:
                LOGGER.warning("New catalog view is not ready; retaining active view: %s", exc)
                previous = self._active
                if previous is None:
                    try:
                        active = json.loads((self.root / "active.json").read_text(encoding="utf-8"))
                        previous = self._read(active["id"])
                    except (OSError, ValueError, KeyError, LibraryError):
                        pass
                if previous is None:
                    raise
                previous.publication_error = (
                    "A new catalog view could not be prepared; this search uses the previous release."
                )
                return previous
        if value.snapshot is not None:
            pointer = self.root / "active.json"
            temporary = pointer.with_suffix(".tmp")
            try:
                temporary.write_text(canonical_json({"id": value.snapshot.id}), encoding="utf-8")
                temporary.replace(pointer)
            except OSError:
                # The immutable view itself was durably prepared before its
                # core release activated. The prior active pointer remains.
                LOGGER.warning("Could not refresh the active catalog cache pointer")
            self._active = value
        return value

    def prepare(self, release, documents):
        identifier = "sha256:" + hashlib.sha256(canonical_json({
            "metadata": release["id"],
            "legacy": self.catalog.snapshot.id if self.catalog.snapshot else None,
        }).encode()).hexdigest()
        return self._build(release, identifier, documents)

    def _build(self, release, identifier, documents=None):
        entries = self.catalog.entries
        snapshot = self.catalog.snapshot
        if release is not None:
            documents = self.store.entries(release["id"]) if documents is None else documents
            providers = {item["collectionId"] for item in documents}
            vocabulary = self.store.vocabulary(release.get("vocabularyVersion"))
            entries = [entry for entry in entries if entry.provider not in providers]
            entries.extend(adapt_entry(document, self.catalog.by_id.get(document["id"]),
                                       release_id=release["id"], vocabulary=vocabulary)
                           for document in documents)
            created = release["createdAt"]
            timestamp = datetime.fromisoformat(created).astimezone(UTC)
            base = snapshot or CatalogSnapshot(identifier, "multiple", "multiple", "0" * 40,
                                               timestamp, created, "", 0, dict(DEFAULT_LIMITS))
            snapshot = replace(base, id=identifier, entry_count=len(entries),
                               indexed_at=timestamp, indexed_at_text=created,
                               coverage="Complete inventories; organization coverage is reported per source.",
                               metadata_release=release)
        if snapshot is None:
            return self.catalog
        value = self._clone(snapshot, entries)
        self._persist(value)
        return self._remember(value)

    def _persist(self, value):
        snapshot = asdict(value.snapshot)
        snapshot["indexed_at"] = value.snapshot.indexed_at.isoformat()
        document = {"schema": "lw.catalog-query-view/1", "snapshot": snapshot,
                    "entries": [asdict(entry) for entry in value.entries]}
        raw = canonical_json(document).encode()
        wrapper = {"sha256": hashlib.sha256(raw).hexdigest(), "document": document}
        path = self.root / (value.snapshot.id.removeprefix("sha256:") + ".json")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(canonical_json(wrapper), encoding="utf-8")
        temporary.replace(path)

    def _read(self, identifier):
        import re
        if not isinstance(identifier, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", identifier):
            raise LibraryError("release_expired", "Released view is unavailable; "
                               "repeat the search without release", 409)
        path = self.root / (identifier.removeprefix("sha256:") + ".json")
        try:
            if path.stat().st_size > 256 * 1024 * 1024:
                raise ValueError("Released query view exceeds its bound")
            wrapper = json.loads(path.read_text(encoding="utf-8"))
            document = wrapper["document"]
            digest = hashlib.sha256(canonical_json(document).encode()).hexdigest()
            if wrapper["sha256"] != digest or document["schema"] != "lw.catalog-query-view/1":
                raise ValueError("Released query view checksum does not match")
            snapshot = dict(document["snapshot"])
            if snapshot["id"] != identifier or len(document["entries"]) > 100000:
                raise ValueError("Released query view identity is invalid")
            snapshot["indexed_at"] = datetime.fromisoformat(snapshot["indexed_at"])
            value = self._clone(CatalogSnapshot(**snapshot),
                                [_restore_entry(item) for item in document["entries"]])
        except (OSError, ValueError, KeyError, TypeError):
            raise LibraryError("release_expired", "Released view is unavailable; "
                               "repeat the search without release", 409)
        return self._remember(value)

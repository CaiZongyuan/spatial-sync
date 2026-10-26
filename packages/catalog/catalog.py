"""The gateway-owned catalog snapshot, lookup and local provenance correlation."""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from urllib.parse import quote

from packages.catalog.local import LocalAsset, read_provenance
from packages.catalog.models import REVISION, CatalogFormatError, CatalogSnapshot, load_catalog
from packages.catalog.search import search_catalog
from packages.catalog.semantic import SemanticIndex, build_semantic_index
from packages.catalog.views import entry_document, estimate_document
from packages.library.common import LibraryError

LOGGER = logging.getLogger(__name__)


class AssetCatalog:
    """One immutable catalog snapshot loaded at gateway startup.

    A missing or invalid file leaves the catalog unavailable rather than failing
    startup: local-only library journeys keep working.
    """

    def __init__(self, path=None, *, embedder=None):
        self.path = Path(path) if path is not None else None
        self.available = False
        self.error = None
        self.snapshot: CatalogSnapshot | None = None
        self.entries = ()
        self.by_id = {}
        self.by_package = {}
        self.embedder = embedder
        self.semantic: SemanticIndex | None = None
        self._lock = threading.RLock()
        self.archive_paths: tuple[Path, ...] = ()
        self._historical = {}
        self.resources = None
        self.metadata_store = None
        self._metadata_views = None

    def attach_metadata(self, store):
        """Keep maintenance storage independent; adapt it only for consumption."""
        from packages.catalog.maintained import ReleasedViews
        with self._lock:
            self.metadata_store = store
            self._metadata_views = ReleasedViews(self, store)

    def validate_metadata(self, documents):
        from packages.catalog.maintained import adapt_entry
        entries = [adapt_entry(document, self.by_id.get(document["id"])) for document in documents]
        build_semantic_index(entries, "metadata-validation", embedder=self.embedder)

    def prepare_metadata_release(self, release, documents):
        """Build and persist the real consuming index before core activation."""
        if self._metadata_views is not None:
            with self._lock:
                return self._metadata_views.prepare(release, documents)

    def startup(self):
        try:
            if self.path is None:
                raise CatalogFormatError("No catalog path is configured")
            snapshot, entries = load_catalog(self.path)
        except (OSError, ValueError) as exc:
            self.available = False
            self.error = f"Catalog is unavailable: {exc}"
            self.snapshot = None
            self.entries = ()
            self.by_id = {}
            self.by_package = {}
            self.semantic = None
            LOGGER.warning("Asset catalog unavailable: %s", exc)
            return
        self.available = True
        self.error = None
        self.snapshot = snapshot
        self.entries = entries
        self.by_id = {entry.id: entry for entry in entries}
        # Legacy package-only consumers may use only unambiguous package names.
        counts = {}
        for entry in entries:
            counts[entry.package_id] = counts.get(entry.package_id, 0) + 1
        self.by_package = {entry.package_id: entry for entry in entries if counts[entry.package_id] == 1}
        # The index is built from this snapshot only; the stand-in needs no
        # network, cache or model weights. A configured real embedder that
        # cannot load falls back to the stand-in and keeps that fact visible.
        self.semantic = build_semantic_index(entries, snapshot.id, embedder=self.embedder)

    def activate(self, candidate, *, archive_paths=()):
        """Publish a fully built index without exposing a mixture of snapshots."""
        with self._lock:
            if self.snapshot and self.snapshot.sync is None:
                self._historical.update(self.by_id)
            for name in ("available", "error", "snapshot", "entries", "by_id", "by_package", "semantic"):
                setattr(self, name, getattr(candidate, name))
            self.archive_paths = tuple(archive_paths)

    def require(self):
        if not self.available or self.snapshot is None:
            raise LibraryError(
                "catalog_unavailable",
                self.error or "The external asset catalog is not available",
                503,
            )
        return self.snapshot

    def entry(self, provider, package, revision):
        with self._lock:
            self.require()
            identity = f"{provider}/{package}@{revision}"
            entry = self.by_id.get(identity) or self._historical.get(identity)
            # ASGI transports can decode the package path before routing. Use
            # its canonical encoded form only after exact lookup, preserving a
            # literal '%' in an upstream ID as a distinct identity.
            encoded_identity = f"{provider}/{quote(package, safe='._-')}@{revision}"
            if entry is None:
                entry = self.by_id.get(encoded_identity) or self._historical.get(encoded_identity)
            if (entry is None or entry.discovery is None) and REVISION.fullmatch(revision):
                for path in reversed(self.archive_paths):
                    _, entries = load_catalog(path)
                    archived = next((item for item in entries if item.id == identity), None)
                    if archived is not None:
                        entry = archived
                        self._historical[identity] = archived
                        break
            if entry is not None:
                return entry
            if self.metadata_store is not None:
                from packages.catalog.maintained import adapt_entry
                try:
                    document = self.metadata_store.entry(identity)
                except (KeyError, ValueError):
                    try:
                        document = self.metadata_store.entry(encoded_identity)
                    except (KeyError, ValueError):
                        document = None
                if document is not None:
                    return adapt_entry(document)
            raise LibraryError(
                "unknown_catalog_entry",
                "Unknown catalog entry identity",
                404,
                provider=provider,
                package=package,
                revision=revision,
            )

    def local_assets(self, library):
        """Correlate only through provenance source revision and package ID."""
        snapshot = self.snapshot if self.available else None
        values = getattr(library, "versions", {}) if library is not None else {}
        result = []
        for version in sorted(values.values(), key=lambda item: (item.bare_id, item.rev)):
            provenance = read_provenance(version)
            entry = None
            if snapshot is not None and provenance is not None:
                package = provenance.get("sourcePackage")
                revision = provenance.get("sourceRevision")
                provider = provenance.get("sourceProvider", "simready")
                candidate = self.by_package.get(package) if isinstance(package, str) else None
                if isinstance(package, str) and isinstance(revision, str) and (
                    candidate is None or candidate.revision != revision or candidate.provider != provider
                ):
                    try:
                        # Historical provenance did not carry a provider. Never
                        # guess across sources; the legacy source is SimReady.
                        candidate = self.entry(provider, package, revision)
                    except LibraryError:
                        candidate = None
                if (
                    candidate is not None
                    and candidate.revision == revision
                    and candidate.provider == provenance.get("sourceProvider", "simready")
                ):
                    entry = candidate
            result.append(LocalAsset(version, provenance, entry))
        return result

    def search(self, library, **kwargs):
        with self._lock:
            release = kwargs.pop("release", None)
            selected = self
            if self._metadata_views is not None:
                selected = self._metadata_views.select(release, kwargs.get("cursor"))
            elif release and (self.snapshot is None or release != self.snapshot.id):
                raise LibraryError("release_expired", "Released view is unavailable; "
                                   "repeat the search without release", 409)
            return search_catalog(selected, library, **kwargs)

    def describe(self, provider, package, revision, *, library=None, view="summary",
                 document_digest=None, release=None):
        if self._metadata_views is not None:
            with self._lock:
                selected = self._metadata_views.select(release)
            return selected.describe(provider, package, revision, library=library, view=view,
                                     document_digest=document_digest)
        entry = self.entry(provider, package, revision)
        if self.resources is not None and entry.metadata is None:
            entry = self.resources.details(entry)
        with self._lock:
            local = [
                item for item in self.local_assets(library)
                if item.entry is not None and item.entry.id == entry.id
            ]
            return entry_document(self, entry, local, view=view, document_digest=document_digest)

    def estimate(self, provider, package, revision, *, library=None, release=None):
        if self._metadata_views is not None:
            with self._lock:
                selected = self._metadata_views.select(release)
            return selected.estimate(provider, package, revision, library=library)
        entry = self.entry(provider, package, revision)
        if self.resources is not None and entry.metadata is None:
            entry = self.resources.details(entry)
        with self._lock:
            local = [
                item for item in self.local_assets(library)
                if item.entry is not None and item.entry.id == entry.id
            ]
            return estimate_document(self, entry, local)

"""The gateway-owned catalog snapshot, lookup and local provenance correlation."""

from __future__ import annotations

import logging
from pathlib import Path

from packages.catalog.local import LocalAsset, read_provenance
from packages.catalog.models import CatalogFormatError, CatalogSnapshot, load_catalog
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
        self.by_package = {entry.package_id: entry for entry in entries}
        # The index is built from this snapshot only; the stand-in needs no
        # network, cache or model weights. A configured real embedder that
        # cannot load falls back to the stand-in and keeps that fact visible.
        self.semantic = build_semantic_index(entries, snapshot.id, embedder=self.embedder)

    def require(self):
        if not self.available or self.snapshot is None:
            raise LibraryError(
                "catalog_unavailable",
                self.error or "The external asset catalog is not available",
                503,
            )
        return self.snapshot

    def entry(self, provider, package, revision):
        self.require()
        entry = self.by_package.get(package)
        if entry is None or entry.provider != provider or entry.revision != revision:
            raise LibraryError(
                "unknown_catalog_entry",
                "Unknown catalog entry identity",
                404,
                provider=provider,
                package=package,
                revision=revision,
            )
        return entry

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
                candidate = self.by_package.get(package) if isinstance(package, str) else None
                if (
                    candidate is not None
                    and candidate.revision == revision == snapshot.revision
                ):
                    entry = candidate
            result.append(LocalAsset(version, provenance, entry))
        return result

    def search(self, library, **kwargs):
        return search_catalog(self, library, **kwargs)

    def describe(self, provider, package, revision, *, library=None, view="summary",
                 document_digest=None):
        entry = self.entry(provider, package, revision)
        local = [
            item for item in self.local_assets(library)
            if item.entry is not None and item.entry.id == entry.id
        ]
        return entry_document(self, entry, local, view=view, document_digest=document_digest)

    def estimate(self, provider, package, revision, *, library=None):
        entry = self.entry(provider, package, revision)
        local = [
            item for item in self.local_assets(library)
            if item.entry is not None and item.entry.id == entry.id
        ]
        return estimate_document(self, entry, local)

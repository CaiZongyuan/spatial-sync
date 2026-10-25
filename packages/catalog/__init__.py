"""External asset catalog: loading, unified search, describe and estimate."""

from packages.catalog.catalog import AssetCatalog
from packages.catalog.models import (
    CATALOG_SCHEMA,
    ENTRY_SCHEMA,
    ESTIMATE_SCHEMA,
    SEARCH_SCHEMA,
    CatalogEntry,
    CatalogFormatError,
    CatalogSnapshot,
    load_catalog,
)
from packages.catalog.semantic import (
    SEMANTIC_SCHEMA,
    HashEmbedder,
    SemanticIndex,
    build_semantic_index,
    load_embedder,
)

__all__ = [
    "AssetCatalog",
    "CATALOG_SCHEMA",
    "ENTRY_SCHEMA",
    "ESTIMATE_SCHEMA",
    "SEARCH_SCHEMA",
    "SEMANTIC_SCHEMA",
    "CatalogEntry",
    "CatalogFormatError",
    "CatalogSnapshot",
    "HashEmbedder",
    "SemanticIndex",
    "build_semantic_index",
    "load_catalog",
    "load_embedder",
]

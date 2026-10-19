"""Real Synthetic browser fixture: declare one action high-risk before host startup."""

from apps.api.app.main import app
from apps.api.app.sources import synthetic_source
from packages.info.catalog import load_catalog

__all__ = ["app"]


def approval_catalog(manifest, scene_id, registry, **options):
    catalog = load_catalog(manifest, scene_id, registry, **options)
    catalog.types["centrifuge/v1"]["actions"]["closeLid"]["risk"] = "high"
    return catalog


synthetic_source.load_catalog = approval_catalog

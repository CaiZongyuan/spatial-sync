"""Production sync/HTTP with a bounded deterministic upstream for CLI and UI."""

import os
from pathlib import Path

from fastapi import Request

from apps.api.app.main import create_app
from tests.fixtures.catalog_source import CatalogSourceFixture

ROOT = Path(__file__).resolve().parents[2]
os.environ.pop("SPATIAL_CATALOG_METADATA_ROOT", None)
for variable, directory in (
    ("LW_LIBRARY", "library"), ("SPATIAL_STATE_ROOT", "state"),
    ("SPATIAL_RECORDINGS_ROOT", "recordings"),
):
    os.environ.setdefault(variable, str(ROOT / ".scratch/catalog-sync-e2e" / directory))

source = CatalogSourceFixture()
source.delay = .1
app = create_app(catalog_sync_source=source.source)


@app.post("/e2e/catalog-source")
async def control(request: Request):
    body = await request.json()
    source.delay = min(3, max(0, float(body.get("delaySeconds", 0))))
    source.fail_listing = bool(body.get("failListing", False))
    if "count" in body:
        source.names = [f"fixture_{index:03d}" for index in range(min(100, max(1, int(body["count"]))))]
    if body.get("clearRequests"):
        source.requests.clear()
    return {"delaySeconds": source.delay, "failListing": source.fail_listing}


@app.get("/e2e/catalog-source")
async def requests():
    return {"requests": list(source.requests)}

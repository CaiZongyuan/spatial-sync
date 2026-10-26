"""Synthetic browser inventory using the real SQLite release and query paths."""

import base64
import os
from contextlib import asynccontextmanager
from pathlib import Path

from approval_gateway import app

from packages.catalog_metadata import MetadataStore
from packages.catalog_metadata.recipes import preview_choice

PREFIX = "/api/v1/library/catalog/maintenance"
ROOT = Path(os.environ["SPATIAL_STATE_ROOT"]) / "catalog-metadata"
store = MetadataStore(ROOT)
REVISION = "d" * 40
IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6f1sAAAAASUVORK5CYII="
)
blob = store.put_blob(IMAGE, kind="preview")
evidence_blob = store.put_blob(b'{"fixture":true,"publisherName":"Fixture beaker"}')
choice = preview_choice([
    {"path": "normal.png", "size": 1, "role": "normal"},
    {"path": "previews/object.png", "size": len(IMAGE), "role": "object"},
])
assert choice["path"] == "previews/object.png"
documents = [{
    "upstreamId": f"scenario:asset_{index:03d}",
    "name": {"en": f"Fixture beaker {index:03d}", "zh": f"夹具烧杯 {index:03d}"},
    "summary": {"en": "Synthetic browser fixture for sample containers.",
                "zh": "合成浏览器夹具；用于盛放样品的容器。"},
    "aliases": {"en": ["sample holder"], "zh": ["样品容器"]},
    "category": "container", "tags": ["sample", "实验"],
    "facts": {"fileCount": 2, "sourceBytes": None, "dimensionsM": None},
    "source": {"dataset": "fixture/metadata", "url": "https://huggingface.co/datasets/fixture/metadata"},
    "preview": {"state": "available", "role": "object", "path": choice["path"],
                "sha256": blob["sha256"], "dataset": "fixture/metadata", "revision": REVISION},
    "evidence": [{"id": evidence_blob["sha256"], "sha256": evidence_blob["sha256"],
                  "kind": "publisher", "path": "metadata.json", "fields": ["name"]}],
} for index in range(35)]
inventory = store.stage_inventory("scenario", REVISION, documents, complete=True)
store.publish([inventory["id"]])
previous_lifespan = app.router.lifespan_context


@asynccontextmanager
async def metadata_lifespan(application):
    async with previous_lifespan(application):
        application.state.catalog.attach_metadata(store)
        yield


app.router.lifespan_context = metadata_lifespan
requests = {"estimates": 0, "previews": [], "normalMapSelected": False}


@app.middleware("http")
async def observe(request, call_next):
    if request.url.path.endswith("/estimate"):
        requests["estimates"] += 1
    if request.url.path == PREFIX + "/preview":
        requests["previews"].append(request.query_params.get("entry"))
    return await call_next(request)


@app.get("/e2e/metadata-catalog")
async def catalog_state():
    return {"requests": requests, "release": store.latest_release(), "selectedPreview": choice}


@app.post("/e2e/metadata-catalog/publish")
async def publish():
    changed = [{**value, "name": {"en": "New publication " + str(index), "zh": "新发布 " + str(index)}}
               for index, value in enumerate(documents)]
    inventory = store.stage_inventory("scenario", REVISION, changed, complete=True)
    return store.publish([inventory["id"]], release_preparer=app.state.catalog.prepare_metadata_release)

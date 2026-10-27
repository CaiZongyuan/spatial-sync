"""The same source-native workflow works without constructing a Lab gateway."""

import base64
import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from packages.catalog_metadata.service import create_app

BASE = "/api/v1/library/catalog/maintenance"
REVISION = "a" * 40


def image_bytes():
    output = BytesIO()
    Image.new("RGBA", (900, 600), (50, 140, 200, 128)).save(output, format="PNG")
    return output.getvalue()


def stage(client, store, *, previews=True, budgets=None, count=2):
    assert client.patch(BASE + "/collections/simready", json={"enabled": True}).status_code == 200
    response = client.post(
        BASE + "/runs",
        json={
            "collectionId": "simready",
            "revision": REVISION,
            "organizer": "external",
            "previews": previews,
            "budgets": budgets,
        },
    )
    assert response.status_code == 202, response.text
    run = response.json()
    evidence = store.put_blob(b'{"purpose":"laboratory storage"}')
    entries = [
        {
            "upstreamId": "bottle_" + str(index),
            "name": {"en": "Bottle " + str(index), "zh": ""},
            "facts": {"dimensionsM": None, "license": None},
            "sourceMetadata": {"publisherDescription": "Storage bottle", "long": "多语言资料" * 9000},
            "evidence": [evidence | {"path": "metadata.json"}],
            "preview": {
                "role": "object",
                "path": "preview.png",
                "state": "not_collected",
                "sha256": "sha256:" + hashlib.sha256(image_bytes()).hexdigest(),
            }
            if index == 0
            else {"role": None, "path": None, "state": "missing"},
        }
        for index in range(count)
    ]
    path = BASE + "/runs/" + run["id"]
    response = client.post(path + "/inventory", json={"entries": entries, "offset": 0})
    assert response.status_code == 200, response.text
    response = client.post(
        path + "/inventory/complete",
        json={
            "revision": REVISION,
            "complete": True,
            "counts": {"entries": count},
            "usage": {"requests": 0, "networkBytes": 0, "previewBytes": 0},
        },
    )
    assert response.status_code == 200, response.text
    return response.json(), path


def organize(client, path):
    batch = client.post(path + "/claim", json={}).json()
    assert {term["id"] for term in batch["vocabulary"]["categories"]} >= {"container"}
    assert batch["evidence"][0]["text"] == '{"purpose":"laboratory storage"}'
    response = client.post(
        path + "/batches/" + batch["id"] + "/submit",
        json={
            "inputKey": batch["inputKey"],
            "nativeSessionId": "fixture-external-agent",
            "usage": {"inputTokens": 7, "outputTokens": 3, "totalTokens": 10},
            "results": [
                {
                    "upstreamId": entry["upstreamId"],
                    "inputDigest": entry["inputDigest"],
                    "fields": {
                        "name": {"zh": "试剂瓶 " + str(index)},
                        "summary": {"zh": "用于实验台容器布局的来源候选"},
                        "category": "container",
                        "tags": ["试剂瓶"],
                        "uses": ["实验台布局"],
                    },
                }
                for index, entry in enumerate(batch["entries"])
            ],
        },
    )
    assert response.status_code == 200, response.text
    return batch


def previews(client, path):
    batch = client.post(path + "/previews/claim", json={}).json()
    for entry in batch["entries"]:
        payload = {
            "upstreamId": entry["upstreamId"],
            "inputDigest": entry["inputDigest"],
            "state": "available" if entry["preview"]["role"] == "object" else "missing",
        }
        if payload["state"] == "available":
            payload["data"] = base64.b64encode(image_bytes()).decode("ascii")
        response = client.post(path + "/previews/submit", json=payload)
        assert response.status_code == 200, response.text
        assert client.post(path + "/previews/submit", json=payload).json() == response.json()


def test_external_workflow_local_webp_progressive_reads_and_offline_restore(tmp_path, monkeypatch):
    app = create_app(root=tmp_path / "catalog", launch_workers=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok", "service": "catalog"}
        run, path = stage(client, app.state.metadata_store)
        assert client.post(path + "/claim", json={}).status_code == 409
        previews(client, path)
        waiting = client.post(path + "/await-agent")
        assert waiting.status_code == 200, waiting.text
        before = client.get(BASE + "/entries").json()
        organize(client, path)
        finished = client.post(path + "/finish")
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "succeeded"
        assert finished.json()["previewCoverage"] == {
            "available": 1,
            "missing": 1,
            "failed": 0,
            "pending": 0,
            "reused": 0,
        }
        found = client.get(BASE + "/entries", params={"q": "实验台布局", "category": "container"}).json()
        assert found["total"] == 2 and len(found["results"]) == 2
        entry = found["results"][0]
        assert entry["facts"]["dimensionsM"] is None and entry["facts"]["license"] is None
        assert (
            client.get(BASE + "/entries", params={"q": "实验台布局", "category": "instrument"}).json()[
                "total"
            ]
            == 0
        )
        assert client.get(BASE + "/entries", params={"release": before["release"]}).json() == before
        raw = bytearray()
        offset = 0
        while True:
            response = client.get(
                BASE + "/entry",
                params={
                    "id": entry["id"],
                    "release": found["release"],
                    "section": "sourceMetadata",
                    "offset": offset,
                    "documentDigest": entry["documentDigest"],
                },
            )
            assert response.status_code == 200, response.text
            section = response.json()
            assert len(response.content) < 32768
            raw.extend(
                section["content"].encode()
                if section["encoding"] == "utf-8"
                else base64.b64decode(section["content"])
            )
            if section["nextOffset"] is None:
                break
            offset = section["nextOffset"]
        assert json.loads(raw)["long"] == "多语言资料" * 9000
        assert (
            client.get(BASE + "/entry", params={"id": entry["id"], "documentDigest": "wrong"}).status_code
            == 409
        )
        thumbnail_path = BASE + "/thumbnails/" + entry["preview"]["sha256"] + ".webp"
        image = client.get(thumbnail_path)
        assert image.status_code == 200 and image.headers["content-type"] == "image/webp"
        with Image.open(BytesIO(image.content)) as decoded:
            assert decoded.format == "WEBP" and decoded.size == (384, 256)
            assert decoded.convert("RGBA").getpixel((0, 0))[3] == 128
        retained_file = app.state.metadata_store.thumbnail_root / entry["preview"]["sha256"][7:]
        retained_file.write_bytes(b"corrupted local derivative")
        assert client.get(thumbnail_path).status_code == 409
        from packages.catalog_metadata.hub import HFHubReader

        monkeypatch.setattr(HFHubReader, "read_preview", lambda *args, **kwargs: (image_bytes(), "image/png"))
        repaired = client.get(
            BASE + "/preview",
            params={
                "entry": entry["id"],
                "release": found["release"],
                "retry": 1,
            },
        )
        assert repaired.status_code == 200 and repaired.content == image.content
        assert client.get(thumbnail_path).content == image.content
        backup = client.get(BASE + "/export").json()
        # The publication still serves its exact derivative after all disposable source images are gone.
        for source_cache in (app.state.metadata_store.blob_root / "previews").iterdir():
            source_cache.unlink()
        assert (
            client.get(BASE + "/preview", params={"entry": entry["id"], "release": found["release"]}).content
            == image.content
        )
        assert run["runtime"]["configured"] is False
    restored = create_app(root=tmp_path / "restored", launch_workers=False)
    with TestClient(restored) as client:
        result = client.post(BASE + "/restore", json=backup)
        assert result.status_code == 200, result.text
        assert client.get(thumbnail_path).content == image.content
        assert client.get(BASE + "/entries", params={"q": "实验台布局"}).json()["release"] == found["release"]


def test_preview_source_binding_failure_and_storage_budget_are_not_success(tmp_path):
    app = create_app(root=tmp_path, launch_workers=False)
    with TestClient(app) as client:
        _, path = stage(client, app.state.metadata_store, budgets={"thumbnailBytes": 1})
        batch = client.post(path + "/previews/claim", json={}).json()
        entry = batch["entries"][0]
        payload = {
            "upstreamId": entry["upstreamId"],
            "inputDigest": "wrong",
            "state": "available",
            "data": base64.b64encode(image_bytes()).decode("ascii"),
        }
        assert client.post(path + "/previews/submit", json=payload).status_code == 409
        payload["inputDigest"] = entry["inputDigest"]
        result = client.post(path + "/previews/submit", json=payload)
        assert result.status_code == 200 and result.json()["budgetExhausted"]
        assert not list(app.state.metadata_store.thumbnail_root.iterdir())
        finished = client.post(path + "/finish")
        assert finished.status_code == 200 and finished.json()["status"] == "budget_exhausted"
        assert finished.json()["previewCoverage"]["available"] == 0


def test_standalone_auth_and_previous_backup_format_remain_usable(tmp_path):
    app = create_app(root=tmp_path, operator_token="test-only-operator", launch_workers=False)
    with TestClient(app) as client:
        assert client.get(BASE).status_code == 403
        client.headers["authorization"] = "Bearer test-only-operator"
        _, path = stage(client, app.state.metadata_store, previews=False)
        assert client.post(path + "/await-agent").status_code == 200
        assert client.post(path + "/finish").status_code == 200
        old = client.get(BASE + "/export").json()
        old["schema"] = "lw.catalog-metadata/1"
        del old["tables"]["entry_previews"]
        del old["tables"]["thumbnail_files"]
        corrupted = copy.deepcopy(old)
        corrupted["tables"]["releases"][0]["id"] = "sha256:" + "b" * 64
        assert client.post(BASE + "/restore", json=corrupted).status_code == 400
        assert client.post(BASE + "/restore", json=old).status_code == 200


def test_preview_pixel_and_decode_limits():
    from packages.catalog_metadata.models import MetadataError
    from packages.catalog_metadata.thumbnails import create_thumbnail

    with pytest.raises(MetadataError, match="decoded safely"):
        create_thumbnail(b"not an image")
    output = BytesIO()
    Image.new("1", (5000, 4000)).save(output, format="PNG")
    with pytest.raises(MetadataError, match="16 megapixels"):
        create_thumbnail(output.getvalue())


def test_native_pagination_stays_bounded_and_pinned_while_publication_changes(tmp_path):
    app = create_app(root=tmp_path, launch_workers=False)
    store = app.state.metadata_store
    entries = [
        {
            "upstreamId": "条目/" + str(index),
            "name": {"zh": "长名称" * 70},
            "summary": {"zh": "用途摘要" * 200},
            "aliases": {"zh": ["别名" * 40] * 8},
            "tags": ["中文" * 60],
            "uses": ["用途" * 70] * 8,
        }
        for index in range(70)
    ]
    inventory = store.stage_inventory("simready", REVISION, entries, complete=True)
    store.publish([inventory["id"]])
    with TestClient(app) as client:
        response = client.get(BASE + "/entries", params={"q": "用途", "limit": 24})
        assert response.status_code == 200, response.text
        result = response.json()
        pinned = result["release"]
        seen = [entry["id"] for entry in result["results"]]
        assert len(response.content) <= 32768 and result["nextCursor"]
        replacement = store.stage_inventory(
            "simready", "b" * 40, [{"upstreamId": "replacement"}], complete=True
        )
        store.publish([replacement["id"]])
        while result["nextCursor"]:
            response = client.get(
                BASE + "/entries", params={"q": "用途", "limit": 24, "cursor": result["nextCursor"]}
            )
            assert response.status_code == 200 and len(response.content) <= 32768
            result = response.json()
            assert result["release"] == pinned
            seen.extend(entry["id"] for entry in result["results"])
        assert len(seen) == len(set(seen)) == 70
        assert client.get(BASE + "/entries", params={"q": "summary"}).json()["total"] == 0
        current = client.get(BASE + "/entries").json()
        assert current["total"] == 1 and current["release"] != pinned


def test_second_host_cannot_interrupt_the_existing_hosts_run(tmp_path):
    from packages.catalog_metadata.models import MetadataError

    first = create_app(root=tmp_path, launch_workers=False)
    second = create_app(root=tmp_path, launch_workers=False)
    with TestClient(first) as client:
        _, path = stage(client, first.state.metadata_store, previews=False)
        assert client.post(path + "/await-agent").status_code == 200
        with pytest.raises(MetadataError, match="Another catalog host"):
            with TestClient(second):
                pass
        assert client.get(path).json()["phase"] == "awaiting_agent"
        assert client.get(path).json()["status"] == "running"
    with TestClient(second) as client:
        assert client.get(path).json()["status"] == "interrupted"


def test_demand_images_cannot_occupy_the_native_search_thread_capacity(tmp_path, monkeypatch):
    from packages.catalog_metadata.hub import HFHubReader

    app = create_app(root=tmp_path, launch_workers=False)
    store = app.state.metadata_store
    inventory = store.stage_inventory(
        "simready",
        REVISION,
        [
            {
                "upstreamId": "object",
                "preview": {"role": "object", "path": "preview.png", "state": "not_collected"},
            }
        ],
        complete=True,
    )
    store.publish([inventory["id"]])
    identity = store.entries()[0]["id"]
    release, arrived = threading.Event(), threading.Event()
    requests = 0

    @app.middleware("http")
    async def observe_preview_requests(request, call_next):
        nonlocal requests
        if request.url.path == BASE + "/preview":
            requests += 1
            if requests == 48:
                arrived.set()
        return await call_next(request)

    def blocked(*args, **kwargs):
        assert release.wait(10)
        return image_bytes(), "image/png"

    monkeypatch.setattr(HFHubReader, "read_preview", blocked)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=50) as pool:
        images = [pool.submit(client.get, BASE + "/preview", params={"entry": identity}) for _ in range(48)]
        try:
            assert arrived.wait(5)
            found = pool.submit(client.get, BASE + "/entries").result(timeout=2)
            assert found.status_code == 200 and found.json()["total"] == 1
        finally:
            release.set()
        assert all(image.result().status_code == 200 for image in images)


def test_embedded_and_original_preview_have_distinct_honest_provenance(tmp_path):
    from packages.catalog_metadata.store import MetadataStore

    store = MetadataStore(tmp_path)
    original = image_bytes()
    encoded = BytesIO()
    Image.new("RGB", (120, 80), "blue").save(encoded, format="JPEG")
    embedded = encoded.getvalue()
    table = store.put_blob(b"deterministic table fixture")
    inventory = store.stage_inventory(
        "scenario",
        REVISION,
        [
            {
                "upstreamId": "item",
                "preview": {
                    "role": "object",
                    "embedded": True,
                    "path": "original-preview.png",
                    "sha256": "sha256:" + hashlib.sha256(original).hexdigest(),
                    "cachedSha256": "sha256:" + hashlib.sha256(embedded).hexdigest(),
                },
                "evidence": [table | {"path": "data/metadata.parquet"}],
            }
        ],
        complete=True,
    )
    entry = store.inventory_source_entries(inventory["id"])[0]
    first, _ = store.save_preview(entry, embedded)
    assert first["sourceKind"] == "embedded" and first["sourcePath"] == "data/metadata.parquet"
    assert first["sourceField"] == "preview" and first["declaredPreviewPath"] == "original-preview.png"
    second, _ = store.save_preview(entry, original)
    assert second["sourceKind"] == "object-preview" and second["sourcePath"] == "original-preview.png"
    assert first["sourceSha256"] != second["sourceSha256"]
    assert first["sha256"] != second["sha256"]

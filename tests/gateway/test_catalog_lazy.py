"""Request counts prove enumeration, search and content have separate costs."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.catalog.source import SourceError
from packages.catalog.sources import CatalogSource
from tests.fixtures.catalog_source import PNG, REVISION, CatalogSourceFixture
from tests.gateway.test_catalog_search import correlated_asset, upload
from tests.gateway.test_catalog_sync import BASE, SEARCH, start


def test_legacy_lazy_preview_excludes_smaller_normal_and_material_thumbnails():
    upstream = CatalogSourceFixture()
    upstream.names = ["air_filter_a01"]
    original = upstream.handle

    def handle(request):
        response = original(request)
        if "/tree/" in request.url.path and request.url.path.endswith("/air_filter_a01"):
            return httpx.Response(200, json=[*response.json(),
                {"type": "file", "path": "air_filter_a01/source_assets/.thumbs/normal.png", "size": 1},
                {"type": "file", "path": "air_filter_a01/Textures/.thumbs/material.png", "size": 2},
            ])
        return response

    upstream.handle = handle
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        assert start(client)["status"] == "succeeded"
        upstream.requests.clear()
        candidate = client.get(SEARCH, params={"scope": "online"}).json()["results"][0]
        image = client.get(candidate["preview"]["url"])
        assert image.status_code == 200 and image.content == PNG
        assert any(path.endswith("/simready_usd/.thumbs/small.png") for path in upstream.requests)
        assert not any("/source_assets/" in path or "/Textures/" in path for path in upstream.requests)


def test_directory_and_search_never_read_package_details_or_images():
    upstream = CatalogSourceFixture()
    upstream.names = [f"fixture_{index}" for index in range(75)]
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        assert start(client)["status"] == "succeeded"
        assert len(upstream.requests) == 76  # revision + paginated lightweight directory
        assert not any("/resolve/" in path for path in upstream.requests)
        assert not any(path.endswith("/" + name) for name in upstream.names for path in upstream.requests)
        upstream.requests.clear()
        result = client.get(SEARCH, params={"scope": "online", "limit": 10}).json()
        assert result["counts"]["matching"] == 75
        assert len(result["results"]) <= 10 and result["nextCursor"]
        assert all(r["preview"]["kind"] == "on-demand" for r in result["results"])
        next_page = client.get(
            SEARCH, params={"scope": "online", "limit": 10, "cursor": result["nextCursor"]}
        )
        assert next_page.status_code == 200
        assert not upstream.requests
        first = result["results"][0]
        assert client.get(first["preview"]["url"]).content == PNG
        assert len(upstream.requests) == 2  # inventory and one smallest image; no detail document
        assert sum(path.endswith(".png") for path in upstream.requests) == 1
        detail_url = "/api/v1/library/catalog/entries/" + first["id"].replace("@", "/")
        detail = client.get(detail_url, params={"view": "full"}).json()
        assert detail["source"]["bytes"] > 0 and detail["license"]["name"] == "CC-BY-4.0"
        assert not detail["acquisition"]["eligible"] and detail["license"]["unknown"]
        assert client.get(detail_url + "/estimate").json()["source"]["bytes"] > 0
        assert len(upstream.requests) == 3  # Opening details adds only the packaging declaration.
        assert (
            client.get(SEARCH, params={"scope": "online", "limit": 10}).json()["snapshot"]["id"]
            == result["snapshot"]["id"]
        )
        assert client.get(first["preview"]["url"]).content == PNG
        assert len(upstream.requests) == 3


def test_concurrent_same_preview_collapses_and_offline_restart_reuses_cache():
    upstream = CatalogSourceFixture()
    upstream.delay = 0.05
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        assert start(client)["status"] == "succeeded"
        result = client.get(SEARCH, params={"scope": "online", "q": "CT_fixture_a01"}).json()
        url = result["results"][0]["preview"]["url"]
        upstream.requests.clear()
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: client.get(url), range(6)))
        assert all(response.content == PNG for response in responses)
        assert len(upstream.requests) == 2
        assert client.get(url.removesuffix("/preview"), params={"view": "full"}).status_code == 200
        assert len(upstream.requests) == 3
    upstream.requests.clear()
    upstream.broken_metadata.add("CT_fixture_a01")
    upstream.fail_listing = True
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        assert client.get(url).content == PNG
        detail = client.get(url.removesuffix("/preview"), params={"view": "full"}).json()
        assert detail["source"]["bytes"] > 0
        assert not upstream.requests


def test_multiple_sources_same_name_are_isolated_and_one_failure_keeps_other_source():
    first, second = CatalogSourceFixture(), CatalogSourceFixture()
    first.names = second.names = ["same_name"]
    sources = [
        CatalogSource("simready", "nvidia/simready-assets", first.source),
        CatalogSource("other", "fixture/other", second.source),
    ]
    with TestClient(create_app(catalog_sources=sources)) as client:
        assert start(client)["status"] == "succeeded"
        results = client.get(SEARCH, params={"scope": "online"}).json()["results"]
        assert {r["id"] for r in results} == {f"simready/same_name@{REVISION}", f"other/same_name@{REVISION}"}
        a, b = (
            next(r for r in results if r["source"]["provider"] == provider)
            for provider in ("simready", "other")
        )
        first.requests.clear()
        second.requests.clear()
        assert client.get(a["preview"]["url"]).content == PNG
        assert first.requests and not second.requests
        first.requests.clear()
        assert client.get(b["preview"]["url"]).content == PNG
        assert second.requests and not first.requests
        first.fail_listing = True
        second.revision = "b" * 40
        updated = start(client)
        assert updated["status"] == "succeeded" and len(updated["errors"]) == 1
        assert {s["provider"]: s["status"] for s in updated["sources"]} == {
            "simready": "failed",
            "other": "succeeded",
        }
        results = client.get(SEARCH, params={"scope": "online"}).json()["results"]
        assert {r["id"] for r in results} == {f"simready/same_name@{REVISION}", "other/same_name@" + "b" * 40}
        assert (
            client.get(SEARCH, params={"scope": "online", "provider": "other"}).json()["counts"]["matching"]
            == 1
        )
        assert client.post(BASE, params={"provider": "missing"}).status_code == 404


def test_previous_v2_snapshot_and_cache_upgrade_without_content_downloads(tmp_path, monkeypatch):
    state = tmp_path / "old-state"
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(state))
    root = state / "catalog"
    (root / "snapshots").mkdir(parents=True)
    (root / "cache/metadata").mkdir(parents=True)
    (root / "cache/previews").mkdir(parents=True)
    upstream = CatalogSourceFixture()
    package, oid = upstream.names[0], f"{1:040x}"
    source = upstream.source()
    try:
        metadata = source.metadata(REVISION, package)
    finally:
        source.close()
    filename = hashlib.sha256(PNG).hexdigest() + ".png"
    metadata.update(packageId=package, treeOid=oid, previewFile=filename)
    key = hashlib.sha256(f"2/{package}/{oid}".encode()).hexdigest()
    (root / "cache/metadata" / (key + ".json")).write_text(json.dumps(metadata), encoding="utf-8")
    (root / "cache/previews" / filename).write_bytes(PNG)
    snapshot_file = REVISION + "-" + "a" * 32 + ".json"
    document = {
        "schema": "lw.asset-catalog/2",
        "provider": "simready",
        "dataset": "nvidia/simready-assets",
        "revision": REVISION,
        "indexedAt": "2026-10-25T00:00:00Z",
        "coverage": "legacy fixture",
        "entries": [
            {
                "packageId": package,
                "title": package,
                "source": metadata["source"]
                | {"url": "https://huggingface.co/datasets/nvidia/simready-assets"},
                "declaredLicense": metadata["declaredLicense"],
                "preview": {"url": "/api/v1/library/catalog/previews/" + filename, "reason": "old cache"},
                "discovery": {"treeOid": oid},
            }
        ],
        "sync": {},
        "limits": {},
    }
    (root / "snapshots" / snapshot_file).write_text(json.dumps(document), encoding="utf-8")
    (root / "current.json").write_text(json.dumps({"file": snapshot_file, "history": []}), encoding="utf-8")
    upstream.requests.clear()
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        assert client.get(SEARCH, params={"scope": "online"}).json()["snapshot"]["entryCount"] == 1
        assert client.get("/api/v1/library/catalog/previews/" + filename).content == PNG
        assert not upstream.requests
        assert start(client)["status"] == "succeeded"
        upstream.requests.clear()
        result = client.get(SEARCH, params={"scope": "online", "q": package}).json()["results"][0]
        assert client.get(result["preview"]["url"]).content == PNG
        detail = client.get("/api/v1/library/catalog/entries/" + result["id"].replace("@", "/")).json()
        assert detail["source"]["bytes"] > 0
        assert not upstream.requests


def test_content_cache_evicts_and_refetches_without_removing_snapshots():
    upstream = CatalogSourceFixture()
    upstream.names = ["fixture_a", "fixture_b"]
    upstream.images = {name: PNG + name.encode() + b"0" * 256 for name in upstream.names}
    app = create_app(catalog_sync_source=upstream.source)
    app.state.catalog.resources.max_cache_bytes = 700
    with TestClient(app) as client:
        assert start(client)["status"] == "succeeded"
        result = client.get(SEARCH, params={"scope": "online"}).json()
        for item in result["results"]:
            assert client.get(item["preview"]["url"]).content == upstream.images[item["source"]["packageId"]]
        cache = app.state.catalog.resources.root
        assert sum(p.stat().st_size for p in cache.rglob("*") if p.is_file()) <= 700
        upstream.requests.clear()
        item = result["results"][0]
        assert client.get(item["preview"]["url"]).content == upstream.images[item["source"]["packageId"]]
        assert upstream.requests
        assert (
            client.get(SEARCH, params={"scope": "online"}).json()["snapshot"]["id"]
            == result["snapshot"]["id"]
        )
        assert list(app.state.catalog_sync.snapshots.glob("*.json"))


def test_failed_image_does_not_block_details_and_explicit_retry_works():
    upstream = CatalogSourceFixture()
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        assert start(client)["status"] == "succeeded"
        item = client.get(SEARCH, params={"scope": "online", "q": "CT_fixture_a01"}).json()["results"][0]
        original = upstream.source

        def failed(**kwargs):
            source = original(**kwargs)
            source.file = lambda *args: (_ for _ in ()).throw(SourceError("temporary image failure"))
            return source

        app = client.app
        registration = app.state.catalog_sync.sources.registrations["simready"]
        from dataclasses import replace

        app.state.catalog_sync.sources.registrations["simready"] = replace(registration, factory=failed)
        assert client.get(item["preview"]["url"]).status_code == 503
        app.state.catalog_sync.sources.registrations["simready"] = registration
        assert client.get(item["preview"]["url"]).status_code == 503  # bounded automatic backoff
        detail = client.get(item["preview"]["url"].removesuffix("/preview")).json()
        assert detail["license"]["name"] == "CC-BY-4.0"
        assert client.get(item["preview"]["url"], params={"retry": 1}).content == PNG


def test_waiting_image_requests_do_not_occupy_search_capacity():
    upstream = CatalogSourceFixture()
    app = create_app(catalog_sync_source=upstream.source)
    release, saturated = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = 0

    def blocked(entry, **kwargs):
        nonlocal active
        with lock:
            active += 1
            if active == 8:
                saturated.set()
        assert release.wait(10)
        return PNG, "png"

    app.state.catalog.resources.preview = blocked
    with TestClient(app) as client:
        assert start(client)["status"] == "succeeded"
        item = client.get(SEARCH, params={"scope": "online"}).json()["results"][0]
        with ThreadPoolExecutor(max_workers=50) as pool:
            images = [pool.submit(client.get, item["preview"]["url"]) for _ in range(48)]
            try:
                assert saturated.wait(5)
                result = pool.submit(client.get, SEARCH, params={"scope": "online"}).result(timeout=2)
                assert result.status_code == 200
            finally:
                release.set()
            assert all(image.result().content == PNG for image in images)


def test_many_sources_keep_search_response_and_cursor_bounded(tmp_path):
    import copy
    from pathlib import Path

    seed = json.loads(
        (Path(__file__).resolve().parents[2] / "catalog/simready.json").read_text(encoding="utf-8")
    )
    groups = []
    for index in range(100):
        group = copy.deepcopy(seed)
        group["provider"] = ("source-" + str(index)).ljust(96, "a")
        group["dataset"] = "数" * 300
        group["coverage"] = "报" * 1000
        groups.append(group)
    path = tmp_path / "many.json"
    path.write_text(
        json.dumps({"schema": "lw.asset-catalog/3", "sources": groups, "limits": {}}), encoding="utf-8"
    )
    with TestClient(create_app(catalog_path=path, catalog_sources=[])) as client:
        params = {"scope": "online", "provider": groups[0]["provider"], "limit": 1}
        response = client.get(SEARCH, params=params)
        value = response.json()
        assert len(response.content) <= 32768
        assert len(value["results"]) == 1 and value["nextCursor"]
        assert value["snapshot"]["sourceCount"] == 100 and value["snapshot"]["sourcesTruncated"]
        assert 0 < len(value["snapshot"]["sources"]) <= 20
        assert len(json.dumps(value["snapshot"]["sources"], ensure_ascii=False,
                              separators=(",", ":")).encode()) <= 8192
        params["cursor"] = value["nextCursor"]
        assert client.get(SEARCH, params=params).json()["results"][0]["id"] != value["results"][0]["id"]


def test_shared_transfer_budget_stops_later_sources(monkeypatch):
    from packages.catalog import sync

    first, second, third = (CatalogSourceFixture() for _ in range(3))
    first.names = second.names = third.names = ["same_name"]
    monkeypatch.setattr(sync, "MAX_TRANSFER_BYTES", 200)
    sources = [
        CatalogSource(name, "fixture/" + name, source.source)
        for name, source in [("simready", first), ("second", second), ("third", third)]
    ]
    with TestClient(create_app(catalog_sources=sources)) as client:
        record = start(client)
        assert record["status"] == "succeeded"  # First successful source is still published.
        assert [result["status"] for result in record["sources"]] == ["succeeded", "failed", "failed"]
        assert record["networkBytes"] > 200
        assert not third.requests
        assert client.get(BASE).json()["limits"]["transferBytes"] == 200


def test_other_provider_cannot_shadow_historical_installed_provenance():
    first, other = CatalogSourceFixture(), CatalogSourceFixture()
    other.names = ["box_cardboard_a01"]
    sources = [
        CatalogSource("simready", "nvidia/simready-assets", first.source),
        CatalogSource("other", "fixture/other", other.source),
    ]
    with TestClient(create_app(catalog_sources=sources)) as client:
        upload(client, correlated_asset())
        assert start(client)["status"] == "succeeded"
        params = {"scope": "all", "provider": "simready", "installed": True, "q": "cardboard"}
        assert client.get(SEARCH, params=params).json()["results"]
        first.names = ["CT_fixture_a01"]
        assert start(client)["status"] == "succeeded"
        result = client.get(SEARCH, params=params).json()["results"]
        assert "environment/cardboard-box@1" in {item["id"] for item in result}

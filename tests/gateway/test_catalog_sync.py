"""Sync publication, discovery boundaries, cached previews and restart over HTTP."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from tests.fixtures.catalog_source import PNG, REVISION, CatalogSourceFixture
from tests.gateway.test_catalog_search import correlated_asset, upload

BASE = "/api/v1/library/catalog/sync"
SEARCH = "/api/v1/library/search"


def finished(client, identifier):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        value = client.get(BASE + "/" + identifier).json()
        if value["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
            return value
        time.sleep(.01)
    raise AssertionError(value)


def start(client):
    response = client.post(BASE)
    assert response.status_code == 202, response.text
    return finished(client, response.json()["id"])


def test_full_pagination_incremental_reuse_revision_admission_and_offline_restart():
    upstream = CatalogSourceFixture()
    app = create_app(catalog_sync_source=upstream.source)
    with TestClient(app) as client:
        upload(client, correlated_asset())
        before = client.get(SEARCH, params={"scope": "online", "limit": 1}).json()
        record = start(client)
        assert record["status"] == "succeeded", record
        assert record["discovered"] == record["indexed"] == 2
        assert record["previewsCached"] == 2
        assert record["metadataFailed"] == record["previewsFailed"] == 0
        assert record["networkBytes"] > len(PNG)
        published = client.get(SEARCH, params={"scope": "online"}).json()
        assert {item["source"]["packageId"] for item in published["results"]} == set(upstream.names)
        assert all(item["preview"]["kind"] == "cached" for item in published["results"])
        image_url = published["results"][0]["preview"]["url"]
        image = client.get(image_url)
        assert image.content == PNG
        assert image.headers["content-type"] == "image/png"
        assert "immutable" in image.headers["cache-control"]
        stale = client.get(SEARCH, params={"scope": "online", "cursor": before["nextCursor"]})
        assert stale.status_code == 400 and stale.json()["detail"]["code"] == "stale_cursor"
        unknown = next(item for item in published["results"] if item["source"]["packageId"].startswith("CT_"))
        assert unknown["license"]["name"] == "CC-BY-4.0"
        assert unknown["license"]["unknown"] and not unknown["license"]["admitted"]
        assert unknown["kind"] == "unknown" and unknown["usage"]["dressing"] is None
        rejected = client.post("/api/v1/library/acquisitions", json={"ref": unknown["ref"]})
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "acquisition_ineligible"
        admitted = client.get(SEARCH, params={"scope": "online", "licenseAdmitted": True}).json()
        assert admitted["counts"]["matching"] == 1

        upstream.requests.clear()
        reused = start(client)
        assert reused["metadataReused"] == reused["previewsReused"] == 2
        assert not any("/resolve/" in path for path in upstream.requests)

        old_box = f"simready/box_cardboard_a01@{REVISION}"
        upstream.revision = "a" * 40
        changed = start(client)
        assert changed["status"] == "succeeded"
        current = client.get(SEARCH, params={"scope": "online"}).json()
        assert all(not item["acquisition"]["eligible"] for item in current["results"])
        assert all(item["source"]["revision"] == upstream.revision for item in current["results"])
        installed = client.get(SEARCH, params={"scope": "all", "q": "cardboard", "installed": True}).json()
        assert "environment/cardboard-box@1" in {item["id"] for item in installed["results"]}
        exact = client.get(SEARCH, params={"q": old_box, "scope": "online"}).json()
        assert exact["results"][0]["id"] == old_box
        assert exact["results"][0]["license"]["admitted"]
        old_detail = client.get("/api/v1/library/catalog/entries/" + old_box.replace("@", "/")).json()
        assert old_detail["license"]["admitted"]

        stable = current["snapshot"]["id"]
        upstream.fail_listing = True
        failed = start(client)
        assert failed["status"] == "failed"
        assert client.get(SEARCH, params={"scope": "online"}).json()["snapshot"]["id"] == stable
        assert not any(path.endswith(".usd") for path in upstream.requests)

    upstream.requests.clear()
    with TestClient(create_app(catalog_sync_source=upstream.source)) as restarted:
        assert restarted.get(SEARCH, params={"scope": "online"}).json()["snapshot"]["id"] == stable
        assert restarted.get(image_url).content == PNG
        assert not upstream.requests  # Startup and search remain offline.
        old = restarted.get("/api/v1/library/catalog/entries/" + old_box.replace("@", "/"))
        assert old.status_code == 200 and old.json()["license"]["admitted"]


def test_unknown_metadata_excludes_hard_size_filters_and_does_not_invent_a_preview():
    upstream = CatalogSourceFixture()
    upstream.broken_metadata.add("CT_fixture_a01")
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        record = start(client)
        assert record["status"] == "succeeded"
        assert record["metadataFailed"] == record["previewsFailed"] == 1
        assert record["previewsMissing"] == 0
        response = client.get(SEARCH, params={"scope": "online", "q": "CT_fixture_a01"}).json()
        item = response["results"][0]
        assert item["source"]["bytes"] is None
        assert item["preview"]["kind"] == "unavailable"
        filtered = client.get(SEARCH, params={
            "scope": "online", "q": "CT_fixture_a01", "minSourceBytes": 0,
        }).json()
        assert filtered["counts"]["matching"] == 0
        estimate_path = "/api/v1/library/catalog/entries/" + item["id"].replace("@", "/") + "/estimate"
        estimate = client.get(estimate_path).json()
        assert estimate["requiredDiskBytes"] is None and not estimate["withinLimits"]
        assert estimate["source"]["licenseFiles"] == []


def test_cancel_keeps_published_snapshot_and_retry_reuses_cache():
    upstream = CatalogSourceFixture()
    upstream.delay = .3
    app = create_app(catalog_sync_source=upstream.source)
    with TestClient(app) as client:
        before = client.get(SEARCH).json()["snapshot"]["id"]
        created = client.post(BASE).json()
        assert upstream.entered.wait(2)
        assert client.post(BASE).status_code == 409
        cancelled = client.post(BASE + "/" + created["id"] + "/cancel")
        assert cancelled.status_code == 200
        assert finished(client, created["id"])["status"] == "cancelled"
        assert client.get(SEARCH).json()["snapshot"]["id"] == before
        upstream.delay = 0
        retry = client.post(BASE + "/" + created["id"] + "/retry")
        assert retry.status_code == 202
        assert finished(client, retry.json()["id"])["status"] == "succeeded"


def test_startup_marks_unfinished_sync_interrupted_without_replacing_seed(tmp_path, monkeypatch):
    root = tmp_path / "restart"
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(root))
    (root / "catalog").mkdir(parents=True)
    identifier = "f" * 32
    (root / "catalog/latest.json").write_text(json.dumps({
        "id": identifier, "status": "running", "stage": "indexing", "errors": [],
    }), encoding="utf-8")
    with TestClient(create_app()) as client:
        assert client.get(BASE).json()["current"]["status"] == "interrupted"
        assert client.get(BASE + "/" + identifier).json()["status"] == "interrupted"
        assert client.get(SEARCH, params={"scope": "online"}).json()["snapshot"]["entryCount"] == 5


def test_operator_access_is_required_before_any_source_request():
    upstream = CatalogSourceFixture()
    settings = Settings(remote=True, operator_token="operator-fixture", viewer_token="viewer-fixture")
    with TestClient(create_app(settings, catalog_sync_source=upstream.source)) as client:
        client.headers["Authorization"] = "Bearer viewer-fixture"
        assert client.get(BASE).status_code == 200
        assert client.post(BASE).status_code == 403
        assert not upstream.requests
        client.headers["Authorization"] = "Bearer operator-fixture"
        assert start(client)["status"] == "succeeded"


def test_pagination_cannot_leave_the_pinned_source_or_publish_a_partial_directory():
    upstream = CatalogSourceFixture()
    upstream.unsafe_next = True
    with TestClient(create_app(catalog_sync_source=upstream.source)) as client:
        before = client.get(SEARCH).json()["snapshot"]["id"]
        record = start(client)
        assert record["status"] == "failed"
        assert "left the fixed dataset/revision" in record["errors"][-1]["message"]
        assert client.get(SEARCH).json()["snapshot"]["id"] == before


def test_sync_preserves_configured_acquisition_limits_after_publication_and_restart(tmp_path):
    seed_path = Path(__file__).resolve().parents[2] / "catalog/simready.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    seed["limits"] = {"sourcePackageBytes": 1048576, "batchBytes": 2097152}
    path = tmp_path / "seed.json"
    path.write_text(json.dumps(seed), encoding="utf-8")
    upstream = CatalogSourceFixture()
    estimate = "/api/v1/library/catalog/entries/simready/box_cardboard_a01/" + REVISION + "/estimate"
    with TestClient(create_app(catalog_path=path, catalog_sync_source=upstream.source)) as client:
        assert start(client)["status"] == "succeeded"
        assert client.get(estimate).json()["limits"]["sourcePackageBytes"] == 1048576
    with TestClient(create_app(catalog_path=path, catalog_sync_source=upstream.source)) as restarted:
        value = restarted.get(estimate).json()
        assert value["limits"]["sourcePackageBytes"] == 1048576
        assert value["limits"]["batchBytes"] == 2097152
        assert not value["withinLimits"]

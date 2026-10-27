"""Released metadata consumer behavior through the existing public HTTP API.

These source documents are synthetic regression fixtures, not quality evidence
or a replacement for the separately reconciled real-source inventory.
"""

import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.catalog_metadata import MetadataStore
from tests.fixtures.catalog_source import PNG, PNG_PIXELS, thumbnail_pixels
from tests.gateway.test_catalog_search import correlated_asset, upload

REVISION = "b" * 40


class PublishedStore:
    def __init__(self, root, count=30):
        self.root = Path(root)
        self.documents = [{
            "id": f"scenario/tool-{index:03d}@{REVISION}",
            "collectionId": "scenario", "upstreamId": f"tool-{index:03d}", "revision": REVISION,
            "name": {"en": f"Fixture tool {index:03d}", "zh": f"夹具工具 {index:03d}"},
            "summary": {"en": "Synthetic object used to test catalog release pagination.",
                        "zh": "合成夹具；用于验证目录发布与分页。"},
            "aliases": {"en": ["laboratory holder"], "zh": ["器材支架"]},
            "category": "lab-equipment", "tags": ["holder", "支架"], "uses": ["sample organization"],
            "facts": {"sourceBytes": None, "fileCount": 2, "dimensionsM": None,
                      "license": {"name": "source-declared-license"}},
            "source": {"dataset": "fixture/scenario", "url": "https://huggingface.co/datasets/fixture/scenario"},
            "preview": {"state": "missing", "path": None, "role": None},
            "evidence": [{"id": "sha256:" + "d" * 64, "kind": "publisher", "fields": ["name"]}],
            "organization": {"status": "pending", "version": 0}, "vocabularyVersion": "v1",
        } for index in range(count)]
        self.version = 1

    def latest_release(self):
        return {"id": f"release-{self.version}", "createdAt": "2026-10-26T00:00:00+00:00",
                "vocabularyVersion": "v1", "entryCount": len(self.documents),
                "collections": [{"id": "scenario", "revision": REVISION,
                                 "coverage": {"inventory": len(self.documents), "organized": 0}}]}

    def entries(self, release_id=None):
        return copy.deepcopy(self.documents)

    def entry(self, identity, release_id=None):
        return next(copy.deepcopy(value) for value in self.documents if value["id"] == identity)

    def vocabulary(self, version=None):
        return {"version": "v1", "categories": [{"id": "lab-equipment",
                "name": {"en": "Lab equipment", "zh": "实验器材"}, "aliases": {"en": [], "zh": []}}],
                "tags": []}


def test_released_metadata_search_preserves_hard_conditions_and_detail_provenance(tmp_path):
    app = create_app()
    store = PublishedStore(tmp_path)
    with TestClient(app) as client:
        app.state.catalog.attach_metadata(store)
        response = client.get("/api/v1/library/search", params={
            "q": "器材支架", "scope": "online", "category": "lab-equipment", "tag": "holder", "limit": 5,
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["counts"]["matching"] == 30
        assert body["snapshot"]["metadataRelease"]["id"] == "release-1"
        entry = body["results"][0]
        assert entry["category"] == "lab-equipment"
        assert entry["tags"] == ["holder", "支架"]
        assert entry["license"]["unknown"] is True
        assert entry["license"]["admitted"] is False
        assert entry["acquisition"]["eligible"] is False
        assert entry["preview"]["state"] == "missing"
        for condition in ({"maxDimensionM": 1}, {"maxSourceBytes": 1}, {"licenseAdmitted": True}):
            denied = client.get("/api/v1/library/search", params={
                "provider": "scenario", "scope": "online", **condition,
            })
            assert denied.status_code == 200, denied.text
            assert denied.json()["results"] == []
            assert denied.json()["zeroResult"]["reason"] == "hard_filters"
        detail = client.get(f"/api/v1/library/catalog/entries/scenario/tool-000/{REVISION}",
                            params={"release": body["snapshot"]["id"], "view": "full"})
        assert detail.status_code == 200, detail.text
        assert detail.json()["metadata"]["evidence"][0]["kind"] == "publisher"
        assert detail.json()["metadata"]["organization"]["status"] == "pending"
        legacy = client.get("/api/v1/library/catalog/entries/simready/box_cardboard_a01/"
                            "524747d9b5e1f21b5b18b56041788c01d745f1a1")
        assert legacy.status_code == 200
        assert legacy.json()["license"]["admitted"] is True


def test_page_locations_follow_real_byte_bound_and_pin_release_across_publish_and_restart(tmp_path):
    store = PublishedStore(tmp_path)
    app = create_app()
    with TestClient(app) as client:
        app.state.catalog.attach_metadata(store)
        first = client.get("/api/v1/library/search", params={
            "scope": "online", "provider": "scenario", "limit": 24, "page": 1,
        })
        assert first.status_code == 200, first.text
        assert len(first.content) <= 32768
        first = first.json()
        released = first["snapshot"]["id"]
        assert first["pagination"]["totalPages"] >= 2
        first_ids = [value["id"] for value in first["results"]]
        store.version = 2
        store.documents[0]["name"]["en"] = "Changed publication"
        updated = client.get("/api/v1/library/search", params={"scope": "online"}).json()
        assert updated["snapshot"]["id"] != released
        gathered = list(first_ids)
        for page in range(2, first["pagination"]["totalPages"] + 1):
            result = client.get("/api/v1/library/search", params={
                "scope": "online", "provider": "scenario", "limit": 24, "page": page, "release": released,
            })
            assert result.status_code == 200, result.text
            assert len(result.content) <= 32768
            result = result.json()
            assert result["snapshot"]["id"] == released
            gathered.extend(value["id"] for value in result["results"])
        assert len(gathered) == len(set(gathered)) == 30
        assert set(gathered) == {value["id"] for value in store.documents}
        continued = client.get("/api/v1/library/search", params={
            "scope": "online", "provider": "scenario", "limit": 24, "cursor": first["nextCursor"],
        })
        assert continued.status_code == 200, continued.text
        assert continued.json()["snapshot"]["id"] == released
    restarted = create_app()
    with TestClient(restarted) as client:
        restarted.state.catalog.attach_metadata(store)
        previous = client.get("/api/v1/library/search", params={
            "scope": "online", "provider": "scenario", "limit": 24, "page": 1, "release": released,
        })
        assert previous.status_code == 200, previous.text
        assert [item["id"] for item in previous.json()["results"]] == first_ids
        assert previous.json()["results"][0]["title"] == "Fixture tool 000"
        missing = client.get("/api/v1/library/search", params={"release": "sha256:" + "0" * 64})
        assert missing.status_code == 409
        assert "repeat the search" in missing.text


def test_failed_index_retains_release_and_encoded_identity_is_exact(tmp_path, monkeypatch):
    from packages.catalog import maintained

    documents = PublishedStore(tmp_path, count=2).documents
    for document in documents:
        document["category"] = "equipment"
    documents[0]["upstreamId"] = "scenario:asset_abc"
    documents[0].pop("id")
    store = MetadataStore(tmp_path)
    initial = store.stage_inventory("scenario", REVISION, documents, complete=True)
    store.publish([initial["id"]])
    app = create_app()
    with TestClient(app) as client:
        app.state.catalog.attach_metadata(store)
        filters = {"scope": "online", "provider": "scenario"}
        before = client.get("/api/v1/library/search", params=filters).json()
        identity = f"scenario/scenario%3Aasset_abc@{REVISION}"
        exact = client.get("/api/v1/library/search", params={"scope": "online", "q": identity})
        assert exact.status_code == 200, exact.text
        assert exact.json()["results"][0]["id"] == identity
        detail = client.get(f"/api/v1/library/catalog/entries/scenario/scenario%253Aasset_abc/{REVISION}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["id"] == identity
        documents[1]["name"]["en"] = "Changed object"
        changed = store.stage_inventory("scenario", REVISION, documents, complete=True)
        build = maintained.build_semantic_index

        def broken_index(*args, **kwargs):
            raise RuntimeError("Consumer index build failed")

        monkeypatch.setattr(maintained, "build_semantic_index", broken_index)
        with pytest.raises(RuntimeError, match="Consumer index build failed"):
            store.publish([changed["id"]], release_preparer=app.state.catalog.prepare_metadata_release)
        assert store.latest_release()["id"] == before["snapshot"]["metadataRelease"]["id"]
        still = client.get("/api/v1/library/search", params={"scope": "online", "provider": "scenario"})
        assert still.status_code == 200
        assert still.json()["snapshot"]["id"] == before["snapshot"]["id"]
        monkeypatch.setattr(maintained, "build_semantic_index", build)
        store.publish([changed["id"]], release_preparer=app.state.catalog.prepare_metadata_release)
        after = client.get("/api/v1/library/search", params=filters).json()
        assert after["snapshot"]["id"] != before["snapshot"]["id"]


def test_old_http_search_stays_available_while_new_consumer_index_is_paused(tmp_path, monkeypatch):
    from packages.catalog import maintained

    documents = PublishedStore(tmp_path, count=2).documents
    for document in documents:
        document["category"] = "equipment"
    store = MetadataStore(tmp_path)
    initial = store.stage_inventory("scenario", REVISION, documents, complete=True)
    store.publish([initial["id"]])
    app = create_app()
    entered, release = threading.Event(), threading.Event()
    with TestClient(app) as client:
        app.state.catalog.attach_metadata(store)
        filters = {"scope": "online", "provider": "scenario"}
        before = client.get("/api/v1/library/search", params=filters).json()
        documents[1]["name"]["en"] = "Changed object"
        changed = store.stage_inventory("scenario", REVISION, documents, complete=True)
        build = maintained.build_semantic_index

        def paused_index(*args, **kwargs):
            entered.set()
            assert release.wait(8), "Test index build was never released"
            return build(*args, **kwargs)

        monkeypatch.setattr(maintained, "build_semantic_index", paused_index)
        with ThreadPoolExecutor(max_workers=2) as pool:
            publishing = pool.submit(store.publish, [changed["id"]],
                                     release_preparer=app.state.catalog.prepare_metadata_release)
            try:
                assert entered.wait(4)
                reading = pool.submit(client.get, "/api/v1/library/search", params=filters)
                response = reading.result(timeout=2)
                assert response.status_code == 200
                assert response.json()["snapshot"]["id"] == before["snapshot"]["id"]
            finally:
                release.set()
            publishing.result(timeout=4)


def test_embedded_preview_and_old_exact_preview_stay_readable_from_retained_cache(tmp_path, monkeypatch):
    from packages.catalog_metadata.hub import HFHubReader

    store = MetadataStore(tmp_path / "metadata")
    digest = store.put_blob(PNG, kind="preview")["sha256"]
    entry = {"upstreamId": "embedded-object", "preview": {
        "state": "available", "role": "object", "path": None,
        "cachedSha256": digest, "embedded": True,
    }}
    initial = store.stage_inventory("scenario", "a" * 40, [entry], complete=True)
    store.publish([initial["id"]])

    def no_network(*args, **kwargs):
        raise AssertionError("A cached embedded preview must not contact upstream")

    monkeypatch.setattr(HFHubReader, "_fetch", no_network)
    app = create_app(metadata_store=store, metadata_launch_workers=False)
    with TestClient(app) as client:
        result = client.get("/api/v1/library/search", params={"scope": "online", "provider": "scenario"})
        assert result.status_code == 200, result.text
        preview = result.json()["results"][0]["preview"]
        assert preview["state"] == "available" and preview["kind"] == "on-demand"
        assert thumbnail_pixels(client.get(preview["url"]).content) == PNG_PIXELS
        newer = store.stage_inventory("scenario", "b" * 40, [entry], complete=True)
        store.publish([newer["id"]], release_preparer=app.state.catalog.prepare_metadata_release)
        old = client.get("/api/v1/library/catalog/entries/scenario/embedded-object/" + "a" * 40)
        assert old.status_code == 200, old.text
        old_url = old.json()["preview"]["url"]
        assert "release=None" not in old_url
        assert thumbnail_pixels(client.get(old_url).content) == PNG_PIXELS
    with TestClient(create_app(metadata_store=store, metadata_launch_workers=False)) as client:
        assert thumbnail_pixels(client.get(old_url).content) == PNG_PIXELS
        store.blob_path(digest).unlink()
        retained = client.get(old_url)
        assert retained.status_code == 200
        assert thumbnail_pixels(retained.content) == PNG_PIXELS


def test_shared_local_metadata_filters_exclude_unknown_and_nonmatching_assets(tmp_path):
    store = MetadataStore(tmp_path / "metadata")
    revision = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
    inventory = store.stage_inventory("simready", revision, [{
        "upstreamId": "box_cardboard_a01", "category": "container", "tags": ["sample"],
        "aliases": {"en": [], "zh": ["样品容器"]},
    }], complete=True)
    store.publish([inventory["id"]])
    with TestClient(create_app(metadata_store=store, metadata_launch_workers=False)) as client:
        matching = upload(client, correlated_asset())["assetVersion"]
        unknown = client.get("/api/v1/library/search", params={"scope": "local"}).json()
        assert len(unknown["results"]) > 1
        filters = {"scope": "local", "category": "container", "tag": "sample", "q": "样品容器",
                   "sort": "name", "page": 1, "limit": 24}
        filtered = client.get("/api/v1/library/search", params=filters)
        assert filtered.status_code == 200, filtered.text
        assert [value["id"] for value in filtered.json()["results"]] == [matching]
        for different in ({"category": "equipment"}, {"provider": "scenario"}):
            excluded = client.get("/api/v1/library/search", params={**filters, **different})
            assert excluded.json()["results"] == []
        gap_only = client.get("/api/v1/library/search", params={**filters, "gaps": True})
        assert gap_only.status_code == 200, gap_only.text
        for value in gap_only.json()["results"]:
            assert value["local"]["asset"]["gapCount"] > 0


def test_many_unicode_collection_headers_preserve_bounded_results_and_exact_release_reads(tmp_path):
    store = MetadataStore(tmp_path / "metadata")
    inventories = []
    for index in range(200):
        collection = "source-" + str(index).zfill(3) + "x" * 60
        store.register_collection({"id": collection, "dataset": f"fixture/collection-{index}",
                                   "recipe": "simready"})
        counts = {"entries": 1}
        if index == 0:
            counts["sourceNote"] = "来源说明" * 300
        inventories.append(store.stage_inventory(collection, REVISION, [{
            "upstreamId": "box", "name": {"en": f"A object {index:03d}", "zh": "实验室容器" * 200},
            "summary": {"en": "Synthetic bounded-response fixture.",
                        "zh": "用于验证长中文摘要的夹具。" * 200},
        }], complete=True, counts=counts)["id"])
    released = store.publish(inventories)
    app = create_app(metadata_store=store, metadata_launch_workers=False)
    with TestClient(app) as client:
        first = client.get("/api/v1/library/search", params={"scope": "online", "limit": 1})
        assert first.status_code == 200, first.text
        assert len(first.content) <= 32768
        body = first.json()
        assert body["zeroResult"] is None and body["counts"]["returned"] == 1
        assert body["counts"]["matching"] == 200 + len(app.state.catalog.entries)
        assert body["results"][0]["source"]["provider"].startswith("source-000")
        assert body["results"][0]["descriptions"]["zh"]
        header = body["snapshot"]["metadataRelease"]
        assert header["id"] == released["id"] and header["entryCount"] == 200
        assert header["collectionCount"] == 200 and header["collectionsTruncated"] is True
        assert 0 < len(header["collections"]) < 20
        assert len(json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()) <= 8192
        assert first.json()["nextCursor"]

        # The summary points at the exact retained JSON document. Each read is
        # bounded independently; Unicode characters never split between chunks.
        offset, pieces = 0, []
        while True:
            detail = client.get(header["details"]["path"], params={"offset": offset})
            assert detail.status_code == 200, detail.text
            assert len(detail.content) <= 32768
            fragment = detail.json()
            assert fragment["id"] == released["id"]
            pieces.append(fragment["text"])
            if fragment["nextOffset"] is None:
                break
            assert fragment["nextOffset"] > offset
            offset = fragment["nextOffset"]
        assert json.loads("".join(pieces)) == released

        changed = store.stage_inventory("source-000" + "x" * 60, "c" * 40,
                                        [{"upstreamId": "box"}], complete=True)
        newer = store.publish([changed["id"]], release_preparer=app.state.catalog.prepare_metadata_release)
        assert newer["id"] != released["id"]
        continued = client.get("/api/v1/library/search", params={
            "scope": "online", "limit": 1, "cursor": body["nextCursor"],
        })
        assert continued.status_code == 200 and len(continued.content) <= 32768
        assert continued.json()["counts"]["returned"] == 1
        assert continued.json()["snapshot"]["id"] == body["snapshot"]["id"]
        assert continued.json()["snapshot"]["metadataRelease"]["id"] == released["id"]

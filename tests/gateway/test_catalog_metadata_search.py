"""Released metadata consumer behavior through the existing public HTTP API.

These source documents are synthetic regression fixtures, not quality evidence
or a replacement for the separately reconciled real-source inventory.
"""

import copy
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.main import create_app

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

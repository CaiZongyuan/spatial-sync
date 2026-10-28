"""Integration tests for the repository-backed lightweight catalog.

Verifies that the gateway serves the versioned catalog (SimReady, Scenario, ChemLab)
with local SQLite caching, Web/CLI/MCP search and describe contracts, and thumbnail
serving without depending on an external 1.8 GB metadata directory.
"""

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.catalog.lightweight import LightweightCatalogStore


def settings_for(tmp_path, **fields):
    return Settings(
        state_root=fields.pop("state_root", tmp_path / "state"),
        recordings_root=fields.pop("recordings_root", tmp_path / "recordings"),
        **fields,
    )


def test_isolated_rebuild_from_versioned_materials(tmp_path):
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    repo_catalog = Path(__file__).resolve().parents[2] / "catalog"
    shutil.copytree(repo_catalog / "data", catalog_root / "data")
    shutil.copytree(repo_catalog / "thumbnails", catalog_root / "thumbnails")

    store = LightweightCatalogStore(catalog_root)
    db_path = store.ensure_db()
    assert db_path.is_file()

    release = store.latest_release()
    assert release is not None
    assert release["entryCount"] == 4183
    assert len(release["collections"]) == 3
    collection_ids = {c["id"] for c in release["collections"]}
    assert collection_ids == {"chemlab", "scenario", "simready"}

    snapshot, entries = store.load_entries()
    assert snapshot.entry_count == 4183
    assert len(entries) == 4183
    assert snapshot.id.startswith("sha256:")

    # Spot check ChemLab microscope entry
    chem_id = "chemlab/instrument.bruker_x4poseidon@3b67020dcccb22e60be3235f32b39d03da5959ab"
    entry = store.entry(chem_id)
    assert entry["upstreamId"] == "instrument.bruker_x4poseidon"
    assert "Micro X-ray" in entry["name"]["en"]
    assert "显微" in entry["name"]["zh"]
    assert entry["preview"]["thumbnail"]["state"] == "available"

    # Verify thumbnail byte check
    thumb_key = entry["preview"]["thumbnail"]["sha256"]
    thumb_bytes = store.thumbnail(thumb_key)
    assert len(thumb_bytes) > 0
    assert thumb_bytes.startswith(b"RIFF")
    assert thumb_bytes[8:12] == b"WEBP"


def test_gateway_serves_lightweight_catalog_by_default(tmp_path, monkeypatch):
    repo_catalog = Path(__file__).resolve().parents[2] / "catalog"
    monkeypatch.setenv("SPATIAL_CATALOG_ROOT", str(repo_catalog))
    app = create_app(settings=settings_for(tmp_path), metadata_launch_workers=False)
    with TestClient(app) as client:
        # 1. Search online candidates cards view
        res = client.get("/api/v1/library/search", params={"scope": "online", "view": "cards", "limit": 24})
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["schema"] == "lw.catalog-cards/1"
        assert data["counts"]["onlineMatching"] == 4183
        assert len(data["results"]) == 24
        assert data["pagination"]["totalPages"] == 175

        # 2. Chinese query for microscope
        q_res = client.get(
            "/api/v1/library/search", params={"scope": "online", "view": "cards", "q": "显微镜"}
        )
        assert q_res.status_code == 200
        q_data = q_res.json()
        assert q_data["counts"]["onlineMatching"] == 8

        # 3. ChemLab describe
        desc_res = client.get(
            "/api/v1/library/catalog/entries/chemlab/instrument.bruker_x4poseidon/3b67020dcccb22e60be3235f32b39d03da5959ab",
            params={"view": "full"},
        )
        assert desc_res.status_code == 200
        desc_data = desc_res.json()
        assert desc_data["schema"] == "lw.catalog-entry/1"
        assert desc_data["title"] == "Micro X-ray computed tomography"
        assert desc_data["preview"]["kind"] == "local"
        assert desc_data["preview"]["state"] == "available"
        thumb_url = desc_data["preview"]["url"]
        assert thumb_url is not None

        # 4. Read thumbnail via maintenance thumbnail route
        thumb_res = client.get(thumb_url)
        assert thumb_res.status_code == 200
        assert thumb_res.headers["content-type"] == "image/webp"
        assert thumb_res.content[:4] == b"RIFF"

        # 5. Native metadata search (CLI / MCP endpoint)
        native_search = client.get("/api/v1/library/catalog/maintenance/entries", params={"q": "显微镜"})
        assert native_search.status_code == 200
        native_data = native_search.json()
        assert native_data["schema"] == "lw.catalog-native-search/1"
        assert native_data["total"] == 8

        # 6. Native describe (CLI / MCP metadata-show endpoint)
        chem_id = "chemlab/instrument.bruker_x4poseidon@3b67020dcccb22e60be3235f32b39d03da5959ab"
        native_show = client.get("/api/v1/library/catalog/maintenance/entry", params={"id": chem_id})
        assert native_show.status_code == 200
        show_data = native_show.json()
        assert show_data["schema"] == "lw.catalog-native-entry/1"
        assert show_data["id"] == chem_id

        # 7. Release document text
        snapshot_id = data["snapshot"]["id"]
        rel_res = client.get(f"/api/v1/library/catalog/releases/{snapshot_id}")
        assert rel_res.status_code == 200
        assert rel_res.json()["schema"] == "lw.catalog-release-text/1"

        # 8. Web compatibility: maintenance status has runs, runtime, and release object (S1 check)
        maint_res = client.get("/api/v1/library/catalog/maintenance")
        assert maint_res.status_code == 200
        maint_status = maint_res.json()
        assert maint_status["runs"] == []
        assert maint_status["runtime"]["configured"] is False
        assert isinstance(maint_status["release"], dict)
        assert maint_status["release"]["id"] == snapshot_id
        assert maint_status["capabilities"]["maintenance"] is False

        # 9. Web compatibility: vocabulary endpoint returns 200 (S1 check)
        vocab_res = client.get("/api/v1/library/catalog/maintenance/vocabulary")
        assert vocab_res.status_code == 200
        assert "version" in vocab_res.json()

        # 10. Structured HTTP error contracts: 404 for unknown entry, 400 for export (E1 check)
        unknown_res = client.get("/api/v1/library/catalog/maintenance/entry", params={"id": "not-there"})
        assert unknown_res.status_code == 404
        assert unknown_res.json()["detail"]["code"] == "unknown_entry"

        export_res = client.get("/api/v1/library/catalog/maintenance/export")
        assert export_res.status_code == 400
        assert export_res.json()["detail"]["code"] == "not_supported"

        # 11. Explicit non-existent revision rejected with 404 (S4 check)
        bad_rev_id = f"chemlab/instrument.bruker_x4poseidon@{'f' * 40}"
        bad_rev_res = client.get("/api/v1/library/catalog/maintenance/entry", params={"id": bad_rev_id})
        assert bad_rev_res.status_code == 404
        assert bad_rev_res.json()["detail"]["code"] == "unknown_entry"

        # 12. Retired maintenance actions answer 409 instead of failing (UI1 check)
        run_res = client.post(
            "/api/v1/library/catalog/maintenance/runs", json={"collectionId": "simready"}
        )
        assert run_res.status_code == 409
        assert run_res.json()["detail"]["code"] == "not_supported"
        collection_res = client.patch(
            "/api/v1/library/catalog/maintenance/collections/simready", json={"enabled": False}
        )
        assert collection_res.status_code == 409
        assert collection_res.json()["detail"]["code"] == "not_supported"

    # State root isolation: no legacy catalog-metadata directory was created
    assert not (tmp_path / "state" / "catalog-metadata").exists()


def test_stale_cursor_rejected_on_snapshot_mismatch(tmp_path, monkeypatch):
    repo_catalog = Path(__file__).resolve().parents[2] / "catalog"
    monkeypatch.setenv("SPATIAL_CATALOG_ROOT", str(repo_catalog))
    app = create_app(settings=settings_for(tmp_path), metadata_launch_workers=False)
    with TestClient(app) as client:
        # Valid page 1 search
        res = client.get("/api/v1/library/search", params={"scope": "online", "view": "cards", "limit": 10})
        cursor = res.json()["nextCursor"]
        assert cursor is not None

        # Requesting with the valid cursor succeeds
        res_next = client.get(
            "/api/v1/library/search",
            params={"scope": "online", "view": "cards", "limit": 10, "cursor": cursor},
        )
        assert res_next.status_code == 200

        # Requesting with a cursor belonging to a different query or invalid cursor fails
        res_bad = client.get(
            "/api/v1/library/search",
            params={"scope": "online", "view": "cards", "limit": 10, "cursor": "invalid_cursor"},
        )
        assert res_bad.status_code == 400

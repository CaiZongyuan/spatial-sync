"""Public HTTP journey for catalog search, describe, estimate and legacy library."""

import hashlib
import io
import json
import tarfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "catalog/simready.json"
REVISION = "524747d9b5e1f21b5b18b56041788c01d745f1a1"
BOX = f"simready/box_cardboard_a01@{REVISION}"


def custom_catalog(path):
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entry = data["entries"][0]
    entry["packageId"] = "fixture_widget"
    entry["source"]["url"] = (
        "https://huggingface.co/datasets/nvidia/simready-assets/tree/"
        f"{REVISION}/fixture_widget"
    )
    entry["names"] = {"en": "Fixture widget", "zh": "夹具部件"}
    entry["aliases"] = {"en": ["widget"], "zh": ["夹具"]}
    entry["descriptions"] = {"en": "A synthetic catalog fixture.", "zh": "合成的目录夹具。"}
    data["entries"] = [entry]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def upload(client, files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    response = client.post(
        "/api/v1/library/imports",
        content=output.getvalue(),
        headers={"Content-Type": "application/x-tar"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def correlated_asset():
    stage = (
        b'#usda 1.0\n(defaultPrim = "Asset"\n upAxis = "Z"\n metersPerUnit = 1)\n'
        b'def Xform "Asset" {}\n'
    )
    provenance = {
        "sourceRevision": REVISION,
        "sourcePackage": "box_cardboard_a01",
        "license": {"name": "CC-BY-4.0"},
        "files": {"asset.usda": {"sha256": hashlib.sha256(stage).hexdigest(),
                                 "bytes": len(stage)}},
    }
    return {
        "asset.yaml": yaml.safe_dump({
            "id": "environment/cardboard-box", "kind": "environment",
            "title": "Cardboard box", "stage": "asset.usda",
            "dimensionsM": [0.699435, 0.521152, 0.509927], "dressing": True,
        }).encode(),
        "asset.usda": stage,
        "LICENSE.txt": b"CC-BY-4.0\n",
        "provenance.json": json.dumps(provenance).encode(),
    }


def test_public_search_keeps_legacy_asset_routes_and_shapes_unchanged():
    with TestClient(create_app()) as client:
        legacy = client.get("/api/v1/library/assets")
        assert legacy.status_code == 200
        assert set(legacy.json()) == {"assets"}
        assert "robot/fr3@1" in {asset["id"] for asset in legacy.json()["assets"]}
        detail = client.get("/api/v1/library/assets/robot/fr3/1")
        assert detail.status_code == 200
        assert detail.json()["id"] == "robot/fr3@1"
        assert detail.json()["dimensionsM"]

        response = client.get("/api/v1/library/search", params={"q": "纸箱"})
        assert response.status_code == 200
        body = response.json()
        assert body["schema"] == "lw.catalog-search/1"
        assert body["scope"] == "all"
        assert body["unknownPolicy"] == "excluded"
        assert body["results"][0]["id"] == BOX
        assert body["results"][0]["names"] == {"en": "Cardboard box", "zh": "纸箱"}
        assert body["snapshot"]["id"].startswith("sha256:")
        assert body["snapshot"]["entryCount"] == 5
        assert body["snapshot"]["offlineReadable"] is True
        assert body["snapshot"]["networkRequired"] is False
        assert body["interpretedQuery"]["keywords"] == ["纸箱"]


def test_semantic_recall_is_labelled_and_keeps_hard_filter_semantics():
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/library/search", params={
            "q": "用来装实验室废弃物的容器", "scope": "online",
        })
        assert response.status_code == 200, response.text
        body = response.json()
        semantic = body["semantic"]
        assert semantic["schema"] == "lw.catalog-semantic/1"
        assert semantic["enabled"] is True
        assert semantic["applied"] is True
        assert semantic["model"]["kind"] == "deterministic-stand-in"
        assert semantic["model"]["real"] is False
        assert semantic["index"]["version"].startswith("sha256:")
        assert semantic["index"]["entryCount"] == 5
        assert body["snapshot"]["id"].startswith("sha256:")
        assert body["results"] and all(
            item["match"]["semanticScore"] is not None for item in body["results"]
        )
        assert "Semantic recall" in " ".join(body["interpretedQuery"]["notes"])

        keyword = client.get("/api/v1/library/search", params={
            "q": "纸箱", "scope": "online",
        }).json()
        assert keyword["semantic"]["applied"] is False
        assert keyword["semantic"]["reason"] == "keyword_matches"
        assert "semanticScore" not in keyword["results"][0]["match"]

        blocked = client.get("/api/v1/library/search", params={
            "q": "用来装实验室废弃物的容器", "scope": "online",
            "usage": "staticCollision", "maxDimensionM": "0.01",
        }).json()
        assert blocked["semantic"]["applied"] is True
        assert blocked["results"] == []
        assert blocked["zeroResult"]["reason"] == "hard_filters"


def test_search_exact_identity_filters_approximation_and_zero_results():
    with TestClient(create_app()) as client:
        exact = client.get("/api/v1/library/search",
                           params={"q": BOX, "scope": "online"})
        assert exact.status_code == 200
        assert [item["id"] for item in exact.json()["results"]] == [BOX]

        filtered = client.get("/api/v1/library/search", params={
            "q": "environment", "scope": "online", "usage": "dressing",
            "approximate": "true",
        })
        assert filtered.status_code == 200
        body = filtered.json()
        assert body["counts"]["approximate"] == 2
        assert body["results"][0]["match"]["satisfied"] == ["usage"]
        assert {item["id"].split("/")[1].partition("@")[0]
                for item in body["results"][3:]} == {
            "equipment_utility_cart_e01", "desk_a01",
        }

        zero = client.get("/api/v1/library/search", params={
            "q": "environment", "scope": "online", "maxDimensionM": "0.1",
        })
        assert zero.status_code == 200
        explanation = zero.json()["zeroResult"]
        assert explanation["reason"] == "hard_filters"
        assert {"filter": "maxDimensionM", "count": 5} in explanation["excludedBy"]
        assert {"action": "enable_approximation"} in explanation["suggestions"]

        missing = client.get("/api/v1/library/search", params={
            "q": "no-such-asset", "scope": "online",
        })
        assert missing.json()["zeroResult"]["reason"] == "no_query_match"


def test_describe_and_estimate_routes_expose_entry_facts_and_digest():
    with TestClient(create_app()) as client:
        summary = client.get(
            f"/api/v1/library/catalog/entries/simready/box_cardboard_a01/{REVISION}"
        )
        assert summary.status_code == 200
        body = summary.json()
        assert body["schema"] == "lw.catalog-entry/1"
        assert body["view"] == "summary"
        assert body["documentDigest"] == body["details"]["documentDigest"]
        assert body["license"]["admitted"] is True
        assert body["usage"]["staticCollision"]["eligible"] is True
        full = client.get(
            f"/api/v1/library/catalog/entries/simready/box_cardboard_a01/{REVISION}",
            params={"view": "full", "documentDigest": body["documentDigest"]},
        )
        assert full.status_code == 200
        assert full.json()["documentDigest"] == body["documentDigest"]
        changed = client.get(
            f"/api/v1/library/catalog/entries/simready/box_cardboard_a01/{REVISION}",
            params={"documentDigest": "0" * 64},
        )
        assert changed.status_code == 409
        assert changed.json()["detail"]["code"] == "catalog_changed"
        unknown = client.get(
            f"/api/v1/library/catalog/entries/simready/absent/{REVISION}"
        )
        assert unknown.status_code == 404
        assert unknown.json()["detail"]["code"] == "unknown_catalog_entry"

        estimate = client.get(
            f"/api/v1/library/catalog/entries/simready/box_cardboard_a01/{REVISION}/estimate"
        )
        assert estimate.status_code == 200
        value = estimate.json()
        assert value["schema"] == "lw.catalog-estimate/1"
        assert value["requiredDiskBytes"] == 21530693 + 19085294 + 10737418240
        assert value["cache"]["known"] is False
        assert value["downloadsMainFiles"] is False
        assert value["networkRequired"] is True
        cart = client.get(
            "/api/v1/library/catalog/entries/simready/equipment_utility_cart_e01/"
            f"{REVISION}/estimate"
        ).json()
        assert cart["adaptedPackage"] == {
            "bytes": None, "upperBoundBytes": 536870912, "known": False,
        }
        assert cart["acquisition"]["eligible"] is False


def test_search_cursor_pagination_and_bounded_errors():
    with TestClient(create_app()) as client:
        first = client.get("/api/v1/library/search", params={
            "q": "environment", "scope": "online", "sort": "name", "limit": 1,
        })
        assert first.status_code == 200 and first.json()["nextCursor"]
        second = client.get("/api/v1/library/search", params={
            "q": "environment", "scope": "online", "sort": "name", "limit": 4,
            "cursor": first.json()["nextCursor"],
        })
        assert second.status_code == 200
        assert second.json()["nextCursor"] is None
        assert len(first.json()["results"]) + len(second.json()["results"]) == 5

        invalid = client.get("/api/v1/library/search", params={
            "q": "environment", "scope": "online", "cursor": "not-a-cursor",
        })
        assert invalid.status_code == 400
        assert invalid.json()["detail"]["code"] == "invalid_cursor"
        mismatch = client.get("/api/v1/library/search", params={
            "q": "纸箱", "scope": "online", "cursor": first.json()["nextCursor"],
        })
        assert mismatch.status_code == 400
        assert mismatch.json()["detail"]["code"] == "cursor_mismatch"
        for limit in (0, 51):
            invalid_limit = client.get("/api/v1/library/search", params={
                "q": "环境", "scope": "online", "limit": limit,
            })
            assert invalid_limit.status_code == 400
            assert invalid_limit.json()["detail"]["code"] == "invalid_limit"


def test_imported_provenance_correlated_asset_appears_under_the_online_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(tmp_path / "state"))
    monkeypatch.setenv("SPATIAL_RECORDINGS_ROOT", str(tmp_path / "recordings"))
    with TestClient(create_app()) as client:
        job = upload(client, correlated_asset())
        assert job["assetVersion"] == "environment/cardboard-box@1"
        combined = client.get("/api/v1/library/search", params={
            "q": "纸箱", "scope": "all",
        }).json()
        assert [item["id"] for item in combined["results"]] == [BOX]
        installed = combined["results"][0]["installed"]
        assert installed["latest"] == "environment/cardboard-box@1"
        assert installed["versions"][0]["id"] == "environment/cardboard-box@1"
        local = client.get("/api/v1/library/search", params={
            "q": "纸箱", "scope": "local",
        }).json()
        assert [item["id"] for item in local["results"]] == ["environment/cardboard-box@1"]
        assert local["results"][0]["license"]["admitted"] is True
        recorded = {
            check["id"]: check["status"] for check in job["validation"]["checks"]
        }
        expected_dressing = all(
            recorded.get(identifier) == "pass" for identifier in ("S1", "S2", "S4", "S6")
        )
        assert local["results"][0]["usage"]["dressing"] is expected_dressing
        assert local["results"][0]["usage"]["staticCollision"] is None
        assert "staticCollision" in local["results"][0]["unknown"]


def test_missing_custom_catalog_keeps_legacy_and_local_search_working(tmp_path):
    missing = tmp_path / "missing-catalog.json"
    with TestClient(create_app(catalog_path=missing)) as client:
        assert client.get("/api/v1/library/assets").status_code == 200
        local = client.get("/api/v1/library/search", params={
            "q": "Franka", "scope": "local",
        })
        assert local.status_code == 200
        assert local.json()["snapshot"]["id"] is None
        assert {item["id"] for item in local.json()["results"]} == {"robot/fr3@1"}
        for scope in ("online", "all"):
            unavailable = client.get("/api/v1/library/search", params={
                "q": "Franka", "scope": scope,
            })
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"]["code"] == "catalog_unavailable"
        lookup = client.get(
            f"/api/v1/library/catalog/entries/simready/box_cardboard_a01/{REVISION}"
        )
        assert lookup.status_code == 503
        estimate = client.get(
            f"/api/v1/library/catalog/entries/simready/box_cardboard_a01/{REVISION}/estimate"
        )
        assert estimate.status_code == 503


def test_custom_catalog_path_is_loaded_and_reported(tmp_path):
    path = custom_catalog(tmp_path / "catalog.json")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with TestClient(create_app(catalog_path=path)) as client:
        result = client.get("/api/v1/library/search", params={
            "q": "夹具", "scope": "online",
        })
        assert result.status_code == 200
        body = result.json()
        assert body["snapshot"]["id"] == "sha256:" + digest
        assert body["snapshot"]["entryCount"] == 1
        assert [item["id"] for item in body["results"]] == [
            f"simready/fixture_widget@{REVISION}"
        ]
        assert body["results"][0]["title"] == "夹具部件"
        entry = client.get(
            f"/api/v1/library/catalog/entries/simready/fixture_widget/{REVISION}"
        )
        assert entry.status_code == 200
        assert entry.json()["names"]["en"] == "Fixture widget"


def test_lw_catalog_environment_override_is_used(tmp_path, monkeypatch):
    path = custom_catalog(tmp_path / "catalog.json")
    monkeypatch.setenv("LW_CATALOG", str(path))
    with TestClient(create_app()) as client:
        body = client.get("/api/v1/library/search", params={
            "q": "widget", "scope": "online",
        }).json()
        assert body["snapshot"]["entryCount"] == 1
        assert [item["id"] for item in body["results"]] == [
            f"simready/fixture_widget@{REVISION}"
        ]

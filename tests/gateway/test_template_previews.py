"""Preview describe and file reads retain the exact real template dependency identity."""

import copy
import json
from functools import partial

import pytest
import yaml
from fastapi.testclient import TestClient
from template_support import reference_files, scene_files, upload

from apps.api.app import main
from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import REPO_ROOT
from packages.library.previews import PreviewRepository
from packages.library.scenes import SceneLibrary
from tools.capture_scene_preview import capture


@pytest.fixture(autouse=True)
def isolate_bundled_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main, "PreviewRepository", partial(PreviewRepository, bundled_root=tmp_path / "empty-bundled")
    )


async def test_external_template_capture_refuses_changed_or_missing_real_assets_through_public_reads(
    tmp_path,
):
    cache = tmp_path / "previews"
    with TestClient(create_app(Settings(scene_previews_root=cache))) as client:
        asset = upload(client, reference_files()).json()
        assert upload(client, scene_files()[0]).status_code == 200
        version = client.app.state.manager.library.resolve("template-slice@1", launch=True)
        captured = await capture(version, "synthetic", cache)
        detail = client.get("/api/v1/library/scenes/template-slice/1").json()
        assert detail["preview"]["status"] == "available", detail
        manifest_url = detail["preview"]["documents"]["manifest"]
        assert client.get(manifest_url).status_code == 200
        path = client.app.state.library_assets.resolve(asset["assetVersion"]).path
        (path / "asset.usda").write_bytes((path / "asset.usda").read_bytes() + b"\n")
        launch = client.post("/api/v1/sessions", json={"sceneVersion": version.id, "source": "synthetic"})
        assert launch.status_code == 409
        unavailable = client.get("/api/v1/library/scenes/template-slice/1").json()
        assert unavailable["preview"]["status"] == "unavailable", unavailable["preview"]
        rejected = client.get(manifest_url)
        assert rejected.status_code == 404 and rejected.json()["detail"]["code"] == "preview_unavailable"
        assert captured["templateAssets"] == [
            {"id": asset["assetVersion"], "contentHash": asset["contentHash"]}
        ]
        assert not any(
            d["path"].startswith("library/assets/device/slice-reference-centrifuge/")
            for d in captured["dependencies"]
        )
        assert client.get("/api/v1/health").json()["sessions"] == []


async def test_builtin_template_capture_records_actual_library_files_and_rejects_partial_identity(tmp_path):
    cache = tmp_path / "previews"
    version = SceneLibrary(user_root=tmp_path / "empty-library").resolve("lab-demo@3", launch=True)
    captured = await capture(version, "synthetic", cache)
    expected = [
        {"id": item["id"], "contentHash": item["contentHash"]} for item in version.template_assets.values()
    ]
    assert sorted(captured["templateAssets"], key=lambda item: item["id"]) == sorted(
        expected, key=lambda item: item["id"]
    )
    actual_files = {name for item in version.template_assets.values() for name in item["files"].values()}
    recorded_files = {str(REPO_ROOT / dependency["path"]) for dependency in captured["dependencies"]}
    assert actual_files.issubset(recorded_files)
    index = next(cache.rglob("capture.json"))
    captured["templateAssets"] = captured["templateAssets"][:-1]
    index.write_text(json.dumps(captured), encoding="utf-8")
    with TestClient(create_app(Settings(scene_previews_root=cache))) as client:
        detail = client.get("/api/v1/library/scenes/lab-demo/3").json()
        assert detail["preview"]["status"] == "unavailable"
        response = client.get("/api/v1/library/scenes/lab-demo/3/preview/synthetic/manifest.json")
        assert response.status_code == 404


async def test_external_capture_identity_is_complete_exact_and_legacy_non_template_captures_remain_readable(
    tmp_path,
):
    cache = tmp_path / "previews"
    with TestClient(create_app(Settings(scene_previews_root=cache))) as client:
        asset = upload(client, reference_files()).json()
        assert upload(client, scene_files()[0]).status_code == 200
        version = client.app.state.manager.library.resolve("template-slice@1", launch=True)
        metadata = await capture(version, "synthetic", cache)
        pin = {"id": asset["assetVersion"], "contentHash": asset["contentHash"]}
        index = next(cache.rglob("capture.json"))
        url = "/api/v1/library/scenes/template-slice/1/preview/synthetic/manifest.json"
        for entries in [
            None,
            [],
            "invalid",
            [{"id": pin["id"]}],
            [pin | {"id": pin["id"].replace("@1", "@2")}],
            [pin | {"contentHash": "0" * 64}],
            [pin, pin],
            [pin, pin | {"contentHash": "f" * 64}],
        ]:
            changed = copy.deepcopy(metadata)
            if entries is None:
                changed.pop("templateAssets", None)
            else:
                changed["templateAssets"] = entries
            index.write_text(json.dumps(changed), encoding="utf-8")
            assert (
                client.get("/api/v1/library/scenes/template-slice/1").json()["preview"]["status"]
                == "unavailable"
            )
            assert client.get(url).status_code == 404
        index.write_text(json.dumps(metadata), encoding="utf-8")
        newer = reference_files()
        data = yaml.safe_load(newer["asset.yaml"])
        data["title"] = "A different immutable revision"
        newer["asset.yaml"] = yaml.safe_dump(data).encode()
        assert upload(client, newer).json()["assetVersion"].endswith("@2")
        assert client.get(url).status_code == 200
        path = client.app.state.library_assets.resolve(asset["assetVersion"]).path
        offline = tmp_path / "offline-asset"
        path.rename(offline)
        assert (
            client.get("/api/v1/library/scenes/template-slice/1").json()["preview"]["status"] == "unavailable"
        )
        assert client.get(url).status_code == 404
        offline.rename(path)
        assert client.get(url).status_code == 200
        legacy = client.app.state.manager.library.resolve("lab-demo@1", launch=True)
        legacy_metadata = await capture(legacy, "synthetic", cache)
        legacy_index = cache / "lab-demo/1" / legacy.content_hash / "synthetic/capture.json"
        legacy_metadata.pop("templateAssets", None)
        legacy_index.write_text(json.dumps(legacy_metadata), encoding="utf-8")
        detail = client.get("/api/v1/library/scenes/lab-demo/1").json()
        assert detail["preview"]["status"] == "available"
        assert client.get(detail["preview"]["documents"]["manifest"]).status_code == 200

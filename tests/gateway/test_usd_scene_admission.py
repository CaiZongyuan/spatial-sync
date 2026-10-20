"""Public authoring/admission and portable GLB seams remain engine-free in CI."""

from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from usd_library_support import import_fixture

from apps.api.app.main import create_app
from apps.api.app.recordings import freeze_manifest
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.assets.registry import AssetRegistry
from packages.library.scenes import visual_url
from packages.library.templates import TemplateError
from packages.protocol.schema import SceneManifest


def test_validated_usd_reaches_the_source_with_exact_scopes_and_drift_fails_before_allocation(tmp_path):
    allocated = []

    class SourceBoundary(SyntheticSource):
        async def start(self, scene_id, options):
            allocated.append((scene_id, self.scene_package.runtime_payload()))
            raise RuntimeError("Intentional engine-free Source boundary; no physics verdict")

    app = create_app(Settings(recordings_enabled=False), source_factory=lambda kind, sid: SourceBoundary(sid))
    with TestClient(app) as client:
        imported = import_fixture(client)
        report = imported["validation"]
        assert {row["id"] for row in report["checks"] if row["status"] == "pass"} >= {"S2", "S3", "S7"}
        assert len(report["effectiveBindings"]["newton"]) == 2
        assert client.post("/api/v1/sessions", json={"sceneVersion": "usd-library-fixture@1",
                                                    "source": "synthetic"}).status_code == 404
        response = client.post("/api/v1/sessions", json={"sceneVersion": "usd-library-fixture@1",
                                                       "source": "newton"})
        assert response.status_code == 503, response.text
        assert len(allocated) == 1
        scene_id, payload = allocated[0]
        assert scene_id == "stage.usda" and len(payload["assets"]) == 3
        assert {item["id"] for item in payload["assets"]} == {
            "environment/usd-fixture-room@1", "environment/usd-fixture-table@1", "labware/usd-fixture-tube@1",
        }
        assert {item["id"]: item["contentHash"] for item in payload["assets"]} == {
            item["id"]: item["contentHash"] for item in payload["expectedAssets"]
        }
        selected = app.state.manager.library.resolve("usd-library-fixture@1", launch=True)
        changed = selected.runtime_assets["environment/usd-fixture-room@1"]
        path = changed["files"]["visual.glb"]
        original = Path(path).read_bytes()
        try:
            Path(path).write_bytes(original + b"changed immutable bytes")
            refused = client.post("/api/v1/sessions", json={"sceneVersion": "usd-library-fixture@1",
                                                          "source": "newton"})
            assert refused.status_code == 409, refused.text
            assert refused.json()["detail"]["code"] == "scene_not_launchable"
            assert len(allocated) == 1 and app.state.manager.platform_sessions() == []
        finally:
            Path(path).write_bytes(original)


async def test_real_indexed_library_glbs_freeze_to_portable_exact_bytes_and_refuse_drift(tmp_path):
    app = create_app(Settings(recordings_enabled=False))
    with TestClient(app) as client:
        import_fixture(client)
        selected = app.state.manager.library.resolve("usd-library-fixture@1", launch=True)
        snapshots = list(selected.runtime_assets.values())
        urls = [visual_url(snapshot, snapshot["metadata"]["visual"]) for snapshot in snapshots]
        node = {"entityKey": "/World/Dressing/Room", "kind": "static", "name": "Room",
                "initialPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}, "visuals": [
                    {"kind": "glb", "url": url, "assetUpAxis": "Z", "scale": [1, 1, 1],
                     "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}} for url in urls]}
        manifest = SceneManifest.model_validate({
            "sessionId": "portable-seam", "sceneRevision": "revision", "revisionNumericId": 1,
            "sceneName": "Library visual freeze seam", "epoch": 0,
            "source": {"kind": "newton", "solver": "fixture", "physicsDt": .01,
                       "basePublishHz": 30., "realtimeFactor": 1.}, "nodes": [node],
        })
        before = {url: client.get(url).content for url in urls}
        frozen, files = await freeze_manifest(manifest, object(), AssetRegistry.from_file(),
                                               library_reader=selected.read_visual)
        assert len(files) == 3
        for visual, url in zip(frozen["nodes"][0]["visuals"], urls, strict=True):
            assert visual["url"] == "assets/sha256-" + sha256(before[url]).hexdigest() + ".glb"
            assert files[visual["url"]] == before[url] == selected.read_visual(url)
        for url in ("/api/v1/library/files/user/../secret.glb", urls[0] + "/../asset.yaml",
                    urls[0].replace("visual.glb", "asset.usda"), "https://example.invalid/visual.glb"):
            with pytest.raises(KeyError):
                selected.read_visual(url)
        target = Path(snapshots[0]["files"]["visual.glb"])
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"drift")
            with pytest.raises(TemplateError, match="changed"):
                selected.read_visual(urls[0])
        finally:
            target.write_bytes(original)

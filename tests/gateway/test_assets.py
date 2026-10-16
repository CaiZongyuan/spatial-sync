from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.assets.registry import AssetRegistry


def test_registered_asset_mime_content_hash_cache_and_missing_runtime_file(tmp_path):
    path = tmp_path / "cover.glb"
    path.write_bytes(b"glTF fixture bytes")
    registry = AssetRegistry({"cover.glb": "cover.glb"}, root=tmp_path)
    with TestClient(create_app(asset_registry=registry)) as client:
        response = client.get("/api/v1/assets/cover.glb")
        assert response.status_code == 200 and response.content == b"glTF fixture bytes"
        assert response.headers["content-type"] == "model/gltf-binary"
        assert "max-age=" in response.headers["cache-control"]
        assert (
            client.get(
                "/api/v1/assets/cover.glb", headers={"If-None-Match": response.headers["etag"]}
            ).status_code
            == 304
        )
        for asset_id in ["unregistered.glb", "%2e%2e/private.glb", "C:%5CWindows%5Cprivate.glb"]:
            assert client.get("/api/v1/assets/" + asset_id).status_code == 404
        path.unlink()
        assert client.get("/api/v1/assets/cover.glb").status_code == 404


def test_original_y_up_glb_is_registered_as_a_real_binary_asset():
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/assets/equipment-cover.glb")
        assert response.status_code == 200 and response.content[:4] == b"glTF"
        assert len(response.content) == 988
        assert client.get("/api/v1/assets/missing-moving-part.glb").status_code == 404

"""Static thumbnails are bounded derived images, independent of simulation sessions."""

import base64

from fastapi.testclient import TestClient
from test_asset_imports import package_files, upload

from apps.api.app.main import create_app

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNoaGj4DwAFhAKAjM1mJgAAAABJRU5ErkJggg=="
)


def path(value):
    return (
        "/api/v1/library/thumbnails?resource=asset&id="
        + value["assetVersion"]
        + "&contentHash="
        + value["contentHash"]
    )


def test_thumbnail_is_persistent_and_invalidated_by_package_changes(tmp_path):
    app = create_app()
    with TestClient(app) as client:
        value = upload(client).json()
        url = path(value)
        assert client.get(url).status_code == 404
        response = client.post(url, content=PNG, headers={"Content-Type": "image/png"})
        assert response.status_code == 200, response.text
        assert client.get(url).content == PNG
        (tmp_path / "unrelated-state.json").write_text("{}", encoding="utf-8")
        assert client.get(url).content == PNG
        assert client.get(response.json()["thumbnailUrl"]).content == PNG
        assert client.get("/api/v1/health").json()["sessionCount"] == 0
    with TestClient(create_app()) as client:
        assert client.get(url).content == PNG
        package = client.app.state.library_assets.resolve(value["assetVersion"]).path
        package.joinpath("asset.usda").write_bytes(b"changed package")
        assert client.get(url).status_code == 404
        assert client.get("/api/v1/library/assets/labware/test-tube/1").status_code == 409


def test_invalid_image_and_cache_eviction_do_not_change_assets(monkeypatch):
    import packages.library.thumbnails as thumbnails

    monkeypatch.setattr(thumbnails, "MAX_ENTRIES", 1)
    with TestClient(create_app()) as client:
        first = upload(client).json()
        assert client.post(path(first), content=b"not a PNG").status_code == 422
        assert client.post(path(first), content=PNG).status_code == 200
        second = upload(client, package_files() | {"NOTICE": b"second version"}).json()
        assert client.post(path(second), content=PNG).status_code == 200
        assert client.get(path(first)).status_code == 404
        assert client.get(path(second)).content == PNG
        assert client.get("/api/v1/library/assets/labware/test-tube/1").status_code == 200


def test_thumbnail_cache_does_not_watch_web_source_tree(tmp_path):
    import json

    with TestClient(create_app()) as client:
        value = upload(client).json()
        url = path(value)
        response = client.post(url, content=PNG, headers={"Content-Type": "image/png"})
        assert response.status_code == 200, response.text
        index_files = list((client.app.state.settings.state_root / "library-thumbnails").glob("*.json"))
        assert len(index_files) == 1
        recorded_paths = json.loads(index_files[0].read_bytes())["paths"]
        assert not any(
            "apps/web/src" in item.replace("\\", "/") or item.endswith("pnpm-lock.yaml")
            for item in recorded_paths
        )
        # Adding another package or touching the library root must not invalidate existing thumbnails.
        user_root = client.app.state.library_assets.roots["user"]
        marker = user_root / ".unrelated-lock-touch"
        marker.write_text("x", encoding="utf-8")
        marker.unlink()
        assert client.get(url).content == PNG

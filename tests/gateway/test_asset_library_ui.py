"""The list contains real package metadata required by library consumers."""
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app


def test_asset_library_list_projects_dimensions_and_types_from_the_immutable_package():
    package = Path(__file__).resolve().parents[2] / "library/assets/robot/fr3/1/asset.yaml"
    metadata = yaml.safe_load(package.read_text(encoding="utf-8"))
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/library/assets", params={"kind": "robot", "q": "Franka", "gaps": True})
        assert response.status_code == 200
        asset = next(item for item in response.json()["assets"] if item["id"] == "robot/fr3@1")
        assert asset["dimensionsM"] == metadata["dimensionsM"]
        assert asset["implementedTypes"] == [item["type"] for item in metadata["implements"]]
        assert {item["id"] for item in asset["completeness"]["items"]} == {
            "collision", "massInertia", "visual", "license", "jointSites", "types",
        }
        detail = client.get("/api/v1/library/assets/robot/fr3/1").json()
        assert detail["dimensionsM"] == asset["dimensionsM"]
        assert detail["implementedTypes"] == asset["implementedTypes"]
        assert client.get(asset["visualUrl"]).content[:4] == b"glTF"

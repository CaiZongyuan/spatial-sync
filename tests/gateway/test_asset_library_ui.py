"""The list contains real package metadata required by library consumers."""
import hashlib
import io
import json
import tarfile
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.library.validation import StructuralValidator


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
        assert detail["referenceInspection"]["complete"]
        assert detail["referencingScenes"] == []
        assert client.get(asset["visualUrl"]).content[:4] == b"glTF"


def upload(client, files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            archive.addfile(entry, io.BytesIO(content))
    response = client.post("/api/v1/library/imports", content=output.getvalue(),
                           headers={"Content-Type": "application/x-tar"})
    assert response.status_code == 200, response.text
    return response.json()


def reference_asset(description):
    stage = b'#usda 1.0\n(defaultPrim = "Asset"\n upAxis = "Z"\n metersPerUnit = 1)\ndef Xform "Asset" {}\n'
    return {
        "asset.yaml": yaml.safe_dump({"id": "labware/reference-fixture", "kind": "labware",
                                     "title": "Reference fixture", "description": description,
                                     "stage": "asset.usda"}).encode(),
        "asset.usda": stage,
        "LICENSE.txt": b"CC0-1.0; authored for tests",
        "provenance.json": json.dumps({"files": {"asset.usda": {
            "sha256": hashlib.sha256(stage).hexdigest(), "bytes": len(stage),
        }}}).encode(),
    }


def reference_scene(name, body, template=None):
    return {
        "scene.yaml": yaml.safe_dump({"name": name, "title": name, "stage": "stage.usda",
                                     "sources": ["newton"]}).encode(),
        "stage.usda": ('#usda 1.0\n(defaultPrim = "World"\n upAxis = "Z"\n metersPerUnit = 1)\n'
                       'def Xform "World" {\ndef Xform "Lab" {\n' + body + '\n}\n}\n').encode(),
        "devices.yaml": yaml.safe_dump({"scene": name, "entities": [
            {"key": "/World/Lab/Placed", "template": template},
        ] if template else []}).encode(),
        "locations.yaml": b"locations: {}\n",
    }


def test_reverse_references_use_real_usd_reports_and_templates_without_read_side_effects(tmp_path):
    # A legacy operator-installed USD scene has no report: it must remain unknown.
    legacy = tmp_path / "user-library/scenes/legacy-uninspected/1"
    legacy.mkdir(parents=True)
    for name, content in reference_scene("legacy-uninspected", 'def Xform "Placed" {}').items():
        (legacy / name).write_bytes(content)
    with TestClient(create_app()) as client:
        first = upload(client, reference_asset("first"))["assetVersion"]
        second = upload(client, reference_asset("second"))["assetVersion"]
        assert first.endswith("@1") and second.endswith("@2")
        arc = ('def Xform "Placed" (prepend references = '
               '@library:assets/labware/reference-fixture/1/asset.usda@</Asset>) {}')
        upload(client, reference_scene("actual-usd-reference", arc))
        upload(client, reference_scene("actual-template-reference", 'def Xform "Placed" {}', second))
        missing = '\ndef Xform "Missing" (prepend references = '
        upload(client, reference_scene("partial-known-reference", arc + missing
                                      + '@library:assets/labware/absent/1/asset.usda@</Asset>) {}'))
        comment = '# @library:assets/labware/reference-fixture/1/asset.usda@\n'
        upload(client, reference_scene("comment-is-not-reference", comment + 'def Xform "Placed" {}'))
        with patch.object(StructuralValidator, "run", side_effect=AssertionError("GET started validation")):
            one = client.get("/api/v1/library/assets/labware/reference-fixture/1").json()
            two = client.get("/api/v1/library/assets/labware/reference-fixture/2").json()
        assert {scene["id"] for scene in one["referencingScenes"]} == {
            "actual-usd-reference@1", "partial-known-reference@1",
        }
        assert {scene["id"] for scene in two["referencingScenes"]} == {"actual-template-reference@1"}
        assert not one["referenceInspection"]["complete"]
        unknown = {scene["id"] for scene in one["referenceInspection"]["uninspectedScenes"]}
        assert {"legacy-uninspected@1", "partial-known-reference@1"}.issubset(unknown)
        report = client.get("/api/v1/library/scenes/actual-usd-reference/1").json()["validation"]
        assert report["referencedAssets"] == [{"id": first, "contentHash": one["contentHash"]}]
        assert all(scene["contentHash"] for scene in one["referencingScenes"])
        indexed = client.app.state.manager.library.resolve("actual-usd-reference@1")
        indexed.reports.save(report | {"referencedAssets": [{"id": first, "contentHash": "0" * 64}]},
                             indexed.content_hash)
        mismatched = client.get("/api/v1/library/assets/labware/reference-fixture/1").json()
        assert "actual-usd-reference@1" not in {scene["id"] for scene in mismatched["referencingScenes"]}
        assert any(scene["id"] == "actual-usd-reference@1" and "hash differs" in scene["reason"]
                   for scene in mismatched["referenceInspection"]["uninspectedScenes"])
        (indexed.path / "stage.usda").write_bytes(b"changed after indexing")
        changed = client.get("/api/v1/library/assets/labware/reference-fixture/1").json()
        assert not changed["referenceInspection"]["complete"]
        assert any(scene["id"] == "actual-usd-reference@1" and "changed after indexing" in scene["reason"]
                   for scene in changed["referenceInspection"]["uninspectedScenes"])

"""Template declarations cross the actual upload, CPU worker, Synthetic and Catalog seams."""

import copy
import hashlib
import io
import json
import pickle
import tarfile

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from packages.library.common import BUILTIN_ROOT
from packages.protocol.schema import SceneManifest
from packages.sim_runtime.info_catalog import load_info_catalog


def upload(client, files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, value in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(value)
            archive.addfile(member, io.BytesIO(value))
    return client.post(
        "/api/v1/library/imports", content=stream.getvalue(), headers={"Content-Type": "application/x-tar"}
    )


def reference_files():
    metadata = {
        "id": "device/slice-reference-centrifuge",
        "kind": "device",
        "title": "Binding reference centrifuge",
        "description": "Synthetic housing proxy; collision, mass and license are not supplied.",
        "stage": "asset.usda",
        "implements": [
            {
                "type": "centrifuge/v1",
                "programs": {"synthetic": "centrifuge.virtual", "newton": "centrifuge.newton"},
                "config": {"slotSites": [f"Rotor/Slot{i}" for i in range(1, 5)], "visualRpmCap": 55},
            }
        ],
    }
    files = {
        "asset.yaml": yaml.safe_dump(metadata).encode(),
        "asset.usda": b"""#usda 1.0
(defaultPrim = "Asset"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "Asset" {
 def Cube "HousingProxy" {
  double size = 1
  float3 xformOp:scale = (0.44, 0.44, 0.1)
  uniform token[] xformOpOrder = ["xformOp:scale"]
 }
 def Xform "Rotor" {
  def Xform "Slot1" {}
  def Xform "Slot2" {}
  def Xform "Slot3" {}
  def Xform "Slot4" {}
 }
}
""",
    }
    files["source/geometry.json"] = json.dumps(
        {
            "source": "packages/sim_runtime/synthetic_lab.py",
            "sourceRevision": "dedacd59a4aa0f13ba32a35e333a2d0922538e55",
            "entityKey": "/World/Lab/Centrifuge/Housing",
            "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]},
            "primitive": {"type": "box", "halfExtents": [0.22, 0.22, 0.05]},
            "scope": "Housing proxy only; station names are declarations, not validated physical placement.",
        }
    ).encode()
    files["provenance.json"] = json.dumps(
        {
            "files": {
                name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                for name, value in files.items()
            }
        }
    ).encode()
    return files


def scene_files():
    path = BUILTIN_ROOT / "scenes/lab-demo/2"
    files = {name: (path / name).read_bytes() for name in ("scene.yaml", "devices.yaml", "locations.yaml")}
    metadata = yaml.safe_load(files["scene.yaml"])
    metadata["name"] = "template-slice"
    declarations = yaml.safe_load(files["devices.yaml"])
    declarations["scene"] = metadata["name"]
    centrifuge = next(
        binding for binding in declarations["entities"] if binding.get("type") == "centrifuge/v1"
    )
    centrifuge["template"] = "device/slice-reference-centrifuge@1"
    del centrifuge["type"], centrifuge["program"], centrifuge["config"]["slotSites"]
    centrifuge["config"]["visualRpmCap"] = 61
    centrifuge["alerts"] = {
        "slice-running": {
            "when": {"property": "phase", "equals": "running"},
            "severity": "info",
            "message": "Template is running",
        }
    }
    files["scene.yaml"] = yaml.safe_dump(metadata).encode()
    files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    return files, declarations


def test_one_template_has_the_same_effective_binding_in_worker_describe_and_synthetic_catalog():
    with TestClient(create_app()) as client:
        asset = upload(client, reference_files())
        assert asset.status_code == 200, asset.text
        reference = asset.json()
        before = client.get("/api/v1/library/assets/device/slice-reference-centrifuge/1").json()
        files, raw = scene_files()
        imported = upload(client, files)
        assert imported.status_code == 200, imported.text
        report = imported.json()["validation"]
        assert next(item for item in report["checks"] if item["id"] == "S5")["status"] == "pass", report
        detail = client.get("/api/v1/library/scenes/template-slice/1").json()
        assert detail["rawBindings"] == raw
        assert detail["templateResolution"]["status"] == "resolved", detail
        effective = {binding["key"]: binding for binding in detail["effectiveBindings"]["synthetic"]}
        binding = effective["/World/Lab/Centrifuge"]
        assert binding["program"] == "centrifuge.virtual"
        assert binding["config"]["visualRpmCap"] == 61
        assert binding["config"]["slotSites"] == [f"/World/Lab/Centrifuge/Rotor/Slot{i}" for i in range(1, 5)]
        assert detail["assets"] == [
            {"id": reference["assetVersion"], "contentHash": reference["contentHash"]}
        ]
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launch.status_code == 201, launch.text
        session_id = launch.json()["sessionId"]
        catalog = client.get(f"/api/v1/sessions/{session_id}/catalog").json()
        public = copy.deepcopy(binding)
        public.pop("alerts", None)
        assert catalog["entities"]["/World/Lab/Centrifuge"] == public
        actual = client.app.state.manager.get(session_id).source.catalog
        assert (
            actual.alert_rules()["/World/Lab/Centrifuge"]["slice-running"]["message"] == "Template is running"
        )
        assert actual.conditions("/World/Lab/Centrifuge", "closeLid")[-1]["entity"] == "/World/Lab/RobotA"
        after = client.get("/api/v1/library/assets/device/slice-reference-centrifuge/1").json()
        assert after["contentHash"] == before["contentHash"]
        assert after["metadata"] == before["metadata"]


def test_the_same_pinned_context_survives_native_process_serialization_and_both_catalog_consumers():
    path = BUILTIN_ROOT / "scenes/robot-and-tubes/1"
    files = {name: (path / name).read_bytes() for name in ("scene.yaml", "devices.yaml", "locations.yaml")}
    metadata = yaml.safe_load(files["scene.yaml"])
    metadata["name"] = "template-native-slice"
    declarations = yaml.safe_load(files["devices.yaml"])
    declarations["scene"] = metadata["name"]
    centrifuge = next(b for b in declarations["entities"] if b.get("type") == "centrifuge/v1")
    centrifuge["template"] = "device/slice-reference-centrifuge@1"
    del centrifuge["type"], centrifuge["program"], centrifuge["config"]["slotSites"]
    centrifuge["alerts"] = {
        "slice-running": {
            "when": {"property": "phase", "equals": "running"},
            "severity": "info",
            "message": "Template is running",
        }
    }
    files["scene.yaml"] = yaml.safe_dump(metadata).encode()
    files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    with TestClient(create_app()) as client:
        assert upload(client, reference_files()).status_code == 200
        imported = upload(client, files)
        assert imported.status_code == 200, imported.text
        checks = {item["id"]: item for item in imported.json()["validation"]["checks"]}
        assert checks["S5"]["status"] == checks["S8"]["status"] == "pass", checks
        selected = client.app.state.manager.library.resolve(
            "template-native-slice@1", launch=True, source="newton"
        )
        restored = pickle.loads(pickle.dumps(selected))
        assert selected.template_assets == restored.template_assets
        # Frozen real Manifest is input to the actual gateway/Runner Catalog function;
        # this test exercises serialization and both consumers without starting an engine.
        capture_root = BUILTIN_ROOT.parent / "assets/scene-previews/robot-and-tubes/1"
        manifest = SceneManifest.model_validate_json(
            next(capture_root.glob("*/newton/manifest.json")).read_bytes()
        )
        gateway = load_info_catalog(manifest, selected.name, scene_package=selected)
        runner = load_info_catalog(manifest, restored.name, scene_package=restored)
        assert gateway.model_dump(mode="json") == runner.model_dump(mode="json")
        assert gateway.alert_rules() == runner.alert_rules()
        detail = client.get("/api/v1/library/scenes/template-native-slice/1").json()
        effective = {
            b["key"]: {k: v for k, v in b.items() if k != "alerts"}
            for b in detail["effectiveBindings"]["newton"]
        }
        assert gateway.entities == effective


@pytest.mark.parametrize(
    "fault,word",
    [
        ("unknown", "Unknown asset"),
        ("source", "found 0"),
        ("ambiguous", "found 2"),
        ("relative", "relative to /Asset"),
    ],
)
def test_invalid_template_remains_inspectable_and_blocks_public_launch(fault, word):
    asset_files = reference_files()
    asset_metadata = yaml.safe_load(asset_files["asset.yaml"])
    if fault == "source":
        asset_metadata["implements"][0]["programs"].pop("synthetic")
    elif fault == "ambiguous":
        asset_metadata["implements"].append(
            {"type": "env-sensor/v1", "programs": {"synthetic": "env_sensor.virtual"}, "config": {}}
        )
    elif fault == "relative":
        asset_metadata["implements"][0]["config"]["slotSites"][0] = "../outside"
    asset_files["asset.yaml"] = yaml.safe_dump(asset_metadata).encode()
    files, declarations = scene_files()
    if fault == "unknown":
        next(b for b in declarations["entities"] if b.get("template"))["template"] = "device/missing@1"
        files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    with TestClient(create_app()) as client:
        assert upload(client, asset_files).status_code == 200
        before = client.get("/api/v1/library/assets/device/slice-reference-centrifuge/1").json()
        imported = upload(client, files)
        assert imported.status_code == 200, imported.text
        failed = next(c for c in imported.json()["validation"]["checks"] if c["id"] == "S5")
        assert failed["status"] == "fail" and word in failed["message"], failed
        detail = client.get("/api/v1/library/scenes/template-slice/1")
        assert detail.status_code == 200 and detail.json()["templateResolution"]["status"] == "failed"
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launch.status_code == 409 and launch.json()["detail"]["code"] == "scene_not_launchable"
        assert client.get("/api/v1/health").json()["sessions"] == []
        assert (
            client.get("/api/v1/library/assets/device/slice-reference-centrifuge/1").json()["metadata"]
            == before["metadata"]
        )


def test_published_dependency_hash_cannot_silently_retarget_after_restart_or_revalidation():
    with TestClient(create_app()) as client:
        assert upload(client, reference_files()).status_code == 200
        imported = upload(client, scene_files()[0])
        assert imported.status_code == 200, imported.text
        original_hash = imported.json()["contentHash"]
        path = (
            client.app.state.library_assets.resolve("device/slice-reference-centrifuge@1").path / "asset.usda"
        )
    path.write_bytes(path.read_bytes() + b"\n")
    with TestClient(create_app()) as client:
        detail = client.get("/api/v1/library/scenes/template-slice/1").json()
        assert detail["contentHash"] == original_hash and not detail["launchable"]
        assert detail["templateResolution"]["status"] == "failed"
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 200, checked.text
        assert not checked.json()["launchable"]
        assert any(
            c["id"] == "S5" and "immutable asset changed" in c["message"] for c in checked.json()["checks"]
        )
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launch.status_code == 409
        assert client.get("/api/v1/health").json()["sessions"] == []


@pytest.mark.parametrize("fault", ["source", "ambiguous"])
def test_failed_template_selection_still_pins_known_asset_bytes(fault):
    files = reference_files()
    metadata = yaml.safe_load(files["asset.yaml"])
    if fault == "source":
        metadata["implements"][0]["programs"].pop("synthetic")
    else:
        metadata["implements"].append(copy.deepcopy(metadata["implements"][0]))
    files["asset.yaml"] = yaml.safe_dump(metadata).encode()
    with TestClient(create_app()) as client:
        asset = upload(client, files).json()
        imported = upload(client, scene_files()[0])
        assert imported.status_code == 200, imported.text
        report = imported.json()["validation"]
        assert next(c for c in report["checks"] if c["id"] == "S5")["status"] == "fail"
        pinned = [{"id": asset["assetVersion"], "contentHash": asset["contentHash"]}]
        assert report["referencedAssets"] == pinned
        path = client.app.state.library_assets.resolve(asset["assetVersion"]).path / "asset.yaml"
    # Repairing the declaration in place is still a different immutable package.
    path.write_bytes(reference_files()["asset.yaml"])
    with TestClient(create_app()) as client:
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 200, checked.text
        report = checked.json()
        assert not report["launchable"]
        assert report["referencedAssets"] == pinned
        assert any("immutable asset changed" in c["message"] for c in report["checks"])
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launch.status_code == 409
        assert client.get("/api/v1/health").json()["sessions"] == []


def test_malformed_template_is_rejected_before_a_scene_revision_is_published():
    files, declarations = scene_files()
    next(b for b in declarations["entities"] if b.get("template"))["template"] = "device/missing"
    files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    with TestClient(create_app()) as client:
        imported = upload(client, files)
        assert imported.status_code == 422, imported.text
        assert imported.json()["detail"]["code"] == "import_invalid"
        assert client.get("/api/v1/library/scenes/template-slice/1").status_code == 404


def test_a_previously_unknown_dependency_can_only_pin_the_first_bytes_it_actually_inspects():
    with TestClient(create_app()) as client:
        imported = upload(client, scene_files()[0])
        assert imported.status_code == 200, imported.text
        assert not imported.json()["validation"]["launchable"]
        assert imported.json()["validation"]["referencedAssets"] == []
        asset = upload(client, reference_files()).json()
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 200 and checked.json()["launchable"], checked.text
        assert checked.json()["referencedAssets"] == [
            {"id": asset["assetVersion"], "contentHash": asset["contentHash"]}
        ]
        launched = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launched.status_code == 201, launched.text


@pytest.mark.parametrize("missing_first", [False, True])
def test_a_later_discovered_dependency_stays_pinned_alongside_the_original_known_dependency(
    tmp_path, missing_first
):
    files, declarations = scene_files()
    incubator = next(b for b in declarations["entities"] if b.get("type") == "incubator/v1")
    incubator.pop("type")
    incubator.pop("program")
    incubator["template"] = "device/later-reference@1"
    files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    later_files = reference_files()
    metadata = yaml.safe_load(later_files["asset.yaml"])
    metadata["id"] = "device/later-reference"
    metadata["implements"] = [{"type": "incubator/v1", "programs": {"synthetic": "incubator.virtual"}}]
    later_files["asset.yaml"] = yaml.safe_dump(metadata).encode()
    with TestClient(create_app()) as client:
        original = upload(client, reference_files()).json()
        imported = upload(client, files)
        assert imported.status_code == 200, imported.text
        assert imported.json()["validation"]["referencedAssets"] == [
            {"id": original["assetVersion"], "contentHash": original["contentHash"]}
        ]
        later = upload(client, later_files).json()
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 200 and checked.json()["launchable"], checked.text
        pinned = checked.json()["referencedAssets"]
        assert {item["id"] for item in pinned} == {original["assetVersion"], later["assetVersion"]}
        path = client.app.state.library_assets.resolve(later["assetVersion"]).path / "asset.usda"
    if missing_first:
        offline = tmp_path / "offline-asset"
        path.parent.rename(offline)
        with TestClient(create_app()) as client:
            checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
            assert checked.status_code == 200 and not checked.json()["launchable"]
            assert checked.json()["referencedAssets"] == pinned
        offline.rename(path.parent)
    path.write_bytes(path.read_bytes() + b"\n")
    with TestClient(create_app()) as client:
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": "template-slice@1", "source": "synthetic"}
        )
        assert launch.status_code == 409, launch.text
        checked = client.post("/api/v1/library/scenes/template-slice/1/validate")
        assert checked.status_code == 200 and not checked.json()["launchable"]
        assert checked.json()["referencedAssets"] == pinned

"""Real USD identity, provenance and namespace checks, without a physics engine."""

import hashlib
import json

import pytest
from pxr import Usd, UsdGeom, UsdPhysics

from apps.api.app.library_validation import program_schemas
from packages.library.common import TYPE_ROOT, document
from packages.library.structural import validate


def payload(tmp_path, *, kind="scene"):
    path = tmp_path / "stage.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, "/World/Lab" if kind == "scene" else "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    data = {
        "kind": kind,
        "subject": f"{kind}:" + ("structure-fixture@1" if kind == "scene" else "labware/fixture@1"),
        "metadata": {"stage": path.name},
        "assets": [],
        "types": {},
        "programs": {},
        "package": {"path": str(tmp_path), "files": {path.name: str(path)}},
    }
    return data, stage


@pytest.mark.parametrize(
    "path", ["/World/Outside", "/Other/Body", "/World/Dressing/Body", "/World/Lab/机器人"]
)
def test_physical_entities_require_ascii_lab_identity_and_cannot_be_dressing(tmp_path, path):
    data, stage = payload(tmp_path)
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(prim)
    stage.GetRootLayer().Save()
    report = validate(data)
    failure = next(check for check in report["checks"] if check["id"] == "S3")
    assert failure["status"] == "fail" and path in failure["message"]


def test_lab_physics_and_dressing_without_physics_are_separate(tmp_path):
    data, stage = payload(tmp_path)
    UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(stage, "/World/Lab/Body").GetPrim())
    UsdGeom.Cube.Define(stage, "/World/Dressing/Visual")
    stage.GetRootLayer().Save()
    report = validate(data)
    assert next(check for check in report["checks"] if check["id"] == "S3")["status"] == "pass"
    assert next(check for check in report["checks"] if check["id"] == "S6")["status"] == "warn"


@pytest.mark.parametrize("target,allowed", [
    ("/World/Lab/Marker", True), ("/World/Dressing/Room/Marker", False),
    ("/Other/Marker", False), ("/Asset/Marker", False),
])
def test_scene_config_sites_require_lab_domain_even_when_other_root_prims_exist(tmp_path, target, allowed):
    data, stage = payload(tmp_path)
    UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(stage, "/World/Lab/Tube").GetPrim())
    UsdGeom.Xform.Define(stage, target)
    stage.GetRootLayer().Save()
    data["metadata"]["sources"] = ["newton"]
    data["types"] = {value["type"]: value for file in TYPE_ROOT.glob("*.yaml") if (value := document(file))}
    data["programs"] = program_schemas()
    data["bindings"] = {"scene": "structure-fixture", "entities": [{
        "key": "/World/Lab/Tube", "type": "tube/v1", "program": "tube.newton",
        "config": {"robots": [], "locationSites": [target]},
    }]}
    report = validate(data)
    check = next(row for row in report["checks"] if row["id"] == "S5")
    assert check["status"] == ("pass" if allowed else "fail")
    if not allowed:
        assert target in check["message"] and "/World/Lab" in check["message"]


def test_asset_template_sites_keep_their_own_asset_domain(tmp_path):
    data, stage = payload(tmp_path, kind="asset")
    UsdGeom.Xform.Define(stage, "/Asset/Marker")
    stage.GetRootLayer().Save()
    data["types"] = {value["type"]: value for file in TYPE_ROOT.glob("*.yaml") if (value := document(file))}
    data["programs"] = program_schemas()
    data["metadata"]["implements"] = [{"type": "tube/v1", "programs": {"newton": "tube.newton"},
                                       "config": {"robots": [], "locationSites": ["Marker"]}}]
    report = validate(data)
    assert next(row for row in report["checks"] if row["id"] == "S5")["status"] == "pass"


def test_instance_proxies_cannot_hide_physics_under_dressing(tmp_path):
    asset_path = tmp_path / "instance.usda"
    asset = Usd.Stage.CreateNew(str(asset_path))
    root = UsdGeom.Xform.Define(asset, "/Asset")
    asset.SetDefaultPrim(root.GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(asset, "/Asset/Body").GetPrim())
    asset.GetRootLayer().Save()
    data, stage = payload(tmp_path)
    reference = UsdGeom.Xform.Define(stage, "/World/Dressing/Instance").GetPrim()
    reference.GetReferences().AddReference(asset_path.name)
    reference.SetInstanceable(True)
    stage.GetRootLayer().Save()
    data["package"]["files"][asset_path.name] = str(asset_path)
    report = validate(data)
    failure = next(check for check in report["checks"] if check["id"] == "S3")
    assert failure["status"] == "fail" and "/World/Dressing/Instance/Body" in failure["message"]


def test_asset_requires_asset_root_and_default_prim(tmp_path):
    data, stage = payload(tmp_path, kind="asset")
    stage.ClearDefaultPrim()
    stage.GetRootLayer().Save()
    report = validate(data)
    assert next(check for check in report["checks"] if check["id"] == "S3")["status"] == "fail"


@pytest.mark.parametrize("changed", [False, True])
def test_provenance_package_local_hashes_are_verified(tmp_path, changed):
    data, stage = payload(tmp_path, kind="asset")
    stage.GetRootLayer().Save()
    source = tmp_path / "source.txt"
    source.write_bytes(b"authored input")
    provenance = tmp_path / "provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "files": {
                    source.name: {
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "bytes": source.stat().st_size,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    license_file = tmp_path / "LICENSE"
    license_file.write_text("CC0-1.0", encoding="utf-8")
    data["package"]["files"] |= {path.name: str(path) for path in (source, provenance, license_file)}
    if changed:
        source.write_bytes(b"different input")
    report = validate(data)
    assert next(check for check in report["checks"] if check["id"] == "S4")["status"] == (
        "fail" if changed else "pass"
    )

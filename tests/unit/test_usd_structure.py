"""Real USD identity, provenance and namespace checks, without a physics engine."""

import hashlib
import json

import pytest
from pxr import Usd, UsdGeom, UsdPhysics

from packages.library.structural import validate


def payload(tmp_path, *, kind="scene"):
    path = tmp_path / "stage.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    root = UsdGeom.Xform.Define(stage, "/World/Lab" if kind == "scene" else "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    data = {"kind": kind, "subject": f"{kind}:" + ("structure-fixture@1" if kind == "scene" else "labware/fixture@1"),
            "metadata": {"stage": path.name}, "assets": [], "types": {}, "programs": {},
            "package": {"path": str(tmp_path), "files": {path.name: str(path)}}}
    return data, stage


@pytest.mark.parametrize("path", ["/World/Outside", "/Other/Body", "/World/Dressing/Body", "/World/Lab/机器人"])
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
    provenance.write_text(json.dumps({"files": {source.name: {
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "bytes": source.stat().st_size,
    }}}), encoding="utf-8")
    license_file = tmp_path / "LICENSE"
    license_file.write_text("CC0-1.0", encoding="utf-8")
    data["package"]["files"] |= {path.name: str(path) for path in (source, provenance, license_file)}
    if changed:
        source.write_bytes(b"different input")
    report = validate(data)
    assert next(check for check in report["checks"] if check["id"] == "S4")["status"] == ("fail" if changed else "pass")

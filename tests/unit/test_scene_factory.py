"""SF0 support boundary: pinned source, Newton variant and the existing import."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from factory import support as support_schema
from factory.fixture import build_source
from factory.simready import (
    REVISION,
    SOURCE_PACKAGES,
    adapt,
    import_package,
)


def packaged(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    output = tmp_path / "adapted"
    report = adapt(candidate, source, output)
    assert report["status"] == "pass", report["gaps"]
    return candidate, source, output, report


def refresh_bom(source, candidate):
    bom_path = source / ".metadata/com.nvidia.simready.packaging.bom.json"
    bom = json.loads(bom_path.read_text(encoding="utf-8"))
    for item in bom["items"]:
        value = (source / item["relative_path"]).read_bytes()
        item["size"] = len(value)
        item["hash"]["sha256"] = hashlib.sha256(value).hexdigest()
    bom_path.write_text(json.dumps(bom, indent=2) + "\n", encoding="utf-8")
    files = [path for path in source.rglob("*") if path.is_file()]
    return replace(
        candidate,
        expected_files=len(files),
        expected_bytes=sum(path.stat().st_size for path in files),
    )


def material_with_texture(source, candidate, asset):
    materials = source / "simready_usd/payloads/materials.usda"
    text = materials.read_text(encoding="utf-8")
    text = text.replace(
        "color3f inputs:diffuseColor = (0.75, 0.4, 0.2)",
        "prepend color3f inputs:diffuseColor.connect = </Materials/Fixture/Albedo.outputs:rgb>",
    )
    text = text.replace(
        "            token outputs:surface\n        }\n    }\n}\n",
        "            token outputs:surface\n        }\n"
        '        def Shader "Albedo" {\n'
        '            token info:id = "UsdUVTexture"\n'
        f"            asset inputs:file = @{asset}@\n"
        "            float3 outputs:rgb\n"
        "        }\n"
        "    }\n}\n",
    )
    materials.write_text(text, encoding="utf-8")
    return refresh_bom(source, candidate)


def test_synthetic_source_reaches_existing_import_and_structural_checks(tmp_path):
    candidate, _, output, report = packaged(tmp_path)
    assert report["variant"]["selected"] == "Newton/Enabled"
    assert report["visual"]["triangles"] == 12
    assert report["dimensionsM"] == [0.2, 0.2, 0.2]
    imported = import_package(
        output, tmp_path / "state", tmp_path / "library", structural_only=True
    )
    assert imported["status"] == "pass", imported
    assert imported["assetVersion"] == "environment/fixture-box@1"
    assert imported["structural"] == {
        "S1": "pass",
        "S2": "pass",
        "S3": "pass",
        "S4": "pass",
        "S5": "pass",
        "S6": "pass",
        "S7": "pass",
        "S8": "warn",
    }
    assert imported["uploadBytes"] < 128 * 1024 * 1024
    assert report["source"]["license"]["inBom"] is False
    assert ".metadata/LICENSE.txt" in report["source"]["bom"]["notInBom"]


def test_newton_variant_is_selected_instead_of_default_physx(tmp_path):
    _, _, output, _ = packaged(tmp_path)
    stage = Usd.Stage.Open(str(output / "asset.usda"))
    bodies = [
        prim for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.MassAPI)
    ]
    assert bodies
    masses = [bodies[0].GetAttribute("physics:mass").Get()]
    assert masses == [20.0]
    assert UsdGeom.GetStageUpAxis(stage) == "Z"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1
    assert stage.GetDefaultPrim().GetPath() == Sdf.Path("/Asset")
    assert not [prim for prim in stage.Traverse() if prim.HasAuthoredReferences()]


def test_missing_newton_variant_is_an_explicit_gap(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    root = source / "simready_usd/sm_fixture_box.usd"
    root.write_text(root.read_text(encoding="utf-8").replace("Newton", "NewtonMissing"), encoding="utf-8")
    candidate = refresh_bom(source, candidate)
    report = adapt(candidate, source, tmp_path / "broken")
    assert report["status"] == "gap"
    assert any("Newton" in gap for gap in report["gaps"])


def test_bom_mismatch_is_not_reported_as_a_pass(tmp_path):
    candidate, source, _, _ = packaged(tmp_path)
    bom = source / ".metadata/com.nvidia.simready.packaging.bom.json"
    value = json.loads(bom.read_text(encoding="utf-8"))
    value["items"][0]["hash"]["sha256"] = "0" * 64
    bom.write_text(json.dumps(value), encoding="utf-8")
    report = adapt(candidate, source, tmp_path / "second")
    assert report["status"] == "fail"
    assert any("BOM" in gap for gap in report["gaps"])


@pytest.mark.parametrize(
    ("asset", "message"),
    [
        ("C:\\\\assets\\\\box.png", "missing or escapes"),
        ("../Textures/box.<UDIM>.png", "UDIM"),
    ],
)
def test_unsafe_and_udim_texture_references_are_explicit_gaps(tmp_path, asset, message):
    candidate, source = build_source(tmp_path / "source")
    candidate = material_with_texture(source, candidate, asset)
    report = adapt(candidate, source, tmp_path / "broken")
    assert report["status"] == "gap"
    assert any(message in gap for gap in report["gaps"])


def test_support_list_matches_the_pinned_five_source_packages():
    path = Path(__file__).resolve().parents[2] / "factory/support.json"
    data = support_schema.load(path)
    assert support_schema.validate(data) == []
    assert data["source"]["revision"] == REVISION
    assert {item["package"] for item in data["sourceCandidates"]} == {
        candidate.package for candidate in SOURCE_PACKAGES
    }
    assert all(
        item["source"]["bomVerified"] == item["source"]["bomItems"]
        for item in data["sourceCandidates"]
    )
    assert all(
        item["adaptation"]["variant"]["selected"] == "Newton/Enabled"
        for item in data["sourceCandidates"]
    )
    assert all(item["license"]["inBom"] is False for item in data["sourceCandidates"])
    assert all(
        {"attribution", "retain license and notices", "mark modifications"}
        <= set(item["license"]["obligations"])
        for item in data["sourceCandidates"]
    )

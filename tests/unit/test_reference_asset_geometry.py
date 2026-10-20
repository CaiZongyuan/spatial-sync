"""Reference authoring assets retain actual geometry, local frames and honest gaps."""

import hashlib
import json

import pytest
from pxr import Usd, UsdGeom

from packages.library.common import BUILTIN_ROOT


def test_every_reference_contains_primitives_and_matches_its_recorded_local_files():
    packages = sorted((BUILTIN_ROOT / "assets").glob("*/*-reference/1"))
    assert len(packages) == 8
    for path in packages:
        stage = Usd.Stage.Open(str(path / "asset.usda"))
        assert any(prim.IsA(UsdGeom.Gprim) for prim in stage.Traverse()), path
        provenance = json.loads((path / "provenance.json").read_bytes())
        for name, expected in provenance["files"].items():
            data = (path / name).read_bytes()
            assert len(data) == expected["bytes"]
            assert hashlib.sha256(data).hexdigest() == expected["sha256"]
        report = json.loads((path / "validation.json").read_bytes())
        checks = {check["id"]: check for check in report["checks"]}
        assert checks["S5"]["status"] == "pass"
        assert checks["S4"]["status"] == "fail" and "license" in checks["S4"]["message"].lower()
        assert checks["P1-P6"]["status"] == "warn"
        gaps = {item["id"]: item["status"] for item in report["completeness"]}
        assert all(gaps[name] == "missing" for name in ("collision", "massInertia", "visual", "license"))


@pytest.mark.parametrize("name,height,radius,z", [
    ("synthetic-tube-reference", 0.08, 0.008, 0.04),
    ("native-small-tube-reference", 0.03, 0.008, 0.015),
    ("native-capsule-tube-reference", 0.11, 0.016, 0.071),
])
def test_tube_variants_keep_distinct_real_z_axis_geometry_and_visual_local_origin(name, height, radius, z):
    stage = Usd.Stage.Open(str(BUILTIN_ROOT / f"assets/labware/{name}/1/asset.usda"))
    primitive = stage.GetPrimAtPath("/Asset/Visual1")
    shape = UsdGeom.Capsule(primitive) if "capsule" in name else UsdGeom.Cylinder(primitive)
    assert shape.GetHeightAttr().Get() == pytest.approx(height)
    assert shape.GetRadiusAttr().Get() == pytest.approx(radius)
    assert shape.GetAxisAttr().Get() == "Z"
    translation = UsdGeom.Xformable(primitive).GetLocalTransformation().ExtractTranslation()
    assert list(translation) == pytest.approx([0, 0, z])
    if name == "native-small-tube-reference":
        marker = UsdGeom.Cube(stage.GetPrimAtPath("/Asset/Visual2"))
        scale = marker.GetPrim().GetAttribute("xformOp:scale").Get()
        assert list(scale) == pytest.approx([0.01, 0.01, 0.004])

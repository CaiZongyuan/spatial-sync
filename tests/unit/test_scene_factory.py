"""SF0 support boundary: pinned source, Newton variant and the existing import."""

import json
from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from factory import support as support_schema
from factory.fixture import build_source
from factory.simready import (
    CANDIDATES,
    REVISION,
    AdaptationError,
    _copy_texture,
    _normalize_joint_endpoints,
    _resolve_texture,
    _safe_relative,
    _select_newton,
    adapt,
    import_package,
)


def packaged(tmp_path):
    candidate, source = build_source(tmp_path / "source")
    output = tmp_path / "adapted"
    report = adapt(candidate, source, output)
    assert report["status"] == "pass", report["gaps"]
    return candidate, source, output, report


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


def test_missing_newton_variant_is_an_explicit_failure(tmp_path):
    path = tmp_path / "no-newton.usda"
    path.write_text(
        '#usda 1.0\n(defPrim = "RootNode")\n'
        'def Xform "RootNode" (prepend variantSets = ["PhysX"]) {\n'
        ' variantSet "PhysX" = { "Disabled" {} }\n}\n',
        encoding="utf-8",
    )
    stage = Usd.Stage.Open(str(path))
    with pytest.raises(AdaptationError, match="Newton"):
        _select_newton(stage)


def test_bom_mismatch_is_not_reported_as_a_pass(tmp_path):
    candidate, source, _, _ = packaged(tmp_path)
    bom = source / ".metadata/com.nvidia.simready.packaging.bom.json"
    value = json.loads(bom.read_text(encoding="utf-8"))
    value["items"][0]["hash"]["sha256"] = "0" * 64
    bom.write_text(json.dumps(value), encoding="utf-8")
    report = adapt(candidate, source, tmp_path / "second")
    assert report["status"] == "fail"
    assert any("BOM" in gap for gap in report["gaps"])


def test_unsafe_and_escaping_reference_paths_are_rejected(tmp_path):
    for value in ("C:\\assets\\box.png", "\\\\server\\share\\box.png", "/tmp/box.png", "../box.png"):
        assert not _safe_relative(value)
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(AdaptationError, match="missing or escapes"):
        _resolve_texture(source, tmp_path / "outside.png")
    with pytest.raises(AdaptationError, match="UDIM"):
        _resolve_texture(source, "../Textures/box.<UDIM>.png")


def test_repeated_texture_reference_keeps_the_package_relative_path(tmp_path, monkeypatch):
    import sys
    import types

    class FakeImage:
        size = (1, 1)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    image = types.SimpleNamespace(
        open=lambda path: FakeImage(), Resampling=types.SimpleNamespace(LANCZOS=None)
    )
    pil = types.ModuleType("PIL")
    pil.Image = image
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", image)
    source = tmp_path / "Texture.png"
    source.write_bytes(b"fixture")
    output = tmp_path / "package"
    output.mkdir()
    names = {}
    assert _copy_texture(source, output, names, []) == "textures/Texture.png"
    assert _copy_texture(source, output, names, []) == "textures/Texture.png"


def test_joint_endpoints_move_from_collision_shapes_to_rigid_bodies(tmp_path):
    path = tmp_path / "joint.usda"
    path.write_text(
        '''#usda 1.0
def Xform "RootNode" {
    def Xform "Body" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {
        def Mesh "Shape" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
            point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
            int[] faceVertexCounts = [3]
            int[] faceVertexIndices = [0, 1, 2]
            uniform token subdivisionScheme = "none"
        }
    }
    def PhysicsFixedJoint "Joint" {
        rel physics:body0 = </RootNode/Body/Shape>
    }
}
''',
        encoding="utf-8",
    )
    stage = Usd.Stage.Open(str(path))
    changes, unresolved = _normalize_joint_endpoints(stage)
    assert unresolved == []
    assert changes == [
        {
            "joint": "/RootNode/Joint",
            "endpoint": "body0",
            "from": ["/RootNode/Body/Shape"],
            "to": ["/RootNode/Body"],
        }
    ]


def test_support_list_matches_the_pinned_five_candidates():
    path = Path(__file__).resolve().parents[2] / "factory/support.json"
    data = support_schema.load(path)
    assert support_schema.validate(data) == []
    assert data["source"]["revision"] == REVISION
    assert {item["package"] for item in data["candidates"]} == {
        candidate.package for candidate in CANDIDATES
    }
    assert all(item["source"]["bomVerified"] == item["source"]["bomItems"] for item in data["candidates"])
    by_package = {item["package"]: item for item in data["candidates"]}
    assert by_package["bucket_utility_gray_a01"]["usage"]["staticCollision"] is True
    assert by_package["bucket_utility_gray_a01"]["import"]["validationFailures"] == []
    assert [
        failure["id"] for failure in by_package["box_cardboard_a01"]["import"]["validationFailures"]
    ] == ["P4"]
    assert by_package["container_recycle_square_blue_a02"]["usage"]["staticCollision"] is False
    assert [
        failure["id"]
        for failure in by_package["container_recycle_square_blue_a02"]["import"]["validationFailures"]
    ] == ["P1"]
    assert by_package["equipment_utility_cart_e01"]["adaptation"]["status"] == "gap"
    assert any(
        "UDIM" in gap for gap in by_package["equipment_utility_cart_e01"]["adaptation"]["gaps"]
    )

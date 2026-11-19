"""Pure-Python USDA primitive extraction for assets without a GLB visual."""

from pathlib import Path

from packages.library.assets import AssetLibrary
from packages.library.usd_primitives import extract_usda_primitives, fallback_asset_visuals
from packages.protocol.schema import Primitive

ROOT = Path(__file__).resolve().parents[2]


def test_builtin_reference_assets_extract_valid_protocol_primitives():
    library = AssetLibrary(state_root=ROOT / ".scratch/test-state-unused")
    library.startup()
    expected_reference_ids = {
        "device/lab-centrifuge-reference@1",
        "device/synthetic-cabinet-reference@1",
        "device/synthetic-incubator-reference@1",
        "device/synthetic-sensor-reference@1",
        "labware/native-capsule-tube-reference@1",
        "labware/native-small-tube-reference@1",
        "labware/synthetic-tube-reference@1",
        "robot/synthetic-arm-reference@1",
    }
    for asset_id in sorted(expected_reference_ids):
        summary = library.resolve(asset_id).summary()
        assert summary["visualUrl"] is None
        visuals = summary["fallbackVisuals"]
        assert len(visuals) >= 1, asset_id
        for item in visuals:
            Primitive.model_validate(item)


def test_extracts_y_up_and_nested_xforms_and_dimensions_fallback(tmp_path):
    usda = tmp_path / "asset.usda"
    usda.write_text(
        """#usda 1.0
(
    defaultPrim = "Asset"
    metersPerUnit = 0.01
    upAxis = "Y"
)
def Xform "Asset" {
    double3 xformOp:translate = (0, 10, 0)
    def Cylinder "Stem" {
        uniform token axis = "Y"
        double radius = 2
        double height = 6
    }
}
""",
        encoding="utf-8",
    )
    visuals = extract_usda_primitives(usda)
    assert len(visuals) == 1
    model = Primitive.model_validate(visuals[0])
    assert model.geometry.type == "cylinder"
    assert abs(model.geometry.radius - 0.02) < 1e-6
    assert abs(model.geometry.halfHeight - 0.03) < 1e-6
    # Parent Y=+0.10m rotated +90 deg around X becomes Z=+0.10m.
    assert abs(model.localPose.p[2] - 0.1) < 1e-6

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    (empty_dir / "asset.usda").write_text('#usda 1.0\ndef Xform "Asset" {}\n', encoding="utf-8")
    fallback = fallback_asset_visuals(
        {"stage": "asset.usda", "dimensionsM": [0.2, 0.4, 0.6]}, empty_dir
    )
    assert len(fallback) == 1
    box = Primitive.model_validate(fallback[0])
    assert box.geometry.type == "box"
    assert box.geometry.halfExtents == (0.1, 0.2, 0.3)
    assert box.localPose.p == (0.0, 0.0, 0.3)
    assert fallback_asset_visuals(
        {"stage": "asset.usda", "visual": "visual.glb", "dimensionsM": [0.2, 0.4, 0.6]}, empty_dir
    ) == []

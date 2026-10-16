from pathlib import Path

import pytest

newton = pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from packages.sim_runtime.loaders import import_robot, import_usd

ROOT = Path(__file__).resolve().parents[2]


def test_mjcf_labels_and_site_survive_builder_prefix():
    builder = newton.ModelBuilder()
    import_robot(builder, ROOT / "examples/falling-boxes/import-smoke.xml", prefix="World/Robot")
    model = builder.finalize(device="cpu")
    assert model.body_label == ["World/Robot/label-smoke/worldbody/Link"]
    assert "World/Robot/label-smoke/worldbody/Link/Hinge" in model.joint_label
    assert "World/Robot/label-smoke/worldbody/Link/tip" in model.shape_label


def test_usd_path_maps_rewrite_the_correct_mutable_label_entries():
    pytest.importorskip("pxr")
    builder = newton.ModelBuilder()
    paths = import_usd(builder, ROOT / "examples/falling-boxes/import-smoke.usda")
    assert paths["path_body_map"] == {"/World/Body": 0}
    assert paths["path_shape_map"] == {"/World/Body/Box": 0}
    assert paths["path_joint_map"] == {"/World/Hinge": 0}
    model = builder.finalize(device="cpu")
    assert model.body_label == ["/World/Body"]
    assert model.shape_label == ["/World/Body/Box"]
    assert model.joint_label == ["/World/Hinge"]

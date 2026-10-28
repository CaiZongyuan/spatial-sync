"""Physical bores follow the actual USD transform, not nominal radius labels."""

import itertools
import math

import pytest

pytest.importorskip("pxr.Usd")
from pxr import Gf, Usd, UsdGeom

from factory.cell_passage import ring


def test_quarter_turn_wall_scales_in_its_local_radial_frame_and_leaves_the_bore_open():
    stage = Usd.Stage.CreateInMemory()
    ring(stage, "/Asset/Ring", (0, 0, 0), 0.010, 0.020, 0.001, 8)
    wall = stage.GetPrimAtPath("/Asset/Ring/Wall02")  # 90 degrees, radial direction +Y.
    bounds = UsdGeom.BBoxCache(0, ["default"]).ComputeWorldBound(wall).ComputeAlignedRange()
    assert bounds.GetMin()[1] == pytest.approx(0.0095, abs=1e-9)
    assert bounds.GetMax()[1] == pytest.approx(0.0105, abs=1e-9)
    assert bounds.GetMax()[0] - bounds.GetMin()[0] > 0.009


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("parent_transform", [False, True])
def test_actual_wall_vertices_preserve_radial_clearance_under_parent_transform(index, parent_transform):
    stage = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(stage, "/Asset")
    if parent_transform:
        root.AddTranslateOp().Set(Gf.Vec3d(0.3, -0.7, 1.2))
        root.AddRotateZOp().Set(31.0)
        root.AddScaleOp().Set(Gf.Vec3f(0.7, 1.3, 2.0))
    ring(stage, "/Asset/Ring", (0, 0, 0), 0.010, 0.020, 0.001, 8)
    cache = UsdGeom.XformCache()
    wall = stage.GetPrimAtPath(f"/Asset/Ring/Wall{index:02d}")
    world = cache.GetLocalToWorldTransform(wall)
    parent_inverse = cache.GetLocalToWorldTransform(root.GetPrim()).GetInverse()
    angle = index * math.pi / 4
    projections = []
    for corner in itertools.product((-0.5, 0.5), repeat=3):
        actual = parent_inverse.Transform(world.Transform(Gf.Vec3d(*corner)))
        projections.append(actual[0] * math.cos(angle) + actual[1] * math.sin(angle))
    assert min(projections) == pytest.approx(0.0095, abs=1e-9)
    assert max(projections) == pytest.approx(0.0105, abs=1e-9)

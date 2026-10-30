"""Measure the actual polygon walls, unchanged rack region and tip envelope."""

import math
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pxr.Usd")
from pxr import Usd, UsdGeom, UsdPhysics

from factory.cell_passage import tip
from factory.cell_passage_tip import bore_volume_ul, relief


def test_tip_authoring_runs_without_optional_scipy():
    code = """
import importlib.abc
import math
import sys

class NoScipy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "scipy" or fullname.startswith("scipy."):
            raise ModuleNotFoundError("No module named 'scipy'", name=fullname)

sys.meta_path.insert(0, NoScipy())
from pxr import Usd, UsdGeom, UsdPhysics
from factory.cell_passage import tip
from factory.cell_passage_tip import relief

for author, count in ((tip, 12), (relief, 36)):
    stage = Usd.Stage.CreateInMemory()
    author(stage, "/Asset")
    assert sum(prim.IsA(UsdGeom.Mesh) for prim in stage.Traverse()) == count
    assert math.isclose(
        UsdPhysics.MassAPI(stage.GetPrimAtPath("/Asset")).GetMassAttr().Get(), .001, rel_tol=1e-6
    )
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr


def authored(author):
    stage = Usd.Stage.CreateInMemory()
    author(stage, "/Asset")
    return stage


def section(stage, path, z):
    points = UsdGeom.Mesh(stage.GetPrimAtPath(path)).GetPointsAttr().Get()
    fraction = (z - points[0][2]) / (points[4][2] - points[0][2])
    return [tuple(points[i][axis] + fraction * (points[i + 4][axis] - points[i][axis])
                  for axis in (0, 1)) for i in range(4)]


def edge_distance(first, second):
    delta = [second[i] - first[i] for i in (0, 1)]
    t = -sum(first[i] * delta[i] for i in (0, 1)) / sum(x * x for x in delta)
    assert 0 < t < 1
    return math.hypot(*(first[i] + t * delta[i] for i in (0, 1)))


@pytest.mark.parametrize("z", [0.04601, 0.047, 0.0497, 0.04999])
def test_actual_relief_aperture_clears_the_original_ten_mm_nozzle_below_the_seat(z):
    stage = authored(relief)
    for index in range(12):
        inner = section(stage, f"/Asset/Nozzle/Segment2/Wall{index:02d}", z)[:2]
        assert edge_distance(*inner) == pytest.approx(0.0054, abs=1e-9)


@pytest.mark.parametrize("z", [0.009, 0.014, 0.019])
def test_rack_guide_region_has_the_same_actual_outer_wall_as_the_old_tip(z):
    before, after = authored(tip), authored(relief)
    for index in range(12):
        old = section(before, f"/Asset/Nozzle/Wall{index:02d}", z)[2:]
        new = section(after, f"/Asset/Nozzle/Segment0/Wall{index:02d}", z)[2:]
        for actual, expected in zip(new, old, strict=True):
            assert actual == pytest.approx(expected, abs=1e-9)
            assert 0.0032 - math.hypot(*actual) >= 0.0003519


def test_root_mass_height_envelope_and_automatic_mass_center_are_preserved():
    before, after = authored(tip), authored(relief)
    boxes = []
    for stage in (before, after):
        root = stage.GetPrimAtPath("/Asset")
        mass = UsdPhysics.MassAPI(root)
        assert mass.GetMassAttr().Get() == pytest.approx(0.001)
        assert not mass.GetCenterOfMassAttr().HasAuthoredValue()
        bounds = UsdGeom.BBoxCache(0, ["default"]).ComputeWorldBound(root).ComputeAlignedRange()
        boxes.append((list(bounds.GetMin()), list(bounds.GetMax())))
    for actual, expected in zip(boxes[1], boxes[0], strict=True):
        assert actual == pytest.approx(expected, abs=1e-9)
    assert boxes[1][1][2] == pytest.approx(0.062, abs=1e-9)
    assert bore_volume_ul() > 1000

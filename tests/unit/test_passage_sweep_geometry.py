"""The envelope includes interior angular extrema of actual collider vertices."""

import math

import pytest

pytest.importorskip("pxr.Usd")

from factory.cell_passage_safety import joint_safety, swept_bounds


def test_sweep_includes_an_extremum_between_the_command_endpoints():
    low, high = swept_bounds([[2, 0, 1]], [0, 0, 0], [0, 0, 1], [math.pi / 4, 3 * math.pi / 4])
    assert low == pytest.approx([-math.sqrt(2), math.sqrt(2), 1])
    assert high == pytest.approx([math.sqrt(2), 2, 1])


def test_actual_negative_hinge_axis_rotates_toward_the_front_of_the_cabinet():
    low, high = swept_bounds([[2, 0, 1]], [0, 0, 0], [0, 0, -1], [0, math.pi / 2])
    assert low == pytest.approx([0, -2, 1], abs=1e-12)
    assert high == pytest.approx([2, 0, 1], abs=1e-12)


@pytest.mark.parametrize("limits", [[0, 2 * math.pi], [-1000, 1000]])
def test_full_rotations_have_bounded_exact_extrema(limits):
    low, high = swept_bounds([[2, 0, 1]], [0, 0, 0], [0, 0, 1], limits)
    assert low == pytest.approx([-2, -2, 1])
    assert high == pytest.approx([2, 2, 1])


@pytest.mark.parametrize("fault", ["empty", "unknown_axis", "nonfinite", "reversed"])
def test_unknown_sweep_geometry_cannot_be_an_empty_safe_envelope(fault):
    points, axis, limits = [[1, 0, 0]], [0, 0, 1], [0, 1]
    if fault == "empty":
        points = []
    elif fault == "unknown_axis":
        axis = [0, 0, 0]
    elif fault == "nonfinite":
        points[0][0] = float("nan")
    else:
        limits = [1, 0]
    with pytest.raises(ValueError):
        swept_bounds(points, [0, 0, 0], axis, limits)


def test_safety_declarations_follow_actual_sibling_frames_body_relations_and_float32_limits():
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    lab = "/World/Lab"
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, lab)
    robot = UsdGeom.Xform.Define(stage, lab + "/Robot")
    for index in range(11):
        UsdPhysics.RigidBodyAPI.Apply(UsdGeom.Xform.Define(
            stage, str(robot.GetPath()) + "/body" + str(index)).GetPrim())
    carriage = UsdGeom.Xform.Define(stage, lab + "/RailCarriage")
    UsdPhysics.RigidBodyAPI.Apply(carriage.GetPrim())
    rail = UsdPhysics.PrismaticJoint.Define(stage, lab + "/Joints/RailCarriage")
    rail.CreateBody1Rel().SetTargets([carriage.GetPath()])
    base = UsdGeom.Xform.Define(stage, lab + "/Incubator/Base")
    base.AddTranslateOp().Set(Gf.Vec3d(10, 0, 0))
    UsdPhysics.RigidBodyAPI.Apply(base.GetPrim())
    for name in ("OuterDoor", "InnerDoor"):
        door = UsdGeom.Xform.Define(stage, lab + "/Incubator/" + name)
        door.AddTranslateOp().Set(Gf.Vec3d(11, 0, 0))
        UsdPhysics.RigidBodyAPI.Apply(door.GetPrim())
        cube = UsdGeom.Cube.Define(stage, str(door.GetPath()) + "/Collider")
        cube.CreateSizeAttr(0.2)
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
        joint = UsdPhysics.RevoluteJoint.Define(stage, lab + "/Joints/Incubator" + name)
        joint.CreateBody0Rel().SetTargets([base.GetPath()])
        joint.CreateBody1Rel().SetTargets([door.GetPath()])
        joint.CreateAxisAttr("Z")
        joint.CreateLowerLimitAttr(0)
        joint.CreateUpperLimitAttr(math.degrees(1.745))
    declarations, sources = joint_safety(stage, {"lids": {}})
    outer = lab + "/Incubator/OuterDoor"
    assert len(declarations[lab + "/RailCarriage"]["movingBodyKeys"]) == 12
    assert declarations[outer]["movingBodyKeys"] == [outer]
    assert declarations[outer]["sweepAnchorBody"] == str(base.GetPath())
    assert declarations[outer]["upper"] == sources[outer]["physicalRangeRad"][1]
    assert declarations[outer]["upper"] != 1.745  # USD stores the physical degree limit as float32.
    assert declarations[outer]["sweepUpperLocalM"][0] == pytest.approx(math.hypot(1.1, .1))
    assert declarations[outer]["sweepUpperLocalM"][1] < 1.2  # No erroneous +10 m sibling translation.

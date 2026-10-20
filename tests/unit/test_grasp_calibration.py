"""Actual pinned FR3 USD and adversarial composed geometry, without Newton."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from apps.api.app.library_validation import program_schemas
from packages.library.common import BUILTIN_ROOT
from packages.library.grasp_calibration import grasp_checks, physical_calibration, tube_bounds
from packages.library.structural import USDGraph
from packages.library.templates import instantiate, load_snapshots, resolve

ROBOT = "/World/Lab/RobotA"
TUBE = "/World/Lab/Tube"


@pytest.fixture
def fixture(tmp_path):
    assets = load_snapshots({"builtin": BUILTIN_ROOT}, ["robot/fr3@1"])
    authored = {"scene": "grasp-test", "entities": [{"key": ROBOT, "template": "robot/fr3@1",
        "config": {"graspHeightM": 0.080, "tubes": [TUBE], "locations": {"locations": {}}}}]}
    path = tmp_path / "stage.usda"
    stage = Usd.Stage.CreateNew(str(path))
    robot = UsdGeom.Xform.Define(stage, ROBOT)
    robot.GetPrim().GetReferences().AddReference("library:assets/robot/fr3/1/asset.usdc")
    robot.AddTranslateOp().Set(Gf.Vec3d(.2, .7, .95))
    body = UsdGeom.Xform.Define(stage, TUBE).GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(body)
    mesh = UsdGeom.Mesh.Define(stage, TUBE + "/Hull")
    mesh.CreatePointsAttr([(-.01, -.01, 0), (.01, -.01, 0), (0, .01, .129)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    stage.GetRootLayer().Save()
    graph = USDGraph({"path": str(tmp_path), "files": {path.name: str(path)}}, list(assets.values()))
    composed = graph.compose(path.name)
    from packages.library.structural import usd_resources

    programs = program_schemas()
    bindings = instantiate(resolve(authored, "newton", assets), usd_resources(composed), programs)["entities"]
    return composed, bindings[0], programs["robot_arm.newton"], authored, graph


def test_actual_asset_and_composed_tcp_keep_physical_owner_and_103_point_4_mm(fixture):
    row = physical_calibration(*fixture)
    assert row["expected"] == row["composed"]
    assert row["expected"]["siteOwner"] == ROBOT + "/fr3_hand"
    assert row["expected"]["handToTcpPose"] == [0, 0, .1034, 0, 0, 0, 1]


@pytest.mark.parametrize("change", ["tcp", "owner", "axis", "collider", "body", "scale", "fake-site"])
def test_composed_physical_edits_cannot_become_calibrated_by_point_alignment(fixture, change):
    stage, binding, program, authored, graph = fixture
    if change == "tcp":
        UsdGeom.Xformable(stage.GetPrimAtPath(ROBOT + "/fr3_hand/tcp")).ClearXformOpOrder()
        UsdGeom.Xformable(stage.GetPrimAtPath(ROBOT + "/fr3_hand/tcp")).AddTranslateOp().Set((0, 0, .1834))
    elif change == "owner":
        UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath(ROBOT + "/fr3_hand/tcp"))
    elif change == "axis":
        UsdPhysics.PrismaticJoint(stage.GetPrimAtPath(ROBOT + "/fr3_finger_joint1")).CreateAxisAttr("X")
    elif change == "collider":
        prim = next(p for p in stage.Traverse() if str(p.GetPath()).startswith(ROBOT)
                    and p.HasAPI(UsdPhysics.CollisionAPI) and p.IsA(UsdGeom.Mesh))
        mesh = UsdGeom.Mesh(prim)
        points = list(mesh.GetPointsAttr().Get())
        points[0] = points[0] + Gf.Vec3f(.01, 0, 0)
        mesh.GetPointsAttr().Set(points)
    elif change == "body":
        UsdGeom.Xformable(stage.GetPrimAtPath(ROBOT + "/fr3_hand")).ClearXformOpOrder()
    elif change == "scale":
        UsdGeom.Xformable(stage.GetPrimAtPath(ROBOT)).AddScaleOp().Set((1, 1, 1.001))
    else:
        path = ROBOT + "/fr3_hand/virtualBottom"
        UsdGeom.Xform.Define(stage, path)
        binding["config"]["tcpSite"] = path
    with pytest.raises(ValueError, match="calibration|rigid"):
        physical_calibration(stage, binding, program, authored, graph)


def test_rigid_placement_drive_and_initial_state_opinions_remain_allowed(fixture):
    stage, *_ = fixture
    root = UsdGeom.Xformable(stage.GetPrimAtPath(ROBOT))
    root.AddRotateZOp().Set(27)
    joint = stage.GetPrimAtPath(ROBOT + "/fr3_joint1")
    joint.CreateAttribute("newton:angular:position", Sdf.ValueTypeNames.Float).Set(20)
    UsdPhysics.DriveAPI.Apply(joint, "angular").CreateTargetPositionAttr(20)
    assert physical_calibration(*fixture)["expected"] == physical_calibration(*fixture)["composed"]


def test_actual_mesh_points_override_a_forged_extent(fixture):
    stage, *_ = fixture
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath(TUBE + "/Hull"))
    mesh.CreateExtentAttr([(-1, -1, 0), (1, 1, 100)])
    assert tube_bounds(stage, TUBE)["boundsM"][1][2] == pytest.approx(.129)


@pytest.mark.parametrize("change", ["no-template", "height", "bottom", "unknown", "conflict"])
def test_s5_rejects_unknown_or_incompatible_nonzero_height(fixture, change):
    stage, binding, program, authored, graph = fixture
    bindings = [binding]
    if change == "no-template":
        authored["entities"][0].pop("template")
    elif change == "height":
        binding["config"]["graspHeightM"] = .130
    elif change == "bottom":
        UsdGeom.Xformable(stage.GetPrimAtPath(TUBE + "/Hull")).AddTranslateOp().Set((0, 0, .001))
    elif change == "unknown":
        UsdPhysics.CollisionAPI(stage.GetPrimAtPath(TUBE + "/Hull")).CreateCollisionEnabledAttr(False)
    else:
        other = deepcopy(binding)
        other["key"] = "/World/Lab/OtherRobot"
        other["config"]["graspHeightM"] = 0
        bindings.append(other)
    problems, _ = grasp_checks(stage, bindings, {binding["program"]: program}, authored, graph)
    assert problems
    if change == "conflict":
        assert "Conflicting" in " ".join(problems)


def test_s5_accepts_compatible_nonzero_height_and_returns_actual_geometry(fixture):
    stage, binding, program, authored, graph = fixture
    problems, receipts = grasp_checks(stage, [binding], {binding["program"]: program}, authored, graph)
    assert not problems
    assert receipts[ROBOT]["tubes"][TUBE]["boundsM"][1][2] == pytest.approx(.129)


def test_selected_scene_teaching_reads_actual_pin_base_and_height_without_ik(fixture, tmp_path):
    from tools.teach import select_scene

    _, _, _, authored, graph = fixture
    library = tmp_path / "library"
    package = library / "scenes/grasp-test/2"
    package.mkdir(parents=True)
    path = package / "stage.usda"
    path.write_bytes(Path(graph.package["files"]["stage.usda"]).read_bytes())
    stage = Usd.Stage.Open(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    stage.GetRootLayer().Save()
    documents = {"scene.yaml": {"name": "grasp-test", "title": "Grasp teaching fixture",
                               "sources": ["newton"], "stage": "stage.usda"},
                 "devices.yaml": authored, "locations.yaml": {"locations": {}}}
    for name, value in documents.items():
        (package / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    selected = select_scene("grasp-test@2", ROBOT, library)
    assert selected.identity["sceneVersion"] == "grasp-test@2"
    assert selected.identity["assetId"] == "robot/fr3@1"
    assert selected.config["graspHeightM"] == .080
    assert selected.teacher.base[:3, 3].tolist() == [.2, .7, .95]

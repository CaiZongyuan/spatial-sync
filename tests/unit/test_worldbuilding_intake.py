"""Independent byte/coordinate checks for the local visual import boundary."""

import json

import numpy as np
import pytest
import trimesh
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from factory.visual_geometry import surface_signature
from factory.worldbuilding_blender import batches, classify
from tools.worldbuilding_intake import RECIPE, audit_visual, compose, fingerprint, package


@pytest.fixture
def visual(tmp_path):
    local = np.eye(4)
    local[:3, 3] = [0, 2, 3]
    placement = np.eye(4)
    placement[:3, 3] = [1, 0, 0]
    scene = trimesh.Scene()
    geometry = trimesh.creation.box(extents=[2, 2, 2])
    scene.add_geometry(geometry, node_name="part__intake", transform=local)
    (tmp_path / "visual.glb").write_bytes(scene.export(file_type="glb"))
    stage = Usd.Stage.CreateNew(str(tmp_path / "asset.usdc"))
    asset = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(asset.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    cube = UsdGeom.Mesh.Define(stage, "/Asset/part")
    cube.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(geometry.vertices, dtype=np.float32)))
    cube.CreateFaceVertexCountsAttr([3] * len(geometry.faces))
    cube.CreateFaceVertexIndicesAttr(geometry.faces.reshape(-1).tolist())
    cube.GetPrim().CreateAttribute("userProperties:source_object", Sdf.ValueTypeNames.String).Set("part")
    cube.AddTranslateOp().Set(Gf.Vec3d(0, 2, 3))
    stage.GetRootLayer().Save()
    signature, _ = surface_signature(geometry.vertices, [3] * len(geometry.faces), geometry.faces.reshape(-1))
    chunk = {"name": "sample-1", "kind": "environment", "group": "sample", "triangles": 12,
             "nodes": {"part": "part__intake"},
             "placement": placement.tolist(), "objects": [
                 {"name": "part", "triangles": 12, "sourceWorld": (placement @ local).tolist(),
                  "sourceTriangleGeometry": signature, "sourcePolygonGeometry": signature,
                  "sourceWorldBounds": [[0, 1, 2], [2, 3, 4]]}]}
    return tmp_path, chunk


def test_decoded_visual_retains_metre_z_up_placement(visual):
    directory, chunk = visual
    audit = audit_visual(directory, chunk)
    assert audit["meshObjects"] == 1
    assert audit["dimensionsM"] == [2, 2, 2]
    assert audit["boundsM"] == [[-1, 1, 2], [1, 3, 4]]


def test_axis_swap_cannot_pass_the_source_pose_witness(visual):
    directory, chunk = visual
    chunk["objects"][0]["sourceWorld"][1][3] = 3
    chunk["objects"][0]["sourceWorld"][2][3] = -2
    with pytest.raises(ValueError, match="transform differs"):
        audit_visual(directory, chunk)


def test_missing_source_object_is_rejected(visual):
    directory, chunk = visual
    chunk["nodes"]["missing"] = "missing__intake"
    chunk["objects"].append({"name": "missing"})
    with pytest.raises(ValueError, match="source-object completeness"):
        audit_visual(directory, chunk)


def test_moved_geometry_with_intact_node_cannot_pass_source_bounds(visual):
    directory, chunk = visual
    chunk["objects"][0]["sourceWorldBounds"] = [[0, 1, 8], [2, 3, 10]]
    with pytest.raises(ValueError, match="source bounds"):
        audit_visual(directory, chunk)


def test_only_explicitly_counted_source_zero_area_triangles_may_disappear(visual):
    directory, chunk = visual
    chunk["triangles"] = 14
    chunk["objects"][0] |= {"triangles": 14, "nonDegenerateTriangles": 12, "zeroAreaTriangles": 2}
    assert audit_visual(directory, chunk)["instancedTriangles"] == 12
    chunk["objects"][0]["nonDegenerateTriangles"] = 13
    with pytest.raises(ValueError, match="nondegenerate triangle count"):
        audit_visual(directory, chunk)


def test_external_texture_dependency_is_rejected(visual):
    directory, chunk = visual
    stage = Usd.Stage.Open(str(directory / "asset.usdc"))
    stage.GetPrimAtPath("/Asset").CreateAttribute("testTexture", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath("C:/outside.png"))
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match="package-relative"):
        audit_visual(directory, chunk)


def test_usd_geometry_is_compared_to_actual_decoded_glb_points(visual):
    directory, chunk = visual
    stage = Usd.Stage.Open(str(directory / "asset.usdc"))
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/Asset/part"))
    points = np.asarray(mesh.GetPointsAttr().Get()) + [0, 0, 1]
    mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(points.astype(np.float32)))
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match="USD surface payload differs"):
        audit_visual(directory, chunk)


def test_shrinking_both_payloads_cannot_pass_the_independent_source_witness(visual):
    directory, chunk = visual
    local = np.eye(4)
    local[:3, 3] = [0, 2, 3]
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(extents=[1, 1, 1]), node_name="part__intake", transform=local)
    (directory / "visual.glb").write_bytes(scene.export(file_type="glb"))
    stage = Usd.Stage.Open(str(directory / "asset.usdc"))
    mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/Asset/part"))
    mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(np.asarray(mesh.GetPointsAttr().Get()) * .5))
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match="GLB surface payload differs"):
        audit_visual(directory, chunk)


def test_degenerate_usd_faces_cannot_hide_behind_unchanged_point_bounds(visual):
    directory, chunk = visual
    stage = Usd.Stage.Open(str(directory / "asset.usdc"))
    UsdGeom.Mesh(stage.GetPrimAtPath("/Asset/part")).GetFaceVertexIndicesAttr().Set([0] * 36)
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match="no nondegenerate faces"):
        audit_visual(directory, chunk)


def test_concave_usd_polygon_cannot_reorder_its_boundary_with_the_same_points(tmp_path):
    points = np.array([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0], [1, 1, 0]], dtype=np.float32)
    faces = np.array([[0, 1, 4], [1, 2, 4], [2, 3, 4]])
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.Trimesh(vertices=points, faces=faces, process=False), node_name="part__intake")
    (tmp_path / "visual.glb").write_bytes(scene.export(file_type="glb"))
    stage = Usd.Stage.CreateNew(str(tmp_path / "asset.usdc"))
    asset = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(asset.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    mesh = UsdGeom.Mesh.Define(stage, "/Asset/part")
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(points))
    mesh.CreateFaceVertexCountsAttr([5])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3, 4])
    mesh.GetPrim().CreateAttribute("userProperties:source_object", Sdf.ValueTypeNames.String).Set("part")
    stage.GetRootLayer().Save()
    triangle, _ = surface_signature(points, [3] * 3, faces.reshape(-1))
    polygon, _ = surface_signature(points, [5], np.arange(5))
    chunk = {"name": "sample-1", "triangles": 3, "nodes": {"part": "part__intake"},
             "placement": np.eye(4).tolist(), "objects": [{
                 "name": "part", "triangles": 3, "sourceWorld": np.eye(4).tolist(),
                 "sourceTriangleGeometry": triangle, "sourcePolygonGeometry": polygon,
                 "sourceWorldBounds": [[0, 0, 0], [2, 2, 0]]}]}
    assert audit_visual(tmp_path, chunk)["instancedTriangles"] == 3
    mesh.GetFaceVertexIndicesAttr().Set([0, 1, 4, 2, 3])
    stage.GetRootLayer().Save()
    with pytest.raises(ValueError, match="USD surface payload differs"):
        audit_visual(tmp_path, chunk)


def test_polygon_witness_allows_only_cyclic_rotation_and_reversal():
    points = np.array([[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0], [1, 1, 0]])
    original, _ = surface_signature(points, [5], [0, 1, 2, 3, 4])
    rotated, _ = surface_signature(points, [5], [2, 3, 4, 0, 1])
    reversed_loop, _ = surface_signature(points, [5], [4, 3, 2, 1, 0])
    changed, _ = surface_signature(points, [5], [0, 1, 4, 2, 3])
    assert original == rotated == reversed_loop
    assert changed != original


def package_fixture(directory, chunk):
    project, derived = directory / "project", directory / "derived"
    (project / "07-render").mkdir(parents=True)
    source = project / "07-render/render_scene_lookdev.blend"
    source.write_bytes(b"independent fixture source")
    derived.mkdir()
    receipt = {"recipe": RECIPE, "frame": 0, "source": "07-render/render_scene_lookdev.blend",
               "sourceFile": fingerprint(source), "chunks": [chunk]}
    (derived / "derivation.json").write_text(json.dumps(receipt), encoding="utf-8")
    return project, derived


def make_symlink(path, target, *, directory=False):
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"OS does not permit fixture symlinks: {error}")


def test_chunk_symlink_cannot_write_metadata_outside_the_owned_derivative(visual):
    outside, chunk = visual
    chunk["files"] = {name: fingerprint(outside / name) for name in ("visual.glb", "asset.usdc")}
    project, derived = package_fixture(outside / "fixture", chunk)
    make_symlink(derived / "sample-1", outside, directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        package(project, derived)
    assert not (outside / "asset.yaml").exists()
    assert not (outside / "provenance.json").exists()


def test_preexisting_license_symlink_is_rejected_before_any_metadata_write(visual):
    directory, chunk = visual
    project, derived = package_fixture(directory / "fixture", chunk)
    package_dir = derived / "sample-1"
    package_dir.mkdir()
    outside = directory / "outside-license"
    outside.write_text("untouched original")
    make_symlink(package_dir / "LICENSE", outside)
    chunk["files"] = {}
    (derived / "derivation.json").write_text(json.dumps({
        "recipe": RECIPE, "frame": 0, "source": "07-render/render_scene_lookdev.blend",
        "sourceFile": fingerprint(project / "07-render/render_scene_lookdev.blend"), "chunks": [chunk]}))
    with pytest.raises(ValueError, match="symlinks"):
        package(project, derived)
    assert outside.read_text() == "untouched original"
    assert not (package_dir / "asset.yaml").exists()


def test_manifest_source_path_cannot_escape_the_project(visual):
    directory, chunk = visual
    chunk["files"] = {}
    project, derived = package_fixture(directory / "fixture", chunk)
    receipt = json.loads((derived / "derivation.json").read_text())
    receipt["source"] = "../outside.blend"
    (derived / "derivation.json").write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="Source must be"):
        package(project, derived)


def test_scene_references_actual_returned_revision_without_device_bindings(tmp_path):
    placement = np.eye(4)
    placement[:3, 3] = [2.95, 0, .85]
    receipt = {"sourceFile": {"sha256": "a" * 64}, "chunks": [
        {"name": "panda-1", "placement": placement.tolist()}]}
    scene = compose(tmp_path / "scene", receipt, {"panda-1": "robot/panda-cell-passaging-panda-1@3"})
    layer = Sdf.Layer.FindOrOpen(str(scene / "stage.usda"))
    prim = layer.GetPrimAtPath("/World/Dressing/panda_1")
    assert prim.referenceList.prependedItems[0].assetPath == (
        "library:assets/robot/panda-cell-passaging-panda-1/3/asset.usdc")
    assert "entities: []" in (scene / "devices.yaml").read_text(encoding="utf-8")


def test_named_body_ancestors_preserve_robot_and_device_identity():
    assert classify(["VIS_finger", "ROOT_panda__left_finger"]) == ("panda", "robot")
    assert classify(["label", "front_door_left_hinge"]) == ("workstation", "device")
    assert classify(["plate", "plate_ROOT__daughter"]) == ("daughter-plate", "labware")


def test_a_single_unbounded_mesh_is_not_silently_simplified():
    with pytest.raises(ValueError, match="exceeds visual chunk budget"):
        batches([{"name": "too-big", "triangles": 450001}])

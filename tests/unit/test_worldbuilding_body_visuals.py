"""Body-local asset output and exact Scene overlay preserve physical authority."""

import json
import struct

import numpy as np
import pytest
import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdShade, Vt

from factory.visual_geometry import surface_signature
from tools.worldbuilding_body_visuals import compose, derive_leaf, rendered_meshes
from tools.worldbuilding_intake import LOCAL_AUTHORIZATION, fingerprint


@pytest.fixture
def body_source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    body_world = np.eye(4)
    body_world[:3, 3] = [1., 2., 3.]
    local = np.eye(4)
    local[:3, 3] = [0., .2, .3]
    geometry = trimesh.creation.box(extents=[.1, .2, .3])
    scene = trimesh.Scene()
    scene.graph.update(frame_to="body_root", matrix=body_world)
    scene.add_geometry(geometry, node_name="mesh__body", parent_node_name="body_root", transform=local)
    (source / "visual.glb").write_bytes(scene.export(file_type="glb"))
    stage = Usd.Stage.CreateNew(str(source / "asset.usdc"))
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    body = UsdGeom.Xform.Define(stage, "/Asset/Body")
    body.AddTransformOp().Set(Gf.Matrix4d(body_world.T.tolist()))
    body.GetPrim().CreateAttribute("userProperties:source_object", Sdf.ValueTypeNames.String).Set("ROOT_hand")
    mesh = UsdGeom.Mesh.Define(stage, "/Asset/Body/Mesh")
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(geometry.vertices, dtype=np.float32)))
    mesh.CreateFaceVertexCountsAttr([3] * len(geometry.faces))
    mesh.CreateFaceVertexIndicesAttr(geometry.faces.reshape(-1).tolist())
    mesh.AddTranslateOp().Set(Gf.Vec3d(0., .2, .3))
    mesh.GetPrim().CreateAttribute("userProperties:source_object", Sdf.ValueTypeNames.String).Set("mesh")
    UsdGeom.Scope.Define(stage, "/Asset/_materials")
    material = UsdShade.Material.Define(stage, "/Asset/_materials/Material")
    shader = UsdShade.Shader.Define(stage, "/Asset/_materials/Material/着色器")
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(.37)
    shader.CreateOutput("surface", Sdf.ValueTypeNames.Token)
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)
    stage.GetRootLayer().Save()
    signature, _ = surface_signature(geometry.vertices, [3] * len(geometry.faces), geometry.faces.reshape(-1))
    world_points = trimesh.transform_points(geometry.vertices, body_world @ local)
    chunk = {"name": "panda-1", "kind": "robot", "nodes": {"ROOT_hand": "body_root", "mesh": "mesh__body"},
        "files": {name: fingerprint(source / name) for name in ("visual.glb", "asset.usdc")}, "objects": [{
            "name": "mesh", "ancestry": ["mesh", "ROOT_hand"], "triangles": 12, "nonDegenerateTriangles": 12,
            "sourceWorld": (body_world @ local).tolist(), "sourceTriangleGeometry": signature,
            "sourcePolygonGeometry": signature, "sourceWorldBounds": [world_points.min(axis=0).tolist(),
                                                                        world_points.max(axis=0).tolist()]}]}
    anchor = {"sourceBody": "panda__hand", "visualRoot": "ROOT_hand",
              "bodyPrimPath": "/World/Lab/panda_rail/panda__hand"}
    return source, chunk, anchor, body_world


def test_leaf_retains_source_surface_in_body_coordinates_and_material_connections(body_source, tmp_path):
    source, chunk, anchor, body_world = body_source
    output = tmp_path / "leaf"
    result = derive_leaf(source, chunk, anchor, body_world.tolist(), output, {"sha256": "a" * 64})
    assert result["sourceMeshObjects"] == result["renderedMeshes"] == 1
    np.testing.assert_allclose(result["audit"]["boundsM"], [[-.05, .1, .15], [.05, .3, .45]], atol=1e-7)
    stage = Usd.Stage.Open(str(output / "asset.usdc"))
    assert not stage.GetPrimAtPath("/Asset").GetAppliedSchemas()
    assert not UsdGeom.Xformable(stage.GetPrimAtPath("/Asset")).GetOrderedXformOps()
    assert stage.GetPrimAtPath("/Asset/Appearance").GetAttribute("purpose").Get() == "render"
    assert all(prim.GetName().isascii() for prim in stage.Traverse())
    surface = stage.GetPrimAtPath("/Asset/_materials/Material").GetAttribute("outputs:surface")
    target, = surface.GetConnections()
    assert stage.GetAttributeAtPath(target).GetPrim().GetAttribute("inputs:roughness").Get() == (
        pytest.approx(.37))
    assert yaml.safe_load((output / "asset.yaml").read_text())["dressing"] is True
    assert json.loads((output / "provenance.json").read_text())["sourceMeshObjects"] == ["mesh"]


def test_diagnostic_rest_pose_cannot_substitute_for_true_frame0_body(body_source, tmp_path):
    source, chunk, anchor, wrong = body_source
    wrong[0, 3] += .02
    with pytest.raises(ValueError, match="transform differs"):
        derive_leaf(source, chunk, anchor, wrong.tolist(), tmp_path / "wrong", {"sha256": "a" * 64})


@pytest.mark.parametrize("chunk_name, kind", [("panda-1", "robot"), ("imager-1", "device"),
                                            ("mother-plate-1", "labware"), ("mother-lid-1", "labware")])
def test_leaf_preserves_source_notices_outside_geometry_list_without_name_collisions(
    body_source, tmp_path, chunk_name, kind
):
    source, chunk, anchor, world = body_source
    chunk |= {"name": chunk_name, "kind": kind}
    notices = {"LICENSE": b"Original license\r\n", "SOURCE_NOTICE.md": b"OEM source; unverified use\n",
               "PANDA_NOTICE": b"Original upstream notice\n", ".metadata/LICENSE": b"Attachment license\n",
               "PANDA_README.md": b"Original readme\n", "UPSTREAM_COMMIT.txt": b"fixed-revision\n"}
    for relative, content in notices.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    assert not set(notices).intersection(chunk["files"])
    output = tmp_path / "leaf"
    derive_leaf(source, chunk, anchor, world.tolist(), output, {"sha256": "a" * 64})
    for relative, content in notices.items():
        assert (output / "upstream" / relative).read_bytes() == content
        assert (source / relative).read_bytes() == content
    assert (output / "LICENSE").read_text() == LOCAL_AUTHORIZATION
    provenance = json.loads((output / "provenance.json").read_text())
    retained = {row["sourcePath"]: row for row in provenance["sourceNotices"]}
    assert set(retained) == set(notices)
    for relative, row in retained.items():
        assert row == {"sourcePath": relative, "outputPath": "upstream/" + relative,
                       **fingerprint(source / relative)}
        assert provenance["files"][row["outputPath"]] == fingerprint(source / relative)


def test_redirected_source_notice_is_rejected_before_output_creation(body_source, tmp_path):
    source, chunk, anchor, world = body_source
    outside = tmp_path / "outside-notice"
    outside.write_bytes(b"Unrelated source notice")
    try:
        (source / "SOURCE_NOTICE.md").symlink_to(outside)
    except OSError as error:
        pytest.skip(f"OS does not permit fixture symlinks: {error}")
    output = tmp_path / "leaf"
    with pytest.raises(ValueError, match="symlinks"):
        derive_leaf(source, chunk, anchor, world.tolist(), output, {"sha256": "a" * 64})
    assert not output.exists() and outside.read_bytes() == b"Unrelated source notice"


def test_duplicate_default_scene_references_are_not_hidden_by_name_dictionary():
    document = {"scene": 0, "scenes": [{"nodes": [0, 0]}], "nodes": [{"name": "mesh", "mesh": 0}],
                "meshes": [{"primitives": [{}]}]}
    assert rendered_meshes(document) == [("mesh", 1), ("mesh", 1)]


def test_duplicate_body_mesh_reference_rejects_derivation(body_source, tmp_path):
    source, chunk, anchor, world = body_source
    raw = (source / "visual.glb").read_bytes()
    size = struct.unpack_from("<I", raw, 12)[0]
    document = json.loads(raw[20:20 + size])
    body, = [node for node in document["nodes"] if node.get("name") == "body_root"]
    body["children"] *= 2
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    tail = raw[20 + size:]
    (source / "visual.glb").write_bytes(struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(tail))
                                      + struct.pack("<I4s", len(encoded), b"JSON") + encoded + tail)
    with pytest.raises(ValueError, match="ownership"):
        derive_leaf(source, chunk, anchor, world.tolist(), tmp_path / "duplicate", {"sha256": "a" * 64})


def test_leaf_refuses_overwrite_before_any_existing_file_changes(body_source, tmp_path):
    source, chunk, anchor, world = body_source
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "marker"
    marker.write_bytes(b"retained")
    with pytest.raises(ValueError, match="fresh body"):
        derive_leaf(source, chunk, anchor, world.tolist(), output, {"sha256": "a" * 64})
    assert marker.read_bytes() == b"retained" and list(output.iterdir()) == [marker]


def test_redirected_output_parent_cannot_create_outside_directory(body_source, tmp_path):
    source, chunk, anchor, world = body_source
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "redirected"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"OS does not permit fixture symlinks: {error}")
    with pytest.raises(ValueError, match="symlinks"):
        derive_leaf(source, chunk, anchor, world.tolist(), link / "new-leaf", {"sha256": "a" * 64})
    assert list(outside.iterdir()) == []


def test_scene_overlay_uses_returned_versions_without_touching_physics_or_bindings(tmp_path):
    physics = tmp_path / "physics"
    scene = physics / "scene"
    scene.mkdir(parents=True)
    stage = Sdf.Layer.CreateNew(str(scene / "stage.usda"))
    stage.ImportFromString('''#usda 1.0
def Xform "World" {
 def Xform "Lab" {
  def Xform "panda_rail" {
   def Xform "panda__hand" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsArticulationRootAPI"]) {
    float physics:mass = 1
   }
  }
  def Xform "incubator" {}
  def Xform "imager" {}
  def Xform "mother" {}
 }
}
''')
    stage.Save()
    binding = {"scene": "qualified", "entities": [
        {"key": "/World/Lab/panda_rail/panda__hand", "program": "kept"}]}
    (scene / "devices.yaml").write_text(yaml.safe_dump(binding))
    receipt = {"physics": str(physics), "physicalFiles": {"scene/" + p.name: fingerprint(p)
                 for p in scene.iterdir()}, "mappings": {
                     "panda__hand": {"bodyPath": "/World/Lab/panda_rail/panda__hand"}}}
    versions = {name: "robot/physical-" + name + "@7"
                for name in ("panda-rail", "incubator", "imager", "mother")}
    versions["panda__hand"] = "robot/visual-hand@9"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = compose(workspace, receipt, versions)
    result = Sdf.Layer.FindOrOpen(str(output / "stage.usda"))
    assert result.GetPrimAtPath("/World/Lab/panda_rail").referenceList.prependedItems[0].assetPath.endswith(
        "/7/asset.usdc")
    body = result.GetPrimAtPath("/World/Lab/panda_rail/panda__hand")
    assert body.attributes["physics:mass"].default == 1
    assert "PhysicsArticulationRootAPI" in body.GetInfo("apiSchemas").prependedItems
    assert body.referenceList.prependedItems[0].assetPath == "library:assets/robot/visual-hand/9/asset.usdc"
    assert result.GetPrimAtPath(body.path.AppendChild("Appearance")).GetInfo("active") is False
    assert yaml.safe_load((output / "devices.yaml").read_text()) == binding

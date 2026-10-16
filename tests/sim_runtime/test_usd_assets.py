from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")
pytest.importorskip("pxr.Usd")
import trimesh

from packages.protocol.math import compose_pose, rotate_vector
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.sites import add_joint_anchor_sites
from packages.sim_runtime.usd_scene import usd_lab_builder

pytestmark = pytest.mark.newton


def usd_scene():
    builder = usd_lab_builder()
    anchors = add_joint_anchor_sites(builder)
    model = builder.finalize(device="cpu")
    state = model.state()
    newton.eval_fk(model, state.joint_q, state.joint_qd, state)
    return SimpleNamespace(
        builder=builder,
        model=model,
        state=state,
        anchor_sites=anchors,
        epoch=0,
        scene_id="usd-lab",
        physics_dt=1 / 240,
        solver=None,
    )


def test_y_up_usd_is_converted_once_and_labels_are_actual_prim_paths():
    sim = usd_scene()
    manifest = export_manifest(sim, session_id="usd", scene_revision="rev", revision_numeric_id=1)
    nodes = {node.entityKey: node for node in manifest.nodes}
    body = nodes["/World/MovingPart"]
    pose = sim.state.body_q.numpy()[body.streamId]
    assert pose[:3] == pytest.approx([1, -0.2, 1.5], abs=1e-6)
    assert sim.builder.up_axis == newton.Axis.Z
    assert body.visuals[0].kind == "glb"
    assert len(body.fallbackVisuals) == 2
    assert {visual.kind for visual in body.fallbackVisuals} == {"mesh", "primitive"}
    assert len(body.colliders) == 1 and body.colliders[0].kind == "primitive"
    native = next(visual for visual in body.fallbackVisuals if visual.kind == "mesh")
    loaded = trimesh.load(BytesIO(sim.meshes.read(native.meshId)), file_type="glb", force="scene")
    shape_pose = compose_pose({"p": pose[:3], "q": pose[3:]}, native.localPose.model_dump())
    vertices = np.concatenate([geometry.vertices for geometry in loaded.geometry.values()])
    world = np.array(
        [
            np.array(rotate_vector(shape_pose["q"], vertex * native.scale)) + shape_pose["p"]
            for vertex in vertices
        ]
    )
    np.testing.assert_allclose(
        [world.min(axis=0), world.max(axis=0)], [[0.9, -0.5, 1.3], [1.1, 0.1, 1.7]], atol=1e-6
    )
    assert "/World/Equipment/Spindle" in {joint.entityKey for joint in manifest.joints}
    assert "/World/Equipment/Housing" in nodes
    assert any(
        w.code == "unsupported_usd_schema" and w.entityKey == "/World/Equipment/Cable"
        for w in manifest.warnings
    )
    assert any(w.code == "asset_missing" and w.entityKey == "/World/MovingPart" for w in manifest.warnings)


def test_y_up_bound_glb_axis_and_local_pose_match_the_usd_equipment_world_bounds():
    from packages.assets.registry import AssetRegistry

    sim = usd_scene()
    manifest = export_manifest(sim, session_id="usd", scene_revision="rev", revision_numeric_id=1)
    housing = next(node for node in manifest.nodes if node.entityKey == "/World/Equipment/Housing")
    binding = housing.visuals[0]
    assert binding.kind == "glb" and binding.assetUpAxis == "Y"
    blob = AssetRegistry.from_file().read("equipment-cover.glb").data
    loaded = trimesh.load(BytesIO(blob), file_type="glb", force="scene")
    pose = compose_pose(housing.initialPose.model_dump(), binding.localPose.model_dump())
    axis_q = [2**-0.5, 0, 0, 2**-0.5]
    world = []
    for geometry in loaded.geometry.values():
        for vertex in geometry.vertices:
            converted = rotate_vector(axis_q, vertex * binding.scale)
            world.append(np.array(rotate_vector(pose["q"], converted)) + pose["p"])
    np.testing.assert_allclose(
        [np.min(world, axis=0), np.max(world, axis=0)], [[-1.1, -0.2, 0], [-0.5, 0.2, 0.6]], atol=1e-6
    )

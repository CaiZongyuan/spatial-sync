from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
import warp as wp

newton = pytest.importorskip("newton")
trimesh = pytest.importorskip("trimesh")
pytestmark = pytest.mark.newton

from packages.sim_runtime.manifest_export import export_manifest


def mesh_scene():
    builder = newton.ModelBuilder()
    body = builder.add_body(label="World/MeshBody")
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0], [0, 0, 3]], dtype=np.float32)
    indices = np.array([0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3], dtype=np.int32)
    with wp.ScopedDevice("cpu"):
        source = newton.Mesh(vertices, indices)
    builder.add_shape_mesh(body, mesh=source, scale=(2, 3, 4), label="World/MeshBody/shape")
    return SimpleNamespace(
        model=builder.finalize(device="cpu"),
        anchor_sites={},
        epoch=0,
        scene_id="mesh-fixture",
        physics_dt=1 / 240,
        solver=None,
    )


def test_newton_mesh_is_content_hash_glb_with_scale_kept_in_manifest():
    sim = mesh_scene()
    manifest = export_manifest(sim, session_id="mesh-session", scene_revision="rev", revision_numeric_id=1)
    body = next(node for node in manifest.nodes if node.kind == "body")
    visual = body.visuals[0]
    assert visual.kind == "mesh"
    assert visual.scale == (2, 3, 4) and visual.assetUpAxis == "Z"
    assert visual.meshId.startswith("sha256-")
    assert visual.url == f"/api/v1/sessions/mesh-session/meshes/{visual.meshId}"
    payload = sim.meshes.read(visual.meshId)
    assert payload[:4] == b"glTF"
    decoded = trimesh.load(BytesIO(payload), file_type="glb", force="scene")
    np.testing.assert_allclose(decoded.bounds, [[0, 0, 0], [1, 2, 3]])

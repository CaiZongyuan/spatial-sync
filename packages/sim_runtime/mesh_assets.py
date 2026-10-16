"""Immutable GLB artifacts from Newton-local triangles; shape scale stays in the Manifest."""

from hashlib import sha256

import numpy as np
import trimesh


class MeshStore:
    def __init__(self):
        self._blobs: dict[str, bytes] = {}

    def register(self, source) -> str:
        vertices = np.asarray(source.vertices, dtype=np.float32).reshape(-1, 3)
        faces = np.asarray(source.indices, dtype=np.int32).reshape(-1, 3)
        if not len(vertices) or not len(faces) or not np.isfinite(vertices).all():
            raise ValueError("mesh must have finite vertices and triangle faces")
        if faces.min() < 0 or faces.max() >= len(vertices):
            raise ValueError("mesh triangle index is outside the vertex array")
        normals = getattr(source, "normals", None)
        if normals is not None:
            normals = np.asarray(normals, dtype=np.float32)
            if normals.shape != vertices.shape or not np.isfinite(normals).all():
                raise ValueError("mesh normals must match finite vertices")
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_normals=normals, process=False)
        scene = trimesh.Scene(base_frame="world")
        scene.add_geometry(mesh, geom_name="mesh", node_name="mesh")
        blob = trimesh.exchange.gltf.export_glb(scene, include_normals=normals is not None)
        mesh_id = "sha256-" + sha256(blob).hexdigest()
        self._blobs.setdefault(mesh_id, blob)
        return mesh_id

    def read(self, mesh_id: str) -> bytes:
        return self._blobs[mesh_id]

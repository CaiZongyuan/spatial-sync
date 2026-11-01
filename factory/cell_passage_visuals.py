"""Derive bounded material-grouped GLB surfaces from canonical authored primitives."""

import numpy as np
import trimesh
from pxr import UsdGeom
from trimesh.visual.material import PBRMaterial


def export(stage):
    cache, groups = UsdGeom.XformCache(), {}
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Cube):
            mesh = trimesh.creation.box(extents=[UsdGeom.Cube(prim).GetSizeAttr().Get()] * 3)
        elif prim.IsA(UsdGeom.Cylinder):
            shape = UsdGeom.Cylinder(prim)
            mesh = trimesh.creation.cylinder(
                radius=shape.GetRadiusAttr().Get(), height=shape.GetHeightAttr().Get(), sections=32
            )
        elif prim.IsA(UsdGeom.Mesh):
            shape = UsdGeom.Mesh(prim)
            if any(value != 3 for value in shape.GetFaceVertexCountsAttr().Get()):
                raise ValueError("Authored visual mesh must be triangulated")
            mesh = trimesh.Trimesh(
                np.asarray(shape.GetPointsAttr().Get()),
                np.asarray(shape.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3),
                process=False,
            )
        else:
            continue
        mesh.apply_transform(np.asarray(cache.GetLocalToWorldTransform(prim)).T)
        gprim = UsdGeom.Gprim(prim)
        colors, opacity = gprim.GetDisplayColorAttr().Get(), gprim.GetDisplayOpacityAttr().Get()
        rgb = tuple(float(value) for value in colors[0]) if colors else (0.5, 0.5, 0.5)
        rgba = (*rgb, float(opacity[0]) if opacity else 1.0)
        groups.setdefault(rgba, []).append(mesh)
    result = trimesh.Scene(base_frame="world")
    for index, (color, meshes) in enumerate(sorted(groups.items())):
        mesh = trimesh.util.concatenate(meshes)
        mesh.visual = trimesh.visual.TextureVisuals(
            material=PBRMaterial(
                name=f"Surface{index:02d}",
                baseColorFactor=np.round(np.asarray(color) * 255).astype(np.uint8),
                metallicFactor=0.05,
                roughnessFactor=0.45,
                alphaMode="BLEND" if color[3] < 1 else "OPAQUE",
                doubleSided=True,
            )
        )
        result.add_geometry(mesh, geom_name=f"Surface{index:02d}", node_name=f"Surface{index:02d}")
    return result.export(file_type="glb")

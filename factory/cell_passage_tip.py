"""A new tip variant with actual polygon clearance and axial seating relief."""

import math

from pxr import Gf, Sdf, UsdGeom, UsdPhysics, Vt

from factory.cell_passage import CONTACT_GAP_M, TIP_SOCKET_INNER_RADIUS_M, tip

ASSET_NAME = "passage-tip-1000-relief"
SEGMENTS = 12
PROFILE = (
    (0.000, 0.0007, 0.0011),
    (0.020, 0.00248, 0.00294),
    (0.046, TIP_SOCKET_INNER_RADIUS_M / math.cos(math.pi / SEGMENTS), 0.0060),
    (0.050, TIP_SOCKET_INNER_RADIUS_M / math.cos(math.pi / SEGMENTS), 0.0060),
)
PROTOTYPE_SHA256 = "889b83f84c4b7dc55ef86297918b7c13fd44fe9b9777145244b93cde3eaf8f44"
FACES = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
         [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]


def bore_volume_ul():
    polygon_area_factor = SEGMENTS * math.sin(2 * math.pi / SEGMENTS) / 2
    return sum((high[0] - low[0]) * (low[1] ** 2 + low[1] * high[1] + high[1] ** 2) / 3
               for low, high in zip(PROFILE, PROFILE[1:])) * polygon_area_factor * 1e9


def relief(stage, root):
    import trimesh

    # Keep the old Root, mass and 50..62 mm collar. Never rewrite the old asset.
    tip(stage, root)
    stage.RemovePrim(root + "/Nozzle")
    for segment, (low, high) in enumerate(zip(PROFILE, PROFILE[1:])):
        for index in range(SEGMENTS):
            angle = index * math.pi / 6
            points = []
            for z, inner, outer in (low, high):
                for radius in (inner, outer):
                    for theta in (angle, angle + math.pi / 6):
                        points.append((radius * math.cos(theta), radius * math.sin(theta), z))
            mesh = trimesh.Trimesh(points, FACES, process=False)
            mesh.fix_normals()
            shape = UsdGeom.Mesh.Define(stage, root + f"/Nozzle/Segment{segment}/Wall{index:02d}")
            shape.CreatePointsAttr(Vt.Vec3fArray(mesh.vertices.tolist()))
            shape.CreateFaceVertexCountsAttr([3] * len(mesh.faces))
            shape.CreateFaceVertexIndicesAttr(mesh.faces.ravel().tolist())
            shape.CreateSubdivisionSchemeAttr("none")
            shape.CreateDisplayColorAttr([Gf.Vec3f(0.6, 0.8, 0.86)])
            UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
            UsdPhysics.MeshCollisionAPI.Apply(shape.GetPrim()).CreateApproximationAttr("convexHull")
            shape.GetPrim().CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(CONTACT_GAP_M)

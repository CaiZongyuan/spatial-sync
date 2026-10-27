"""Build a new FR3 rotor package with tapered tube guides from the received asset.

Offline authoring only. The output still needs Newton and public action validation.
"""

import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path

import numpy as np
import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt


def guide_sector(center, lower, upper, inner_lower, inner_upper, angle, count=16):
    points = []
    for height, inner in [(lower, inner_lower), (upper, inner_upper)]:
        for radius in (inner, .0234):
            for theta in (angle, angle + 2*math.pi/count):
                points.append([center[0]+radius*math.cos(theta), center[1]+radius*math.sin(theta), height])
    faces = [[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],
             [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
             [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]]
    mesh = trimesh.Trimesh(points, faces, process=False)
    mesh.fix_normals()
    return mesh


def build(source, output, clearance=.0008, slot_radius=.13):
    source, output = source.resolve(), output.resolve()
    if output.exists() or source == output:
        raise ValueError("A fresh output directory is required")
    metadata = yaml.safe_load((source / "asset.yaml").read_text(encoding="utf-8"))
    if metadata["id"] != "device/fr3-lab-rotor-a":
        raise ValueError("This recipe requires the received FR3 mechanical-A rotor")
    provenance = json.loads((source / "provenance.json").read_text(encoding="utf-8"))
    stage = Usd.Stage.Open(str(source / metadata["stage"]))
    stage = Usd.Stage.Open(stage.Flatten())
    root = stage.GetPrimAtPath("/Asset")
    visual = trimesh.load(source / metadata["visual"], force="scene")
    removed = []
    for prim in list(root.GetChildren()):
        if prim.IsA(UsdGeom.Mesh) and prim.HasAPI(UsdPhysics.CollisionAPI):
            points = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get())
            if len(points) == 8 and np.isclose(points[:, 2].min(), .0074099302) and np.isclose(
                points[:, 2].max(), .0416998863
            ):
                removed.append(str(prim.GetPath()))
                stage.RemovePrim(prim.GetPath())
    if len(removed) != 128:
        raise ValueError("Source cup collision recipe differs from the pinned 4x32 sectors")
    for node in list(visual.graph.nodes_geometry):
        if node.startswith("Cup") and "_" in node:
            _, geometry = visual.graph[node]
            visual.graph.transforms.remove_node(node)
            visual.geometry.pop(geometry)
    new_names = set()
    for slot in range(1, 5):
        site = UsdGeom.Xformable(stage.GetPrimAtPath(f"/Asset/Slot{slot}"))
        position = np.asarray(site.ComputeLocalToWorldTransform(0).ExtractTranslation())
        target = position.copy()
        target[:2] *= slot_radius / np.linalg.norm(position[:2])
        delta = target-position
        site.GetOrderedXformOps()[0].Set(Gf.Vec3d(*target))
        floor_index = next(index for index, row in enumerate(provenance["geometry"])
                           if row["sourceObject"].rsplit("/", 1)[-1] == f"CupFloor{slot}")
        floor = UsdGeom.Mesh(stage.GetPrimAtPath(f"/Asset/Collision{floor_index:04d}"))
        floor_points = np.asarray(floor.GetPointsAttr().Get())
        if not np.allclose(floor_points.mean(axis=0)[:2], position[:2], rtol=0, atol=1e-6):
            raise ValueError("Source cup-floor collision centre differs from its Site")
        floor.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(
            (floor_points+delta).astype(np.float32)))
        transform, geometry = visual.graph[f"CupFloor{slot}"]
        if not np.allclose(transform, np.eye(4), rtol=0, atol=1e-12):
            raise ValueError("Source cup-floor visual frame differs from the frozen recipe")
        visual.geometry[geometry].apply_translation(delta)
        position = target
        heights = [.0074099302, position[2], position[2]+.025, position[2]+.045]
        radii = [.001+clearance, .001+clearance, .0085+clearance, .014]
        for band in range(3):
            for sector in range(16):
                name = f"Guide{slot}_{band}_{sector:02d}"
                mesh = guide_sector(position[:2], heights[band], heights[band+1],
                                    radii[band], radii[band+1], sector*2*math.pi/16)
                geometry = UsdGeom.Mesh.Define(stage, "/Asset/"+name)
                geometry.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(mesh.vertices.astype(np.float32)))
                geometry.CreateFaceVertexCountsAttr([3]*len(mesh.faces))
                geometry.CreateFaceVertexIndicesAttr(mesh.faces.ravel().tolist())
                geometry.CreateSubdivisionSchemeAttr("none")
                geometry.CreateVisibilityAttr("invisible")
                UsdPhysics.CollisionAPI.Apply(geometry.GetPrim())
                UsdPhysics.MeshCollisionAPI.Apply(geometry.GetPrim()).CreateApproximationAttr("convexHull")
                geometry.GetPrim().CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(.001)
                mesh.visual = trimesh.visual.ColorVisuals(mesh, face_colors=[185, 194, 203, 255])
                visual.add_geometry(mesh, node_name=name, geom_name=name)
                new_names.add(name)
    # Estimate mass and inertia from the same solid parts used for collision and visuals.
    materials = {row["sourceObject"].rsplit("/", 1)[-1]: row.get("materialEstimate", "aluminium")
                 for row in provenance["geometry"]}
    parts = []
    for node in visual.graph.nodes_geometry:
        transform, geometry = visual.graph[node]
        mesh = visual.geometry[geometry].copy()
        mesh.apply_transform(transform)
        mesh.density = 1200 if node in new_names else {"aluminium": 2700, "polymer": 1200}[
            materials.get(node, "aluminium")]
        parts.append(mesh.mass_properties)
    total = sum(part.mass for part in parts)
    center = sum(part.mass*part.center_mass for part in parts) / total
    inertia = sum(part.inertia + part.mass*(np.dot(part.center_mass-center, part.center_mass-center)*np.eye(3)
                  - np.outer(part.center_mass-center, part.center_mass-center)) for part in parts)
    diagonal, axes = np.linalg.eigh(inertia)
    if np.linalg.det(axes) < 0:
        axes[:, 0] *= -1
    matrix = np.eye(4)
    matrix[:3, :3] = axes
    q = trimesh.transformations.quaternion_from_matrix(matrix)
    mass = UsdPhysics.MassAPI(root)
    mass.GetMassAttr().Set(float(total))
    mass.GetCenterOfMassAttr().Set(Gf.Vec3f(*center))
    mass.GetDiagonalInertiaAttr().Set(Gf.Vec3f(*diagonal))
    mass.GetPrincipalAxesAttr().Set(Gf.Quatf(float(q[0]), Gf.Vec3f(*q[1:])))
    output.mkdir(parents=True)
    stage.GetRootLayer().Export(str(output / "asset.usda"))
    (output / "visual.glb").write_bytes(visual.export(file_type="glb"))
    shutil.copyfile(source / "LICENSE.md", output / "LICENSE.md")
    metadata["description"] = (
        f"FR3 rotor with tapered supports, flared guides and {slot_radius*1000:g} mm slot radius; "
        "estimated physics.")
    metadata["stage"], metadata["visual"] = "asset.usda", "visual.glb"
    metadata["dimensionsM"] = np.ptp(visual.bounds, axis=0).tolist()
    metadata["physics"]["massKg"] = float(total)
    (output / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    provenance["derivedFromAsset"] = {
        "id": "device/fr3-lab-rotor-a@1",
        "stageSha256": hashlib.sha256((source / metadata["stage"]).read_bytes()).hexdigest(),
    }
    provenance["repair"] = {"issue": 213, "recipe": "tapered-guide-v1", "radialClearanceM": clearance,
                            "guideHeightM": .045, "entryRadiusM": .014, "guideSectors": 16,
                            "guideDensityEstimateKgM3": 1200,
                            "bodyFrameAndBottomHeightUnchanged": True, "slotRadiusM": slot_radius,
                            "nominalSitesChanged": True, "reteachingRequired": True}
    provenance["physicsVerified"] = False
    provenance["geometry"] = [{"sourceObject": node, "collisionEqualsVisual": True,
                                "materialEstimate": "polymer" if node in new_names else materials[node]}
                               for node in visual.graph.nodes_geometry]
    provenance["files"] = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                      "bytes": path.stat().st_size} for path in output.iterdir()}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return {"massKg": float(total), "colliders": len(visual.geometry), "radialClearanceM": clearance,
            "slotRadiusM": slot_radius}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2))

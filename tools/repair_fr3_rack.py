"""Author the received FR3 rack with tapered bottom inserts.

Sites are supplied in rack-local coordinates; the rack needs a scene world anchor.
The original source is cloned before editing. Physical properties are estimates.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, Vt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.repair_fr3_rotor import guide_sector


def build(source, output, sites):
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError("A fresh output directory is required")
    metadata = yaml.safe_load((source / "asset.yaml").read_text(encoding="utf-8"))
    if metadata["id"] != "labware/fr3-lab-rack":
        raise ValueError("This recipe requires the received FR3 rack")
    if set(sites) != {"A1", "A2", "A3", "B1", "B2", "B3"}:
        raise ValueError("All six rack-local bottom Sites are required")
    stage = Usd.Stage.Open(Usd.Stage.Open(str(source / metadata["stage"])).Flatten())
    body = stage.GetPrimAtPath("/Asset")
    UsdPhysics.RigidBodyAPI.Apply(body)
    mass = UsdPhysics.MassAPI.Apply(body)
    visual = trimesh.load(source / metadata["visual"], force="scene")
    for name in ["A1", "A2", "A3", "B1", "B2", "B3"]:
        p = np.asarray(sites[name])
        heights = [p[2], p[2] + 0.025, p[2] + 0.030]
        radii = [0.0018, 0.0093, 0.012]
        for band in range(2):
            for sector in range(16):
                geometry = guide_sector(
                    p[:2],
                    heights[band],
                    heights[band + 1],
                    radii[band],
                    radii[band + 1],
                    sector * 2 * np.pi / 16,
                )
                delta = geometry.vertices[:, :2] - p[:2]
                radius = np.linalg.norm(delta, axis=1)
                outer = radius > 0.020
                geometry.vertices[outer, :2] = p[:2] + delta[outer] * (0.0135 / radius[outer, None])
                label = f"Guide{name}_{band}_{sector:02d}"
                mesh = UsdGeom.Mesh.Define(stage, "/Asset/" + label)
                mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(geometry.vertices.astype(np.float32)))
                mesh.CreateFaceVertexCountsAttr([3] * len(geometry.faces))
                mesh.CreateFaceVertexIndicesAttr(geometry.faces.ravel().tolist())
                mesh.CreateSubdivisionSchemeAttr("none")
                mesh.CreateVisibilityAttr("invisible")
                UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
                UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("convexHull")
                mesh.GetPrim().CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(0.001)
                geometry.visual = trimesh.visual.ColorVisuals(geometry, face_colors=[210, 218, 223, 255])
                visual.add_geometry(geometry, node_name=label, geom_name=label)
    parts = []
    for node in visual.graph.nodes_geometry:
        transform, name = visual.graph[node]
        mesh = visual.geometry[name].copy()
        mesh.apply_transform(transform)
        mesh.density = 1200
        parts.append(mesh.mass_properties)
    total = sum(part.mass for part in parts)
    center = sum(part.mass * part.center_mass for part in parts) / total
    inertia = sum(
        part.inertia
        + part.mass
        * (
            np.dot(part.center_mass - center, part.center_mass - center) * np.eye(3)
            - np.outer(part.center_mass - center, part.center_mass - center)
        )
        for part in parts
    )
    diagonal, axes = np.linalg.eigh(inertia)
    if np.linalg.det(axes) < 0:
        axes[:, 0] *= -1
    matrix = np.eye(4)
    matrix[:3, :3] = axes
    q = trimesh.transformations.quaternion_from_matrix(matrix)
    assert total > 0 and np.all(diagonal > 0), (total, diagonal)
    mass.CreateMassAttr(float(total))
    mass.CreateCenterOfMassAttr(Gf.Vec3f(*center))
    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(*diagonal))
    mass.CreatePrincipalAxesAttr(Gf.Quatf(float(q[0]), Gf.Vec3f(*q[1:])))
    output.mkdir(parents=True)
    stage.GetRootLayer().Export(str(output / "asset.usda"))
    (output / "visual.glb").write_bytes(visual.export(file_type="glb"))
    (output / "LICENSE.md").write_bytes((source / "LICENSE.md").read_bytes())
    metadata["description"] = (
        "Rigid rack with tapered bottom inserts; six original bottom Sites retained. "
        "Fixed installation requires the scene world anchor; physical properties are estimates."
    )
    metadata["physics"] = {"massKg": float(total), "massSource": "estimated", "collision": "authored"}
    metadata["stage"] = "asset.usda"
    metadata["dimensionsM"] = np.ptp(visual.bounds, axis=0).tolist()
    (output / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    provenance = json.loads((source / "provenance.json").read_text())
    provenance["repair"] = {
        "issue": 213,
        "recipe": "rack-tapered-inserts-v1",
        "sitesUnchanged": sites,
        "outerRadiusM": 0.0135,
        "entryRadiusM": 0.012,
        "guideHeightM": 0.030,
        "estimatedDensityKgM3": 1200,
        "sceneWorldAnchorRequired": True,
    }
    provenance["physicsVerified"] = False
    provenance["files"] = {
        path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
        for path in output.iterdir()
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return {"massKg": total, "newGuides": 192, "sites": sites}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--recipe", type=Path, default=Path(__file__).with_name("fr3_repair_recipe.json"))
    args = parser.parse_args()
    sites = json.loads(args.recipe.read_text())["rackSites"]
    print(json.dumps(build(args.source, args.output, sites), indent=2))

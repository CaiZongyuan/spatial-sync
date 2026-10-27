"""Author the received sealed FR3 tube with an outer collision proxy.

The original appearance, mass, inertia and bottom frame remain unchanged. The
result needs import and physical qualification; it does not qualify pouring or
contact training. Use an environment with the project's simulation/USD extras.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import trimesh
import yaml
from pxr import Sdf, Usd, UsdGeom, UsdPhysics, Vt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.library.grasp_calibration import tube_bounds


def build(source, output):
    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise ValueError("A fresh output directory is required")
    metadata = yaml.safe_load((source / "asset.yaml").read_text(encoding="utf-8"))
    if metadata["id"] not in {f"labware/fr3-lab-tube-{index:02d}" for index in range(1, 7)}:
        raise ValueError("This recipe requires a received FR3 mechanical-A tube")
    source_stage = source / metadata["stage"]
    stage = Usd.Stage.Open(Usd.Stage.Open(str(source_stage)).Flatten())
    before = tube_bounds(stage, "/Asset")
    body = stage.GetPrimAtPath("/Asset")
    mass = UsdPhysics.MassAPI(body)
    mass_fields = (mass.GetMassAttr(), mass.GetCenterOfMassAttr(), mass.GetDiagonalInertiaAttr(),
                   mass.GetPrincipalAxesAttr())
    mass_values = tuple(field.Get() for field in mass_fields)
    removed = 0
    for child in list(body.GetChildren()):
        if child.HasAPI(UsdPhysics.CollisionAPI):
            stage.RemovePrim(child.GetPath())
            removed += 1
    if removed != 245:
        raise ValueError("Source tube collision recipe differs from the received 245 parts")
    points = []
    for height, radius in [(0, .001), (.025, .0085)]:
        points.extend([[radius*np.cos(angle), radius*np.sin(angle), height]
                       for angle in np.arange(24)*2*np.pi/24])
    foot = trimesh.convex.convex_hull(np.array(points))
    mesh = UsdGeom.Mesh.Define(stage, "/Asset/CollisionFoot")
    mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(foot.vertices.astype(np.float32)))
    mesh.CreateFaceVertexCountsAttr([3]*len(foot.faces))
    mesh.CreateFaceVertexIndicesAttr(foot.faces.ravel().tolist())
    mesh.CreateSubdivisionSchemeAttr("none")
    mesh.CreateVisibilityAttr("invisible")
    UsdPhysics.CollisionAPI.Apply(mesh.GetPrim())
    UsdPhysics.MeshCollisionAPI.Apply(mesh.GetPrim()).CreateApproximationAttr("convexHull")
    shapes = [mesh.GetPrim()]
    for name, radius, lower, upper in [("Shaft", .0085, .025, .115), ("Cap", .00952, .115, .129)]:
        shape = UsdGeom.Cylinder.Define(stage, "/Asset/Collision"+name)
        shape.CreateAxisAttr("Z")
        shape.CreateRadiusAttr(radius)
        shape.CreateHeightAttr(upper-lower)
        shape.AddTranslateOp().Set((0, 0, (lower+upper)/2))
        shape.CreateVisibilityAttr("invisible")
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
        shapes.append(shape.GetPrim())
    for shape in shapes:
        shape.CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(.001)
    after = tube_bounds(stage, "/Asset")
    np.testing.assert_allclose(before["boundsM"], after["boundsM"], rtol=0, atol=1e-6)
    if tuple(field.Get() for field in mass_fields) != mass_values:
        raise ValueError("The collision recipe changed the retained mass properties")
    output.mkdir(parents=True)
    stage.GetRootLayer().Export(str(output / "asset.usda"))
    shutil.copyfile(source / metadata["visual"], output / "visual.glb")
    shutil.copyfile(source / "LICENSE.md", output / "LICENSE.md")
    metadata["stage"], metadata["visual"] = "asset.usda", "visual.glb"
    metadata["description"] = (
        "Sealed rigid tube with an outer collision proxy. Original appearance, mass, inertia and bottom "
        "frame retained; pouring and contact training need separate qualification.")
    (output / "asset.yaml").write_text(yaml.safe_dump(metadata, sort_keys=False), encoding="utf-8")
    provenance = json.loads((source / "provenance.json").read_text(encoding="utf-8"))
    provenance["sourceGeometry"] = provenance.pop("geometry")
    provenance["repair"] = {
        "issue": 213, "recipe": "sealed-tube-envelope-v1", "sourceAsset": metadata["id"]+"@1",
        "sourceStageSha256": hashlib.sha256(source_stage.read_bytes()).hexdigest(),
        "sourceColliderCount": removed, "colliderCount": 3,
        "boundsBefore": before["boundsM"], "boundsAfter": after["boundsM"],
        "massInertiaBottomFrameUnchanged": True, "closedRigidContainerOnly": True,
    }
    provenance["physicsVerified"] = False
    provenance["files"] = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                       "bytes": path.stat().st_size} for path in output.iterdir()}
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return {"sourceColliderCount": removed, "colliderCount": 3, "boundsM": after["boundsM"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), indent=2))

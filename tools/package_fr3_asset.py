"""Derive the built-in FR3 package from the repository's pinned platform URDF.

CPU file conversion only: no Newton initialization or physics validation. Run with
uv run --with pycollada==0.9.3 --with usd-core==26.8 tools/package_fr3_asset.py
"""

import hashlib
import importlib.metadata
import json
import math
import platform
import shutil
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.library.assets import metadata

SOURCE = ROOT / "assets/robots/franka_emika_panda"
OUTPUT = ROOT / "library/assets/robot/fr3/1"
# Same rest pose and open fingers as the existing platform FR3, without world placement.
HOME = dict(zip((f"fr3_joint{i}" for i in range(1, 8)), (0, -0.4, 0, -2, 0, 1.6, 0.7)))
HOME |= {"fr3_finger_joint1": 0.04, "fr3_finger_joint2": 0.04}


def fingerprint(path):
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def vector(value, default="0 0 0"):
    return np.array([float(number) for number in (value or default).split()])


def origin(element):
    result = np.eye(4)
    if element is not None:
        result = trimesh.transformations.euler_matrix(*vector(element.get("rpy")))
        result[:3, 3] = vector(element.get("xyz"))
    return result


def quaternion(matrix):
    values = trimesh.transformations.quaternion_from_matrix(matrix)
    return Gf.Quatf(float(values[0]), Gf.Vec3f(*values[1:]))


def transform(prim, matrix):
    UsdGeom.Xformable(prim).AddTransformOp().Set(Gf.Matrix4d(matrix.T.tolist()))


def mesh_prim(stage, path, mesh, *, collision=False):
    geometry = UsdGeom.Mesh.Define(stage, path)
    geometry.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(mesh.vertices, dtype=np.float32)))
    geometry.CreateFaceVertexCountsAttr(Vt.IntArray.FromNumpy(np.full(len(mesh.faces), 3, dtype=np.int32)))
    geometry.CreateFaceVertexIndicesAttr(
        Vt.IntArray.FromNumpy(np.asarray(mesh.faces, dtype=np.int32).ravel())
    )
    geometry.CreateSubdivisionSchemeAttr("none")
    if collision:
        UsdPhysics.CollisionAPI.Apply(geometry.GetPrim())
        UsdPhysics.MeshCollisionAPI.Apply(geometry.GetPrim()).CreateApproximationAttr("convexHull")
        geometry.CreatePurposeAttr("guide")
    else:
        geometry.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(mesh.vertex_normals, dtype=np.float32)))
        geometry.SetNormalsInterpolation("vertex")
        material = getattr(mesh.visual, "material", None)
        if material is not None:
            material = material.to_pbr() if hasattr(material, "to_pbr") else material
            color = (
                np.asarray(
                    material.baseColorFactor
                    if material.baseColorFactor is not None
                    else [255, 255, 255, 255],
                    dtype=float,
                )
                / 255
            )
            usd_material = UsdShade.Material.Define(stage, path + "Material")
            shader = UsdShade.Shader.Define(stage, path + "Material/Surface")
            shader.CreateIdAttr("UsdPreviewSurface")
            shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color[:3]))
            shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(color[3]))
            for name, value in (
                ("metallic", material.metallicFactor),
                ("roughness", material.roughnessFactor),
            ):
                if value is not None:
                    shader.CreateInput(name, Sdf.ValueTypeNames.Float).Set(float(value))
            usd_material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
            UsdShade.MaterialBindingAPI.Apply(geometry.GetPrim()).Bind(usd_material)
    return geometry


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    ledger = json.loads((SOURCE / "PROVENANCE.json").read_text(encoding="utf-8"))
    for relative, expected in ledger["files"].items():
        assert fingerprint(SOURCE.parent / relative) == expected, relative
    robot = ET.parse(SOURCE / "urdf/fr3_franka_hand.urdf").getroot()
    links = {link.attrib["name"]: link for link in robot.findall("link")}
    joints = robot.findall("joint")
    parent_joint = {joint.find("child").get("link"): joint for joint in joints}
    poses = {"base": np.eye(4)}
    for joint in joints:
        parent, child = joint.find("parent").get("link"), joint.find("child").get("link")
        motion = np.eye(4)
        value = HOME.get(joint.get("name"), 0)
        axis = vector(joint.find("axis").get("xyz")) if joint.find("axis") is not None else None
        if joint.get("type") == "revolute":
            motion = trimesh.transformations.rotation_matrix(value, axis)
        elif joint.get("type") == "prismatic":
            motion[:3, 3] = axis * value
        poses[child] = poses[parent] @ origin(joint.find("origin")) @ motion
    stage_path = OUTPUT / "asset.usdc"
    # A new stage avoids retaining removed prims when regenerating the same version.
    stage = Usd.Stage.CreateInMemory()
    asset = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(asset.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    UsdPhysics.ArticulationRootAPI.Apply(asset.GetPrim())
    physical = {name for name, link in links.items() if link.find("inertial") is not None}
    visuals, mass, triangles = trimesh.Scene(base_frame="Asset"), 0, 0
    for name in sorted(physical):
        link, pose = links[name], poses[name]
        body = UsdGeom.Xform.Define(stage, f"/Asset/{name}")
        transform(body.GetPrim(), pose)
        UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
        inertia = link.find("inertial")
        link_mass = float(inertia.find("mass").get("value"))
        mass += link_mass
        mass_api = UsdPhysics.MassAPI.Apply(body.GetPrim())
        mass_api.CreateMassAttr(link_mass)
        inertial_frame = origin(inertia.find("origin"))
        mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*inertial_frame[:3, 3]))
        values = inertia.find("inertia").attrib
        tensor = np.array(
            [
                [
                    float(values["i" + left + right]) if left <= right else float(values["i" + right + left])
                    for right in "xyz"
                ]
                for left in "xyz"
            ]
        )
        diagonal, axes = np.linalg.eigh(tensor)
        if np.linalg.det(axes) < 0:
            axes[:, 0] *= -1
        principal = np.eye(4)
        principal[:3, :3] = inertial_frame[:3, :3] @ axes
        mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(*diagonal))
        mass_api.CreatePrincipalAxesAttr(quaternion(principal))
        for category in ("visual", "collision"):
            for index, element in enumerate(link.findall(category)):
                local = origin(element.find("origin"))
                mesh = element.find("geometry/mesh")
                if mesh is not None:
                    source = SOURCE.parent / mesh.get("filename").removeprefix("package://")
                    scene = trimesh.load_scene(source, process=False)
                    scale = vector(mesh.get("scale"), "1 1 1")
                    local[:3, :3] = local[:3, :3] @ np.diag(scale)
                else:
                    box = element.find("geometry/box")
                    if box is None:
                        raise ValueError("Unexpected geometry in pinned FR3 source")
                    scene = trimesh.Scene(trimesh.creation.box(extents=vector(box.get("size"))))
                for subindex, node in enumerate(scene.graph.nodes_geometry):
                    matrix, geometry_name = scene.graph[node]
                    geometry = scene.geometry[geometry_name]
                    path = f"/Asset/{name}/{category}{index}_{subindex}"
                    prim = mesh_prim(stage, path, geometry, collision=category == "collision")
                    transform(prim.GetPrim(), local @ matrix)
                    if category == "visual":
                        visuals.add_geometry(
                            geometry.copy(), node_name=path, geom_name=path, transform=pose @ local @ matrix
                        )
                        triangles += len(geometry.faces)
    for joint in joints:
        child, parent = joint.find("child").get("link"), joint.find("parent").get("link")
        if child not in physical:
            continue  # Empty fixed URDF frames are folded into the next physical joint's local frame.
        joint_frame = poses[parent] @ origin(joint.find("origin"))
        while parent not in physical and parent in parent_joint:
            parent = parent_joint[parent].find("parent").get("link")
        kind = joint.get("type")
        joint_class = {
            "fixed": UsdPhysics.FixedJoint,
            "revolute": UsdPhysics.RevoluteJoint,
            "prismatic": UsdPhysics.PrismaticJoint,
        }[kind]
        usd_joint = joint_class.Define(stage, "/Asset/" + joint.get("name"))
        local = np.linalg.inv(poses[parent]) @ joint_frame if parent in physical else joint_frame
        if parent in physical:
            usd_joint.CreateBody0Rel().SetTargets([f"/Asset/{parent}"])
        usd_joint.CreateBody1Rel().SetTargets([f"/Asset/{child}"])
        usd_joint.CreateLocalPos0Attr(Gf.Vec3f(*local[:3, 3]))
        usd_joint.CreateLocalRot0Attr(quaternion(local))
        usd_joint.CreateLocalPos1Attr(Gf.Vec3f(0))
        usd_joint.CreateLocalRot1Attr(Gf.Quatf(1))
        usd_joint.CreateCollisionEnabledAttr(False)
        if kind != "fixed":
            axis = vector(joint.find("axis").get("xyz"))
            assert tuple(axis) in ((0, 0, 1), (0, 1, 0))
            usd_joint.CreateAxisAttr("Z" if axis[2] else "Y")
            limit = joint.find("limit")
            factor = 180 / math.pi if kind == "revolute" else 1
            usd_joint.CreateLowerLimitAttr(float(limit.get("lower")) * factor)
            usd_joint.CreateUpperLimitAttr(float(limit.get("upper")) * factor)
            if joint.find("mimic") is not None:
                usd_joint.GetPrim().SetCustomDataByKey("urdfMimicJoint", joint.find("mimic").get("joint"))
    tcp = UsdGeom.Xform.Define(stage, "/Asset/fr3_hand/tcp")
    tcp.AddTranslateOp().Set(Gf.Vec3d(0, 0, 0.1034))
    stage.GetRootLayer().Export(str(stage_path))
    (OUTPUT / "visual.glb").write_bytes(visuals.export(file_type="glb"))
    shutil.copyfile(SOURCE / "LICENSE", OUTPUT / "LICENSE")
    shutil.copyfile(SOURCE / "README.md", OUTPUT / "UPSTREAM_README.md")
    (OUTPUT / "source").mkdir(exist_ok=True)
    shutil.copyfile(SOURCE / "urdf/fr3_franka_hand.urdf", OUTPUT / "source/fr3_franka_hand.urdf")
    shutil.copyfile(SOURCE / "PROVENANCE.json", OUTPUT / "source/PROVENANCE.json")
    data = {
        "id": "robot/fr3",
        "kind": "robot",
        "title": "Franka Research 3 with Franka Hand",
        "description": "Pinned platform FR3: seven arm joints and two fingers. CPU file conversion; "
        "structure and physics validation are pending. URDF mimic is retained as metadata.",
        "stage": "asset.usdc",
        "visual": "visual.glb",
        "dimensionsM": np.ptp(visuals.bounds, axis=0).tolist(),
        "physics": {"massKg": mass, "massSource": "estimated", "collision": "authored"},
        "implements": [
            {
                "type": "robot-arm/v1",
                "programs": {"newton": "robot_arm.newton"},
                "config": {
                    "jointKeys": [f"fr3_joint{i}" for i in range(1, 8)],
                    "fingerKeys": [f"fr3_finger_joint{i}" for i in (1, 2)],
                    "handBody": "fr3_hand",
                    "tcpSite": "fr3_hand/tcp",
                    "speedScale": 0.5,
                    "graspMode": "weld",
                },
            }
        ],
        "dressing": False,
    }
    (OUTPUT / "asset.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8", newline="\n")
    files = {
        file.relative_to(OUTPUT).as_posix(): fingerprint(file)
        for file in sorted(OUTPUT.rglob("*"))
        if file.is_file() and file.name not in ("provenance.json", "validation.json")
    }
    provenance = {
        "repository": ledger["repository"],
        "revision": ledger["revision"],
        "upstreamDescriptionRevision": "d9841cf (as recorded by the retained upstream README)",
        "license": ledger["license"],
        "sourceEntrypoint": ledger["entrypoint"],
        "sourceFiles": ledger["files"],
        "files": files,
        "producer": {"path": "tools/package_fr3_asset.py", **fingerprint(Path(__file__))},
        "toolVersions": {
            "python": platform.python_version(),
            **{
                name: importlib.metadata.version(name)
                for name in ("trimesh", "numpy", "pycollada", "usd-core", "pyyaml", "pydantic")
            },
        },
        "conversion": {
            "device": "cpu",
            "poseRadOrM": HOME,
            "visualTriangles": triangles,
            "rigidBodies": len(physical),
            "physicsValidation": "not run",
            "limitations": [
                "URDF mimic retained as metadata; no USD mimic drive is authored",
                "Drives and runtime program integration require later validation",
            ],
        },
    }
    (OUTPUT / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    metadata(OUTPUT)
    report = {
        "schema": "lw.validation/1",
        "subject": "asset:robot/fr3@1",
        "ranAt": datetime.now(UTC).isoformat(),
        "launchable": False,
        "toolVersions": {"pydantic": importlib.metadata.version("pydantic")},
        "checks": [
            {
                "id": "S1",
                "tier": "structure",
                "status": "pass",
                "message": "asset.yaml matches the asset package schema",
            },
            {
                "id": "coverage",
                "tier": "structure",
                "status": "warn",
                "message": "Remaining structure and physics checks have not run",
            },
            {
                "id": "massSource",
                "tier": "physics",
                "status": "warn",
                "message": "Masses and inertias copied from pinned URDF; not independently measured",
            },
        ],
    }
    (OUTPUT / "validation.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "triangles": triangles,
                "massKg": mass,
                "dimensionsM": data["dimensionsM"],
                "files": len(files),
            }
        )
    )


if __name__ == "__main__":
    main()

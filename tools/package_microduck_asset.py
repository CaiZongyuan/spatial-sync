"""Derive the built-in Microduck robot asset package from the upstream MJCF source.

Converts ``robot_groundcontact.xml`` (15 rigid bodies, 14 actuated revolute joints,
7 named sensor/contact sites) and its STL meshes into a canonical Z-up OpenUSD
articulation (``asset.usdc``), PBR ``visual.glb``, ``asset.yaml``, Apache-2.0
``LICENSE``, and ``provenance.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import trimesh
import yaml
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.library.assets import AssetLibrary
from packages.library.assets import metadata as parse_asset_metadata
from packages.library.visuals import glb_budget

DEFAULT_SOURCE = Path(os.environ.get("MICRODUCK_SOURCE", "/root/project/microduck"))
DEFAULT_OUTPUT = ROOT / "library/assets/robot/microduck/1"
UPSTREAM_REPOSITORY = "https://github.com/pollen-robotics/microduck"
FALLBACK_REVISION = "5984efb770855432b03dafd3d879e9929981e45b"
ENTRYPOINT_REL = "microduck_rl/src/mjlab_microduck/robot/microduck/robot_groundcontact.xml"
CONSTANTS_REL = "microduck_rl/src/mjlab_microduck/robot/microduck_constants.py"

# Upstream STAND2 keyframe (radians) from mjlab_microduck/robot/microduck_constants.py.
JOINT_ORDER = (
    "left_hip_yaw",
    "left_hip_roll",
    "left_hip_pitch",
    "left_knee",
    "left_ankle",
    "neck_pitch",
    "head_pitch",
    "head_yaw",
    "head_roll",
    "right_hip_yaw",
    "right_hip_roll",
    "right_hip_pitch",
    "right_knee",
    "right_ankle",
)

HOME_FRAME: dict[str, float] = {
    "left_hip_yaw": 0.0,
    "left_hip_roll": -0.0873,
    "left_hip_pitch": -0.4579,
    "left_knee": -0.0049,
    "left_ankle": 0.4530,
    "neck_pitch": 0.3491,
    "head_pitch": 0.3491,
    "head_yaw": 0.0,
    "head_roll": 0.0,
    "right_hip_yaw": 0.0,
    "right_hip_roll": 0.0873,
    "right_hip_pitch": 0.4579,
    "right_knee": 0.0049,
    "right_ankle": -0.4530,
}

# Upstream chosen_actuator drive parameters (ADR-0007).
ACTUATOR_KP_NM_PER_RAD = 0.55
ACTUATOR_KD_NMS_PER_RAD = 0.053
ACTUATOR_MAX_FORCE_NM = 0.96
ACTUATOR_ARMATURE = 0.0018
ACTUATOR_FRICTIONLOSS = 0.0048

# Static equilibrium trunk pitch (deg) for STAND2 under soft 0.55 N*m/rad PD drives.
TRUNK_EQUILIBRIUM_PITCH_DEG = -5.010211

# Compact box colliders (body-local center [m], full box extents [m]) for the 7
# intermediate links that carry positive mass in robot_groundcontact.xml without
# authored collision geoms, satisfying P4 (50 <= density <= 20000 kg/m^3) and P3.
INTERMEDIATE_LINK_COLLIDERS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "yaw2roll": ((0.0, 0.001, 0.015), (0.014, 0.014, 0.016)),
    "bearing_roll": ((0.0, 0.001, 0.015), (0.014, 0.014, 0.016)),
    "upper_leg_left": ((0.007, 0.020, 0.013), (0.014, 0.024, 0.020)),
    "upper_leg_right": ((-0.007, 0.020, 0.013), (0.014, 0.024, 0.020)),
    "neck": ((0.0, -0.025, 0.014), (0.014, 0.024, 0.016)),
    "neck_pitch": ((0.0, 0.012, -0.0145), (0.010, 0.010, 0.010)),
    "yaw_roll_motion": ((0.0, -0.005, 0.016), (0.016, 0.016, 0.020)),
}

INTERNAL_MESH_PREFIXES = (
    "seeed_bearing",
    "xl330",
    "pcb__",
    "elec_rpi",
    "banana_pcb",
    "power_support",
    "motor_support",
    "speaker",
)


def fingerprint(path: Path) -> dict[str, str | int]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def parse_vec(raw: str | None, default: tuple[float, ...] = (0.0, 0.0, 0.0)) -> np.ndarray:
    if not raw:
        return np.asarray(default, dtype=np.float64)
    return np.asarray([float(item) for item in raw.split()], dtype=np.float64)


def quat_wxyz_to_mat(quat_wxyz: np.ndarray | list[float]) -> np.ndarray:
    q = np.asarray(quat_wxyz, dtype=np.float64)
    q = q / np.linalg.norm(q)
    w, x, y, z = q
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)],
            [2.0 * (x * y + w * z), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - w * x)],
            [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def pose_matrix(pos: np.ndarray | list[float], quat_wxyz: np.ndarray | list[float]) -> np.ndarray:
    mat = np.eye(4, dtype=np.float64)
    mat[:3, :3] = quat_wxyz_to_mat(quat_wxyz)
    mat[:3, 3] = np.asarray(pos, dtype=np.float64)
    return mat


def mat_to_gf_quatf(rot: np.ndarray) -> Gf.Quatf:
    hom = np.eye(4, dtype=np.float64)
    hom[:3, :3] = rot
    q = trimesh.transformations.quaternion_from_matrix(hom)
    q = q / np.linalg.norm(q)
    return Gf.Quatf(float(q[0]), Gf.Vec3f(float(q[1]), float(q[2]), float(q[3])))


def set_translate_orient(prim: Usd.Prim, mat: np.ndarray) -> None:
    xf = UsdGeom.Xformable(prim)
    xf.ClearXformOpOrder()
    xf.AddTranslateOp().Set(Gf.Vec3d(*mat[:3, 3].tolist()))
    xf.AddOrientOp(UsdGeom.XformOp.PrecisionFloat).Set(mat_to_gf_quatf(mat[:3, :3]))


def set_matrix_transform(prim: Usd.Prim, col_major_or_hom: np.ndarray) -> None:
    """Author a single USD row-vector Matrix4d from a standard column-vector 4x4 matrix."""
    xf = UsdGeom.Xformable(prim)
    xf.ClearXformOpOrder()
    xf.AddTransformOp().Set(Gf.Matrix4d(col_major_or_hom.T.tolist()))


def decimate_visual_mesh(mesh: trimesh.Trimesh, *, internal: bool) -> trimesh.Trimesh:
    """Deterministic isotropic vertex-clustering decimation to stay well inside S6 GLB budget."""
    target_max = 1600 if internal else 5500
    if len(mesh.faces) <= target_max:
        return mesh.copy()

    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    lo = verts.min(axis=0)
    span = float(np.ptp(verts, axis=0).max())
    if span <= 1e-9:
        return mesh.copy()

    res = 22 if internal else 48
    simplified_verts, simplified_faces = verts, faces
    for grid_res in (res, max(12, res - 6), max(10, res - 10)):
        cell = span / float(grid_res)
        coords = np.floor((verts - lo) / cell).astype(np.int64)
        _, inv = np.unique(coords, axis=0, return_inverse=True)
        n_clusters = int(inv.max()) + 1
        new_verts = np.zeros((n_clusters, 3), dtype=np.float64)
        counts = np.bincount(inv, minlength=n_clusters).astype(np.float64)
        for axis in range(3):
            new_verts[:, axis] = np.bincount(inv, weights=verts[:, axis], minlength=n_clusters) / counts
        mapped = inv[faces]
        non_deg = (
            (mapped[:, 0] != mapped[:, 1])
            & (mapped[:, 1] != mapped[:, 2])
            & (mapped[:, 0] != mapped[:, 2])
        )
        mapped = mapped[non_deg]
        if len(mapped) == 0:
            break
        canon = np.sort(mapped, axis=1)
        _, unique_idx = np.unique(canon, axis=0, return_index=True)
        simplified_faces = mapped[np.sort(unique_idx)]
        simplified_verts = new_verts
        if len(simplified_faces) <= target_max:
            break

    out = trimesh.Trimesh(vertices=simplified_verts, faces=simplified_faces, process=True)
    return out


def upstream_revision(source_root: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", "-c", f"safe.directory={source_root}", "-C", str(source_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        rev = proc.stdout.strip()
        if len(rev) == 40:
            return rev
    except (OSError, subprocess.SubprocessError):
        pass
    return FALLBACK_REVISION


def package_microduck(source_root: Path, output_dir: Path) -> dict[str, object]:
    xml_path = source_root / ENTRYPOINT_REL
    license_path = source_root / "LICENSE"
    constants_path = source_root / CONSTANTS_REL
    if not xml_path.is_file() or not license_path.is_file():
        raise FileNotFoundError(f"Upstream Microduck source not found at {source_root}")

    output_dir.mkdir(parents=True, exist_ok=True)
    root = ET.parse(xml_path).getroot()
    asset_section = root.find("asset")
    assert asset_section is not None

    mesh_paths: dict[str, Path] = {}
    for mesh_el in asset_section.findall("mesh"):
        file_attr = mesh_el.attrib["file"]
        name = mesh_el.attrib.get("name") or Path(file_attr).stem
        mesh_paths[name] = xml_path.parent / "assets" / file_attr

    materials_rgba: dict[str, np.ndarray] = {}
    for mat_el in asset_section.findall("material"):
        materials_rgba[mat_el.attrib["name"]] = parse_vec(mat_el.attrib.get("rgba"), (0.8, 0.8, 0.8, 1.0))

    raw_meshes = {name: trimesh.load_mesh(mpath, process=True) for name, mpath in sorted(mesh_paths.items())}
    hull_meshes = {name: mesh.convex_hull for name, mesh in raw_meshes.items()}
    vis_meshes = {
        name: decimate_visual_mesh(mesh, internal=name.startswith(INTERNAL_MESH_PREFIXES))
        for name, mesh in raw_meshes.items()
    }

    pitch_rad = math.radians(TRUNK_EQUILIBRIUM_PITCH_DEG)
    trunk_init = pose_matrix(
        [0.0, 0.0, 0.20],
        [math.cos(pitch_rad / 2.0), 0.0, math.sin(pitch_rad / 2.0), 0.0],
    )

    bodies_info: list[
        tuple[str, str | None, ET.Element, ET.Element | None, np.ndarray, np.ndarray, np.ndarray]
    ] = []

    def walk_body(body_el: ET.Element, parent_name: str | None, parent_world: np.ndarray) -> None:
        bname = body_el.attrib["name"]
        rel_pos = parse_vec(body_el.attrib.get("pos"), (0.0, 0.0, 0.0))
        rel_quat = parse_vec(body_el.attrib.get("quat"), (1.0, 0.0, 0.0, 0.0))
        rel_quat = rel_quat / np.linalg.norm(rel_quat)
        rel_mat = pose_matrix(rel_pos, rel_quat)
        joint_el = body_el.find("joint")
        if parent_name is None:
            world_mat = trunk_init.copy()
        else:
            assert joint_el is not None, f"Missing joint on child body {bname}"
            jname = joint_el.attrib["name"]
            q_init = HOME_FRAME[jname]
            rot_j = trimesh.transformations.rotation_matrix(q_init, [0.0, 0.0, 1.0])
            world_mat = parent_world @ rel_mat @ rot_j
        bodies_info.append((bname, parent_name, body_el, joint_el, rel_pos, rel_quat, world_mat))
        for child_el in body_el.findall("body"):
            walk_body(child_el, bname, world_mat)

    worldbody = root.find("worldbody")
    assert worldbody is not None
    trunk_el = worldbody.find("body")
    assert trunk_el is not None
    walk_body(trunk_el, None, np.eye(4, dtype=np.float64))
    assert len(bodies_info) == 15, f"Expected 15 rigid bodies, got {len(bodies_info)}"

    # Place STAND2 sole collision hulls flush on z = 0.
    min_z = math.inf
    for _, _, body_el, _, _, _, world_mat in bodies_info:
        for geom_el in body_el.findall("geom"):
            if geom_el.attrib.get("class") != "collision":
                continue
            mname = geom_el.attrib["mesh"]
            g_mat = world_mat @ pose_matrix(
                parse_vec(geom_el.attrib.get("pos"), (0.0, 0.0, 0.0)),
                parse_vec(geom_el.attrib.get("quat"), (1.0, 0.0, 0.0, 0.0)),
            )
            pts_w = (hull_meshes[mname].vertices @ g_mat[:3, :3].T) + g_mat[:3, 3]
            min_z = min(min_z, float(pts_w[:, 2].min()))
    z_shift = -min_z

    stage = Usd.Stage.CreateInMemory()
    asset_xform = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(asset_xform.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    UsdPhysics.ArticulationRootAPI.Apply(asset_xform.GetPrim())

    UsdGeom.Scope.Define(stage, "/Asset/PhysicsMaterials")
    foot_mat_prim = stage.DefinePrim("/Asset/PhysicsMaterials/FootMaterial", "Material")
    foot_phys_mat = UsdPhysics.MaterialAPI.Apply(foot_mat_prim)
    foot_phys_mat.CreateStaticFrictionAttr(1.0)
    foot_phys_mat.CreateDynamicFrictionAttr(1.0)
    foot_phys_mat.CreateRestitutionAttr(0.0)

    UsdGeom.Scope.Define(stage, "/Asset/VisualMaterials")
    usd_materials: dict[str, UsdShade.Material] = {}
    for mat_name, rgba in sorted(materials_rgba.items()):
        mat_path = f"/Asset/VisualMaterials/{mat_name}"
        usd_mat = UsdShade.Material.Define(stage, mat_path)
        shader = UsdShade.Shader.Define(stage, f"{mat_path}/Surface")
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
            Gf.Vec3f(float(rgba[0]), float(rgba[1]), float(rgba[2]))
        )
        shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(float(rgba[3]))
        is_metal = "bearing" in mat_name
        shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.25 if is_metal else 0.0)
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.30 if is_metal else 0.45)
        usd_mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        usd_materials[mat_name] = usd_mat

    # Non-Xformable Scope avoids colliding with body /Asset/neck_pitch and keeps /Asset/joints out of sites.
    UsdGeom.Scope.Define(stage, "/Asset/joints")

    glb_scene = trimesh.Scene(base_frame="Asset")
    glb_geom_cache: dict[tuple[str, str], str] = {}
    total_mass = 0.0
    usd_visual_triangles = 0
    discovered_joints: list[str] = []

    for bname, parent_name, body_el, joint_el, rel_pos, rel_quat, raw_world_mat in bodies_info:
        world_mat = raw_world_mat.copy()
        world_mat[2, 3] += z_shift
        inv_world_mat = np.linalg.inv(world_mat)

        body_prim = UsdGeom.Xform.Define(stage, f"/Asset/{bname}").GetPrim()
        set_translate_orient(body_prim, world_mat)
        UsdPhysics.RigidBodyAPI.Apply(body_prim)

        inert = body_el.find("inertial")
        assert inert is not None, f"Missing inertial on {bname}"
        link_mass = float(inert.attrib["mass"])
        total_mass += link_mass
        ipos = parse_vec(inert.attrib.get("pos"), (0.0, 0.0, 0.0))
        fi = parse_vec(inert.attrib["fullinertia"])
        inertia_tensor = np.array(
            [
                [fi[0], fi[3], fi[4]],
                [fi[3], fi[1], fi[5]],
                [fi[4], fi[5], fi[2]],
            ],
            dtype=np.float64,
        )
        diag, axes = np.linalg.eigh(inertia_tensor)
        if np.linalg.det(axes) < 0.0:
            axes[:, 0] *= -1.0
        mass_api = UsdPhysics.MassAPI.Apply(body_prim)
        mass_api.CreateMassAttr(link_mass)
        mass_api.CreateCenterOfMassAttr(Gf.Vec3f(*ipos.tolist()))
        mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(*diag.tolist()))
        mass_api.CreatePrincipalAxesAttr(mat_to_gf_quatf(axes))

        vis_idx = 0
        col_idx = 0
        for geom_el in body_el.findall("geom"):
            geom_class = geom_el.attrib.get("class", "")
            if geom_class not in ("visual", "collision"):
                continue
            mname = geom_el.attrib["mesh"]
            g_local = pose_matrix(
                parse_vec(geom_el.attrib.get("pos"), (0.0, 0.0, 0.0)),
                parse_vec(geom_el.attrib.get("quat"), (1.0, 0.0, 0.0, 0.0)),
            )
            g_world = world_mat @ g_local

            if geom_class == "visual":
                vmesh = vis_meshes[mname]
                mat_name = geom_el.attrib.get("material", "")
                rgba = materials_rgba.get(mat_name, np.array([0.8, 0.8, 0.8, 1.0]))
                vpath = f"/Asset/{bname}/visual_{vis_idx}"
                vis_idx += 1

                # Express mesh vertices in world-aligned STAND2 frame so UsdGeom.BBoxCache has
                # zero rotated-AABB corner inflation while preserving exact rigid link kinematics.
                pts_w = (np.asarray(vmesh.vertices, dtype=np.float64) @ g_world[:3, :3].T) + g_world[:3, 3]
                norms_w = np.asarray(vmesh.vertex_normals, dtype=np.float64) @ g_world[:3, :3].T

                u_mesh = UsdGeom.Mesh.Define(stage, vpath)
                u_mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(pts_w, dtype=np.float32)))
                u_mesh.CreateFaceVertexCountsAttr(
                    Vt.IntArray.FromNumpy(np.full(len(vmesh.faces), 3, dtype=np.int32))
                )
                u_mesh.CreateFaceVertexIndicesAttr(
                    Vt.IntArray.FromNumpy(np.asarray(vmesh.faces, dtype=np.int32).ravel())
                )
                u_mesh.CreateNormalsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(norms_w, dtype=np.float32)))
                u_mesh.SetNormalsInterpolation("vertex")
                u_mesh.CreateSubdivisionSchemeAttr("none")
                set_matrix_transform(u_mesh.GetPrim(), inv_world_mat)
                if mat_name in usd_materials:
                    UsdShade.MaterialBindingAPI.Apply(u_mesh.GetPrim()).Bind(usd_materials[mat_name])
                usd_visual_triangles += int(len(vmesh.faces))

                cache_key = (mname, mat_name)
                if cache_key not in glb_geom_cache:
                    colored = vmesh.copy()
                    base_u8 = np.clip(np.round(rgba * 255.0), 0, 255).astype(np.uint8)
                    is_metal = "bearing" in mat_name
                    colored.visual = trimesh.visual.TextureVisuals(
                        material=trimesh.visual.material.PBRMaterial(
                            name=mat_name or mname,
                            baseColorFactor=base_u8,
                            metallicFactor=0.25 if is_metal else 0.0,
                            roughnessFactor=0.30 if is_metal else 0.45,
                        )
                    )
                    geom_id = f"{mname}__{mat_name}" if mat_name else mname
                    glb_scene.add_geometry(colored, node_name=vpath, geom_name=geom_id, transform=g_world)
                    glb_geom_cache[cache_key] = geom_id
                else:
                    glb_scene.graph.update(
                        frame_to=vpath,
                        frame_from="Asset",
                        matrix=g_world,
                        geometry=glb_geom_cache[cache_key],
                    )
            else:
                hull = hull_meshes[mname]
                cpath = f"/Asset/{bname}/collision_{col_idx}"
                col_idx += 1
                c_mesh = UsdGeom.Mesh.Define(stage, cpath)
                is_sole = bname in ("ankle_left", "ankle_right")
                if is_sole:
                    pts_w = (np.asarray(hull.vertices, dtype=np.float64) @ g_world[:3, :3].T) + g_world[:3, 3]
                    c_mesh.CreatePointsAttr(Vt.Vec3fArray.FromNumpy(np.asarray(pts_w, dtype=np.float32)))
                    set_matrix_transform(c_mesh.GetPrim(), inv_world_mat)
                    # "render" is included in BBoxCache floor alignment yet hidden in Three.js viewport.
                    c_mesh.CreatePurposeAttr("render")
                else:
                    c_mesh.CreatePointsAttr(
                        Vt.Vec3fArray.FromNumpy(np.asarray(hull.vertices, dtype=np.float32))
                    )
                    set_translate_orient(c_mesh.GetPrim(), g_local)
                    c_mesh.CreatePurposeAttr("guide")

                c_mesh.CreateFaceVertexCountsAttr(
                    Vt.IntArray.FromNumpy(np.full(len(hull.faces), 3, dtype=np.int32))
                )
                c_mesh.CreateFaceVertexIndicesAttr(
                    Vt.IntArray.FromNumpy(np.asarray(hull.faces, dtype=np.int32).ravel())
                )
                c_mesh.CreateSubdivisionSchemeAttr("none")
                UsdPhysics.CollisionAPI.Apply(c_mesh.GetPrim())
                UsdPhysics.MeshCollisionAPI.Apply(c_mesh.GetPrim()).CreateApproximationAttr("convexHull")
                if is_sole:
                    UsdShade.MaterialBindingAPI.Apply(c_mesh.GetPrim()).Bind(
                        UsdShade.Material(foot_mat_prim),
                        UsdShade.Tokens.weakerThanDescendants,
                        "physics",
                    )

        if bname in INTERMEDIATE_LINK_COLLIDERS:
            cpos, csize = INTERMEDIATE_LINK_COLLIDERS[bname]
            cpath = f"/Asset/{bname}/collision_{col_idx}"
            cube = UsdGeom.Cube.Define(stage, cpath)
            cube.CreateSizeAttr(1.0)
            cube.CreatePurposeAttr("guide")
            xf = UsdGeom.Xformable(cube.GetPrim())
            xf.AddTranslateOp().Set(Gf.Vec3d(*cpos))
            xf.AddScaleOp(UsdGeom.XformOp.PrecisionFloat).Set(Gf.Vec3f(*csize))
            UsdPhysics.CollisionAPI.Apply(cube.GetPrim())

        for site_el in body_el.findall("site"):
            sname = site_el.attrib["name"]
            s_local = pose_matrix(
                parse_vec(site_el.attrib.get("pos"), (0.0, 0.0, 0.0)),
                parse_vec(site_el.attrib.get("quat"), (1.0, 0.0, 0.0, 0.0)),
            )
            site_prim = UsdGeom.Xform.Define(stage, f"/Asset/{bname}/{sname}").GetPrim()
            set_translate_orient(site_prim, s_local)

        if parent_name is not None and joint_el is not None:
            jname = joint_el.attrib["name"]
            discovered_joints.append(jname)
            j_obj = UsdPhysics.RevoluteJoint.Define(stage, f"/Asset/joints/{jname}")
            j_prim = j_obj.GetPrim()
            j_obj.CreateBody0Rel().SetTargets([f"/Asset/{parent_name}"])
            j_obj.CreateBody1Rel().SetTargets([f"/Asset/{bname}"])
            j_obj.CreateLocalPos0Attr(Gf.Vec3f(*rel_pos.tolist()))
            j_obj.CreateLocalRot0Attr(Gf.Quatf(float(rel_quat[0]), Gf.Vec3f(*rel_quat[1:].tolist())))
            j_obj.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
            j_obj.CreateLocalRot1Attr(Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0)))
            j_obj.CreateCollisionEnabledAttr(False)
            axis_vec = parse_vec(joint_el.attrib.get("axis"), (0.0, 0.0, 1.0))
            assert np.allclose(axis_vec, [0.0, 0.0, 1.0]), f"Unexpected axis {axis_vec} on {jname}"
            j_obj.CreateAxisAttr("Z")
            lo_rad, hi_rad = [float(val) for val in joint_el.attrib["range"].split()]
            j_obj.CreateLowerLimitAttr(math.degrees(lo_rad))
            j_obj.CreateUpperLimitAttr(math.degrees(hi_rad))

            q_deg = math.degrees(HOME_FRAME[jname])
            drive = UsdPhysics.DriveAPI.Apply(j_prim, "angular")
            drive.CreateTypeAttr("force")
            drive.CreateStiffnessAttr(ACTUATOR_KP_NM_PER_RAD * (math.pi / 180.0))
            drive.CreateDampingAttr(ACTUATOR_KD_NMS_PER_RAD * (math.pi / 180.0))
            drive.CreateMaxForceAttr(ACTUATOR_MAX_FORCE_NM)
            drive.CreateTargetPositionAttr(q_deg)

            j_prim.CreateAttribute(
                "state:angular:physics:position", Sdf.ValueTypeNames.Float, custom=False
            ).Set(q_deg)
            j_prim.CreateAttribute("mjc:armature", Sdf.ValueTypeNames.Float, custom=True).Set(
                ACTUATOR_ARMATURE
            )
            j_prim.CreateAttribute("mjc:frictionloss", Sdf.ValueTypeNames.Float, custom=True).Set(
                ACTUATOR_FRICTIONLOSS
            )

    assert tuple(discovered_joints) == JOINT_ORDER, (
        f"Joint order mismatch: {discovered_joints} != {list(JOINT_ORDER)}"
    )

    stage_path = output_dir / "asset.usdc"
    with tempfile.NamedTemporaryFile(suffix=".usdc", dir=output_dir, delete=False) as tmp_stage:
        tmp_stage_path = Path(tmp_stage.name)
    stage.GetRootLayer().Export(str(tmp_stage_path))
    tmp_stage_path.replace(stage_path)

    glb_path = output_dir / "visual.glb"
    glb_path.write_bytes(glb_scene.export(file_type="glb"))
    glb_triangles = glb_budget(glb_path)

    shutil.copyfile(license_path, output_dir / "LICENSE")

    default_pos = [round(HOME_FRAME[j], 6) for j in JOINT_ORDER]
    bow_target = [
        0.0,
        -0.0873,
        -0.6379,
        -0.0049,
        0.413,
        0.45,
        0.45,
        0.0,
        0.0,
        0.0,
        0.0873,
        0.6379,
        0.0049,
        -0.413,
    ]
    fall_postures = {
        "front": [
            0.0,
            -0.0873,
            0.5,
            -0.5,
            -0.6,
            1.0,
            1.1,
            0.0,
            0.0,
            0.0,
            0.0873,
            -0.5,
            0.5,
            0.6,
        ],
        "back": [
            0.0,
            -0.0873,
            -1.1,
            -0.0049,
            0.9,
            -0.6,
            -0.6,
            0.0,
            0.0,
            0.0,
            0.0873,
            1.1,
            0.0049,
            -0.9,
        ],
        "left": [
            0.0,
            0.36,
            -1.1,
            1.2,
            0.8,
            0.3491,
            0.3491,
            0.0,
            0.41,
            0.0,
            0.36,
            0.2,
            0.0,
            -0.2,
        ],
        "right": [
            0.0,
            -0.36,
            -0.2,
            0.0,
            0.2,
            0.3491,
            0.3491,
            0.0,
            -0.41,
            0.0,
            -0.36,
            1.1,
            -1.2,
            -0.8,
        ],
    }

    dims = [round(float(val), 6) for val in np.ptp(glb_scene.bounds, axis=0).tolist()]
    asset_yaml = {
        "id": "robot/microduck",
        "kind": "robot",
        "title": "Pollen Robotics Microduck Biped",
        "description": (
            "Canonical Z-up OpenUSD biped robot derived from upstream Microduck "
            "robot_groundcontact.xml (15 rigid bodies, 14 XL330 revolute joints, "
            "STAND2 equilibrium posture, standard UsdPhysics angular drives)."
        ),
        "stage": "asset.usdc",
        "visual": "visual.glb",
        "dimensionsM": dims,
        "physics": {
            "massKg": round(total_mass, 8),
            "massSource": "datasheet",
            "collision": "authored",
        },
        "simulation": {
            "solver": "mujoco",
            "warmupSteps": 1,
            "physicsDt": 0.002,
        },
        "implements": [
            {
                "type": "biped/v1",
                "programs": {
                    "newton": "biped.posture",
                },
                "config": {
                    "baseBody": "trunk_base",
                    "jointKeys": [f"joints/{name}" for name in JOINT_ORDER],
                    "defaultPosition": default_pos,
                    "bowMinPitchDeg": 12.0,
                    "maxTiltDeg": 55.0,
                    "uprightTiltDeg": 20.0,
                    "xyTolerance": 0.08,
                    "yawTolerance": 0.25,
                    "dwellSeconds": 0.25,
                    "actionTargets": {
                        "standUp": default_pos,
                        "bow": bow_target,
                    },
                    "fallPostures": fall_postures,
                },
            }
        ],
        "dressing": False,
    }
    (output_dir / "asset.yaml").write_text(
        yaml.safe_dump(asset_yaml, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )

    tracked_sources = [license_path, xml_path]
    if constants_path.is_file():
        tracked_sources.append(constants_path)
    tracked_sources.extend(sorted(mesh_paths.values()))
    source_files = {
        src.relative_to(source_root).as_posix(): fingerprint(src)
        for src in sorted(set(tracked_sources))
    }

    package_files = {
        file.relative_to(output_dir).as_posix(): fingerprint(file)
        for file in sorted(output_dir.rglob("*"))
        if file.is_file() and file.name not in ("provenance.json", "validation.json")
    }

    provenance = {
        "repository": UPSTREAM_REPOSITORY,
        "revision": upstream_revision(source_root),
        "license": "Apache-2.0; original upstream LICENSE retained",
        "sourceEntrypoint": ENTRYPOINT_REL,
        "sourceFiles": source_files,
        "files": package_files,
        "producer": {
            "path": "tools/package_microduck_asset.py",
            **fingerprint(Path(__file__).resolve()),
        },
        "toolVersions": {
            "python": platform.python_version(),
            **{
                pkg: importlib.metadata.version(pkg)
                for pkg in ("trimesh", "numpy", "usd-core", "pyyaml", "pydantic")
            },
        },
        "conversion": {
            "device": "cpu",
            "poseName": "STAND2",
            "trunkEquilibriumPitchDeg": TRUNK_EQUILIBRIUM_PITCH_DEG,
            "poseRad": HOME_FRAME,
            "actuator": {
                "class": "chosen_actuator",
                "kpNmPerRad": ACTUATOR_KP_NM_PER_RAD,
                "kdNmsPerRad": ACTUATOR_KD_NMS_PER_RAD,
                "maxForceNm": ACTUATOR_MAX_FORCE_NM,
                "armature": ACTUATOR_ARMATURE,
                "frictionloss": ACTUATOR_FRICTIONLOSS,
            },
            "rigidBodies": len(bodies_info),
            "actuatedJoints": len(JOINT_ORDER),
            "usdVisualTriangles": usd_visual_triangles,
            "glbUniqueTriangles": glb_triangles,
            "notes": [
                (
                    "Pure USD physics drives authored per ADR-0007; bam plugins, "
                    "passive backlash hinges, and rollers omitted"
                ),
                (
                    "Joints authored under non-Xformable /Asset/joints Scope to "
                    "avoid prim-path collision with body neck_pitch"
                ),
                (
                    "Compact guide Cube colliders authored on 7 intermediate "
                    "positive-mass links for P4 mass-density compliance"
                ),
            ],
        },
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    parse_asset_metadata(output_dir)

    return {
        "output": str(output_dir),
        "massKg": round(total_mass, 8),
        "dimensionsM": dims,
        "usdVisualTriangles": usd_visual_triangles,
        "glbUniqueTriangles": glb_triangles,
        "files": len(package_files),
    }


def validate_asset_package(output_dir: Path, state_dir: Path) -> dict[str, object]:
    from apps.api.app.main import program_schemas
    from packages.library.validation import StructuralValidator

    user_lib = state_dir / "user_library"
    user_lib.mkdir(parents=True, exist_ok=True)
    assets = AssetLibrary(state_dir / "initial", builtin_root=ROOT / "library", user_root=user_lib)
    assets.startup()
    version = assets.resolve("robot/microduck@1")
    validator = StructuralValidator(state_dir / "validator", programs=program_schemas)
    report = validator.run(
        version.path,
        "asset",
        f"asset:{version.id}",
        version.metadata,
        assets,
    )
    (output_dir / "validation.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    # Re-index on a fresh AssetLibrary with committed validation.json and verify revalidate().
    final_assets = AssetLibrary(state_dir / "final", builtin_root=ROOT / "library", user_root=user_lib)
    final_assets.startup()
    finalized = final_assets.resolve("robot/microduck@1")
    validator.revalidate(finalized, "asset", final_assets)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="Upstream microduck repo path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output asset version directory")
    parser.add_argument("--validate", action="store_true", help="Run StructuralValidator (S1-S8 + P1-P6)")
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Scratch state directory when --validate is set",
    )
    args = parser.parse_args()

    summary = package_microduck(args.source.resolve(), args.output.resolve())
    if args.validate:
        if args.state_dir is not None:
            report = validate_asset_package(args.output.resolve(), args.state_dir.resolve())
        else:
            with tempfile.TemporaryDirectory(prefix="microduck-validate-") as tmp:
                report = validate_asset_package(args.output.resolve(), Path(tmp))
        summary["launchable"] = report["launchable"]
        summary["checks"] = {c["id"]: c["status"] for c in report["checks"]}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

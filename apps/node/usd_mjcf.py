"""Deterministic USD-to-MuJoCo (MJCF / MjSpec) articulation derivation bridge.

This module stays free of gateway/library imports so compute-node runners can copy it
into isolated trainer sandboxes as ``lw_usd_mjcf`` (ADR-0006, ADR-0007).
"""

from __future__ import annotations

import json
import math
import os
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

QuatWXYZ = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]

_AXIS_VECTORS: dict[str, Vec3] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}

# Quaternions rotating MuJoCo's default local +Z axis onto +X or +Y.
_Z_TO_AXIS_QUAT: dict[str, QuatWXYZ] = {
    "X": (math.sqrt(0.5), 0.0, math.sqrt(0.5), 0.0),
    "Y": (math.sqrt(0.5), -math.sqrt(0.5), 0.0, 0.0),
    "Z": (1.0, 0.0, 0.0, 0.0),
}


@dataclass(frozen=True)
class DerivedJoint:
    name: str
    prim_path: str
    kind: Literal["hinge", "slide"]
    axis: Vec3
    pos: Vec3
    limited: bool
    lower: float
    upper: float
    default_position: float
    stiffness: float
    damping: float
    max_force: float | None
    armature: float
    frictionloss: float
    actuated: bool


@dataclass(frozen=True)
class DerivedArticulation:
    model_name: str
    root_body: str
    fixed_base: bool
    root_pos: Vec3
    root_quat: QuatWXYZ
    joints: tuple[DerivedJoint, ...]
    sites: tuple[str, ...]
    mjcf_xml: str

    @property
    def joint_names(self) -> tuple[str, ...]:
        return tuple(joint.name for joint in self.joints)

    @property
    def joint_prim_paths(self) -> tuple[str, ...]:
        return tuple(joint.prim_path for joint in self.joints)

    def default_joint_positions(self) -> dict[str, float]:
        return {joint.name: joint.default_position for joint in self.joints}

    def to_mjspec(self):
        import mujoco

        return mujoco.MjSpec.from_string(self.mjcf_xml)

    def to_mjmodel(self):
        return self.to_mjspec().compile()


def _quat_normalize(q: QuatWXYZ) -> QuatWXYZ:
    norm = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if norm <= 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    inv = 1.0 / norm
    if q[0] < 0.0:
        inv = -inv
    return (q[0] * inv, q[1] * inv, q[2] * inv, q[3] * inv)


def _quat_conj(q: QuatWXYZ) -> QuatWXYZ:
    return (q[0], -q[1], -q[2], -q[3])


def _quat_mul(a: QuatWXYZ, b: QuatWXYZ) -> QuatWXYZ:
    return _quat_normalize(
        (
            a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3],
            a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2],
            a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1],
            a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0],
        )
    )


def _quat_rotate(q: QuatWXYZ, v: Vec3) -> Vec3:
    qw, qx, qy, qz = _quat_normalize(q)
    tx = 2.0 * (qy * v[2] - qz * v[1])
    ty = 2.0 * (qz * v[0] - qx * v[2])
    tz = 2.0 * (qx * v[1] - qy * v[0])
    return (
        v[0] + qw * tx + (qy * tz - qz * ty),
        v[1] + qw * ty + (qz * tx - qx * tz),
        v[2] + qw * tz + (qx * ty - qy * tx),
    )


def _pose_mul(p1: Vec3, q1: QuatWXYZ, p2: Vec3, q2: QuatWXYZ) -> tuple[Vec3, QuatWXYZ]:
    rot_p2 = _quat_rotate(q1, p2)
    return (
        (p1[0] + rot_p2[0], p1[1] + rot_p2[1], p1[2] + rot_p2[2]),
        _quat_mul(q1, q2),
    )


def _pose_inv(p: Vec3, q: QuatWXYZ) -> tuple[Vec3, QuatWXYZ]:
    qc = _quat_conj(q)
    neg = _quat_rotate(qc, (-p[0], -p[1], -p[2]))
    return (neg, qc)


def _gf_quat_to_wxyz(quat: Any) -> QuatWXYZ:
    if quat is None:
        return (1.0, 0.0, 0.0, 0.0)
    imag = quat.GetImaginary()
    return _quat_normalize((float(quat.GetReal()), float(imag[0]), float(imag[1]), float(imag[2])))


def _gf_vec3(vec: Any, scale: float = 1.0) -> Vec3:
    if vec is None:
        return (0.0, 0.0, 0.0)
    return (float(vec[0]) * scale, float(vec[1]) * scale, float(vec[2]) * scale)


def _decompose_matrix(mat: Gf.Matrix4d, meters_per_unit: float) -> tuple[Vec3, QuatWXYZ, Vec3]:
    pos = mat.ExtractTranslation()
    sx = Gf.Vec3d(mat[0][0], mat[0][1], mat[0][2]).GetLength() or 1.0
    sy = Gf.Vec3d(mat[1][0], mat[1][1], mat[1][2]).GetLength() or 1.0
    sz = Gf.Vec3d(mat[2][0], mat[2][1], mat[2][2]).GetLength() or 1.0
    ortho = Gf.Matrix4d(mat).RemoveScaleShear()
    quat = _gf_quat_to_wxyz(ortho.ExtractRotationQuat())
    return (
        (float(pos[0]) * meters_per_unit, float(pos[1]) * meters_per_unit, float(pos[2]) * meters_per_unit),
        quat,
        (float(sx), float(sy), float(sz)),
    )


def _fmt(values: Sequence[float]) -> str:
    cleaned = [0.0 if abs(float(v)) < 1e-12 else float(v) for v in values]
    return " ".join(f"{v:.9g}" for v in cleaned)


def _fmt_scalar(value: float) -> str:
    v = 0.0 if abs(float(value)) < 1e-12 else float(value)
    return f"{v:.9g}"


def _resolve_asset_packages(asset_packages: Mapping[str, str | Path] | None) -> dict[str, Path]:
    if asset_packages is not None:
        return {str(key): Path(val) for key, val in asset_packages.items()}
    raw = os.environ.get("LW_ASSET_PACKAGES", "").strip()
    if not raw:
        return {}
    parsed = json.loads(raw)
    return {str(key): Path(val) for key, val in parsed.items()}


def _open_usd_stage(
    source: str | Path | Usd.Stage, asset_packages: Mapping[str, str | Path] | None
) -> Usd.Stage:
    if isinstance(source, Usd.Stage):
        return source
    path = Path(source).resolve()
    packages = _resolve_asset_packages(asset_packages)
    root_layer = Sdf.Layer.FindOrOpen(str(path))
    if root_layer is None:
        raise ValueError(f"Unable to open USD layer: {path}")
    exported = root_layer.ExportToString()
    if "library:" not in exported and not packages:
        stage = Usd.Stage.Open(root_layer)
        if stage is None:
            raise ValueError(f"Unable to compose USD stage: {path}")
        return stage

    anon = Sdf.Layer.CreateAnonymous(path.name)
    anon.TransferContent(root_layer)

    def _rewrite(asset_path: str) -> str:
        if not asset_path:
            return asset_path
        if asset_path.startswith("library:"):
            parts = PurePosixPath(asset_path.removeprefix("library:")).parts
            if len(parts) >= 5 and parts[0] == "assets":
                kind, name, rev = parts[1], parts[2], parts[3]
                rel = Path(*parts[4:])
                pkg_root = (
                    packages.get(f"{kind}/{name}@{rev}")
                    or packages.get(f"asset/{kind}/{name}@{rev}")
                    or packages.get(f"assets/{kind}/{name}/{rev}")
                )
                if pkg_root is None:
                    raise ValueError(f"Unresolved USD library asset reference: {asset_path}")
                return (pkg_root / rel).resolve().as_posix()
            raise ValueError(f"Unsupported USD library URI: {asset_path}")
        candidate = Path(asset_path)
        if not candidate.is_absolute():
            resolved = (path.parent / candidate).resolve()
            if resolved.exists():
                return resolved.as_posix()
        return asset_path

    UsdUtils.ModifyAssetPaths(anon, _rewrite)
    stage = Usd.Stage.Open(anon)
    if stage is None:
        raise ValueError(f"Unable to compose rewritten USD stage: {path}")
    return stage


def _is_rigid_body(prim: Usd.Prim) -> bool:
    if not prim or not prim.IsValid() or not prim.IsActive():
        return False
    if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
        return False
    enabled = UsdPhysics.RigidBodyAPI(prim).GetRigidBodyEnabledAttr().Get()
    return enabled is not False


def _enclosing_rigid_body(stage: Usd.Stage, target_path: Sdf.Path, scope_path: Sdf.Path) -> Usd.Prim | None:
    prim = stage.GetPrimAtPath(target_path)
    while prim and prim.IsValid() and prim.GetPath() != Sdf.Path.absoluteRootPath:
        if _is_rigid_body(prim):
            if prim.GetPath().HasPrefix(scope_path):
                return prim
            return None
        prim = prim.GetParent()
    return None


def _custom_float(prim: Usd.Prim, names: Sequence[str], default: float = 0.0) -> float:
    for name in names:
        attr = prim.GetAttribute(name)
        if attr and attr.IsValid() and attr.HasAuthoredValue():
            val = attr.Get()
            if val is not None and math.isfinite(float(val)):
                return float(val)
    return default


def _mesh_is_volumetric(vertices: list[Vec3]) -> bool:
    if len(vertices) < 4:
        return False
    p0 = vertices[0]
    # Find farthest point from p0
    v1 = max(vertices[1:], key=lambda p: (p[0] - p0[0]) ** 2 + (p[1] - p0[1]) ** 2 + (p[2] - p0[2]) ** 2)
    dx1, dy1, dz1 = v1[0] - p0[0], v1[1] - p0[1], v1[2] - p0[2]
    if dx1 * dx1 + dy1 * dy1 + dz1 * dz1 < 1e-12:
        return False

    def _cross_norm2(p: Vec3) -> float:
        ux, uy, uz = p[0] - p0[0], p[1] - p0[1], p[2] - p0[2]
        cx = dy1 * uz - dz1 * uy
        cy = dz1 * ux - dx1 * uz
        cz = dx1 * uy - dy1 * ux
        return cx * cx + cy * cy + cz * cz

    v2 = max(vertices[1:], key=_cross_norm2)
    if _cross_norm2(v2) < 1e-12:
        return False
    ux, uy, uz = v2[0] - p0[0], v2[1] - p0[1], v2[2] - p0[2]
    nx = dy1 * uz - dz1 * uy
    ny = dz1 * ux - dx1 * uz
    nz = dx1 * uy - dy1 * ux
    return any(
        abs((p[0] - p0[0]) * nx + (p[1] - p0[1]) * ny + (p[2] - p0[2]) * nz) > 1e-7
        for p in vertices[1:]
    )


def _unique_name(desired: str, used: set[str]) -> str:
    clean = desired.strip().replace("/", "_") or "item"
    if clean not in used:
        used.add(clean)
        return clean
    idx = 2
    while f"{clean}_{idx}" in used:
        idx += 1
    chosen = f"{clean}_{idx}"
    used.add(chosen)
    return chosen


def derive_usd_mjcf(
    source: str | Path | Usd.Stage,
    *,
    prim_path: str | None = None,
    asset_packages: Mapping[str, str | Path] | None = None,
    fixed_base: bool | None = None,
    include_ground_plane: bool = False,
    include_static_colliders: bool = True,
    model_name: str | None = None,
) -> DerivedArticulation:
    """Derive a deterministic MuJoCo MJCF model and structured metadata from a Z-up USD stage."""
    stage = _open_usd_stage(source, asset_packages)
    up_axis = str(UsdGeom.GetStageUpAxis(stage) or "Z").upper()
    if up_axis != "Z":
        raise ValueError(f"Expected canonical Z-up USD stage, got upAxis={up_axis!r}")

    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    kg_per_unit = float(UsdPhysics.GetStageKilogramsPerUnit(stage) or 1.0)
    xform_cache = UsdGeom.XformCache()

    if prim_path is not None:
        scope_prim = stage.GetPrimAtPath(prim_path)
        if not scope_prim or not scope_prim.IsValid():
            raise ValueError(f"USD prim_path not found: {prim_path}")
    else:
        scope_prim = stage.GetDefaultPrim()
        if not scope_prim or not scope_prim.IsValid():
            children = [p for p in stage.GetPseudoRoot().GetChildren() if p.IsActive()]
            if not children:
                raise ValueError("USD stage has no active root prims")
            scope_prim = children[0]

    scope_path = scope_prim.GetPath()
    all_prims = list(Usd.PrimRange(scope_prim, Usd.TraverseInstanceProxies()))
    rigid_bodies = [prim for prim in all_prims if _is_rigid_body(prim)]
    if not rigid_bodies:
        raise ValueError(f"No active UsdPhysics.RigidBodyAPI prims found under {scope_path}")
    body_by_path = {prim.GetPath(): prim for prim in rigid_bodies}

    # Discover enabled joints within scope (and any stage-level joints targeting bodies in scope)
    joint_candidates: list[Usd.Prim] = []
    seen_joint_paths: set[Sdf.Path] = set()
    for prim in [*all_prims, *Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies())]:
        if prim.GetPath() in seen_joint_paths or not prim.IsActive() or not prim.IsA(UsdPhysics.Joint):
            continue
        j_api = UsdPhysics.Joint(prim)
        if j_api.GetJointEnabledAttr().Get() is False:
            continue
        seen_joint_paths.add(prim.GetPath())
        joint_candidates.append(prim)

    # Map child_body_path -> (parent_body_path | None, joint_prim)
    incoming_joint: dict[Sdf.Path, tuple[Sdf.Path | None, Usd.Prim]] = {}
    children_by_parent: dict[Sdf.Path, list[tuple[Sdf.Path, Usd.Prim]]] = {p: [] for p in body_by_path}

    for j_prim in joint_candidates:
        j_api = UsdPhysics.Joint(j_prim)
        b0_targets = j_api.GetBody0Rel().GetTargets()
        b1_targets = j_api.GetBody1Rel().GetTargets()
        if not b1_targets:
            continue
        child_prim = _enclosing_rigid_body(stage, b1_targets[0], scope_path)
        if child_prim is None:
            continue
        parent_prim = _enclosing_rigid_body(stage, b0_targets[0], scope_path) if b0_targets else None
        c_path = child_prim.GetPath()
        p_path = parent_prim.GetPath() if parent_prim is not None else None
        if c_path in incoming_joint:
            continue
        incoming_joint[c_path] = (p_path, j_prim)
        if p_path is not None:
            children_by_parent[p_path].append((c_path, j_prim))

    #Also connect nested rigid bodies without explicit joints as fixed children
    for b_path, b_prim in body_by_path.items():
        if b_path in incoming_joint:
            continue
        anc = b_prim.GetParent()
        while anc and anc.IsValid() and anc.GetPath().HasPrefix(scope_path):
            if anc.GetPath() in body_by_path:
                incoming_joint[b_path] = (anc.GetPath(), Usd.Prim())
                children_by_parent[anc.GetPath()].append((b_path, Usd.Prim()))
                break
            anc = anc.GetParent()

    root_candidates = [
        b_path
        for b_path in body_by_path
        if b_path not in incoming_joint or incoming_joint[b_path][0] is None
    ]
    if not root_candidates:
        raise ValueError(f"Cyclic rigid body graph under {scope_path}")
    root_path = root_candidates[0]
    root_prim = body_by_path[root_path]

    world_joint_prim = incoming_joint.get(root_path, (None, Usd.Prim()))[1]
    inferred_fixed = bool(
        world_joint_prim
        and world_joint_prim.IsValid()
        and world_joint_prim.IsA(UsdPhysics.FixedJoint)
    )
    is_fixed_base = inferred_fixed if fixed_base is None else bool(fixed_base)

    # Compute root pose in world coordinates
    scope_world_pos, scope_world_quat, _ = _decompose_matrix(
        xform_cache.GetLocalToWorldTransform(scope_prim), meters_per_unit
    )
    if world_joint_prim and world_joint_prim.IsValid():
        wj = UsdPhysics.Joint(world_joint_prim)
        # Empty body0 inside a referenced asset is relative to scope_prim.
        b0_targets = wj.GetBody0Rel().GetTargets()
        if b0_targets:
            b0_prim = stage.GetPrimAtPath(b0_targets[0])
            frame0_pos, frame0_quat, s0 = _decompose_matrix(
                xform_cache.GetLocalToWorldTransform(b0_prim), meters_per_unit
            )
        else:
            j_parent = world_joint_prim.GetParent()
            if j_parent and j_parent.IsValid() and j_parent.GetPath().HasPrefix(scope_path):
                frame0_pos, frame0_quat, s0 = _decompose_matrix(
                    xform_cache.GetLocalToWorldTransform(j_parent), meters_per_unit
                )
            else:
                frame0_pos, frame0_quat, s0 = scope_world_pos, scope_world_quat, (1.0, 1.0, 1.0)
        lp0 = wj.GetLocalPos0Attr().Get()
        lr0 = _gf_quat_to_wxyz(wj.GetLocalRot0Attr().Get())
        p0_local = (
            float(lp0[0]) * s0[0] * meters_per_unit if lp0 is not None else 0.0,
            float(lp0[1]) * s0[1] * meters_per_unit if lp0 is not None else 0.0,
            float(lp0[2]) * s0[2] * meters_per_unit if lp0 is not None else 0.0,
        )
        w_j0_pos, w_j0_quat = _pose_mul(frame0_pos, frame0_quat, p0_local, lr0)

        _, _, s1 = _decompose_matrix(xform_cache.GetLocalToWorldTransform(root_prim), meters_per_unit)
        lp1 = wj.GetLocalPos1Attr().Get()
        lr1 = _gf_quat_to_wxyz(wj.GetLocalRot1Attr().Get())
        p1_local = (
            float(lp1[0]) * s1[0] * meters_per_unit if lp1 is not None else 0.0,
            float(lp1[1]) * s1[1] * meters_per_unit if lp1 is not None else 0.0,
            float(lp1[2]) * s1[2] * meters_per_unit if lp1 is not None else 0.0,
        )
        inv_j1_pos, inv_j1_quat = _pose_inv(p1_local, lr1)
        root_pos, root_quat = _pose_mul(w_j0_pos, w_j0_quat, inv_j1_pos, inv_j1_quat)
    else:
        root_pos, root_quat, _ = _decompose_matrix(
            xform_cache.GetLocalToWorldTransform(root_prim), meters_per_unit
        )

    mj_root = ET.Element("mujoco", {"model": model_name or scope_prim.GetName() or "usd_articulation"})
    ET.SubElement(mj_root, "compiler", {"angle": "radian", "autolimits": "true"})
    asset_elem = ET.SubElement(mj_root, "asset")
    worldbody_elem = ET.SubElement(mj_root, "worldbody")
    actuator_elem = ET.SubElement(mj_root, "actuator")

    used_body_names: set[str] = set()
    used_joint_names: set[str] = set()
    used_geom_names: set[str] = set()
    used_mesh_names: set[str] = set()
    used_site_names: set[str] = set()

    derived_joints: list[DerivedJoint] = []
    derived_sites: list[str] = []
    has_ground_plane = False

    if include_static_colliders:
        for prim in Usd.PrimRange(stage.GetPseudoRoot(), Usd.TraverseInstanceProxies()):
            if not prim.IsActive() or prim.GetPath().HasPrefix(scope_path):
                continue
            if prim.GetTypeName() == "Plane" and prim.HasAPI(UsdPhysics.CollisionAPI):
                g_pos, g_quat, _ = _decompose_matrix(
                    xform_cache.GetLocalToWorldTransform(prim), meters_per_unit
                )
                g_name = _unique_name(prim.GetName() or "ground", used_geom_names)
                ET.SubElement(
                    worldbody_elem,
                    "geom",
                    {
                        "name": g_name,
                        "type": "plane",
                        "pos": _fmt(g_pos),
                        "quat": _fmt(g_quat),
                        "size": "0 0 0.05",
                        "contype": "1",
                        "conaffinity": "1",
                    },
                )
                has_ground_plane = True

    if include_ground_plane and not has_ground_plane:
        g_name = _unique_name("ground", used_geom_names)
        ET.SubElement(
            worldbody_elem,
            "geom",
            {
                "name": g_name,
                "type": "plane",
                "pos": "0 0 0",
                "size": "0 0 0.05",
                "contype": "1",
                "conaffinity": "1",
            },
        )

    def _extract_joint_spec(j_prim: Usd.Prim, child_scale: Vec3) -> DerivedJoint | None:
        if not j_prim or not j_prim.IsValid() or j_prim.IsA(UsdPhysics.FixedJoint):
            return None
        j_api = UsdPhysics.Joint(j_prim)
        lp1 = j_api.GetLocalPos1Attr().Get()
        lr1 = _gf_quat_to_wxyz(j_api.GetLocalRot1Attr().Get())
        anchor_in_child = (
            float(lp1[0]) * child_scale[0] * meters_per_unit if lp1 is not None else 0.0,
            float(lp1[1]) * child_scale[1] * meters_per_unit if lp1 is not None else 0.0,
            float(lp1[2]) * child_scale[2] * meters_per_unit if lp1 is not None else 0.0,
        )

        if j_prim.IsA(UsdPhysics.RevoluteJoint):
            rev = UsdPhysics.RevoluteJoint(j_prim)
            axis_token = str(rev.GetAxisAttr().Get() or "Z").upper()
            axis_vec = _quat_rotate(lr1, _AXIS_VECTORS.get(axis_token, (0.0, 0.0, 1.0)))
            raw_lo = rev.GetLowerLimitAttr().Get()
            raw_hi = rev.GetUpperLimitAttr().Get()
            lo = (
                math.radians(float(raw_lo))
                if raw_lo is not None and math.isfinite(float(raw_lo))
                else -math.inf
            )
            hi = (
                math.radians(float(raw_hi))
                if raw_hi is not None and math.isfinite(float(raw_hi))
                else math.inf
            )
            limited = math.isfinite(lo) and math.isfinite(hi) and lo <= hi

            drive_type = "angular"
            pos_scale = math.pi / 180.0
            gain_scale = 180.0 / math.pi
            kind: Literal["hinge", "slide"] = "hinge"
        elif j_prim.IsA(UsdPhysics.PrismaticJoint):
            pris = UsdPhysics.PrismaticJoint(j_prim)
            axis_token = str(pris.GetAxisAttr().Get() or "Z").upper()
            axis_vec = _quat_rotate(lr1, _AXIS_VECTORS.get(axis_token, (0.0, 0.0, 1.0)))
            raw_lo = pris.GetLowerLimitAttr().Get()
            raw_hi = pris.GetUpperLimitAttr().Get()
            lo = (
                float(raw_lo) * meters_per_unit
                if raw_lo is not None and math.isfinite(float(raw_lo))
                else -math.inf
            )
            hi = (
                float(raw_hi) * meters_per_unit
                if raw_hi is not None and math.isfinite(float(raw_hi))
                else math.inf
            )
            limited = math.isfinite(lo) and math.isfinite(hi) and lo <= hi

            drive_type = "linear"
            pos_scale = meters_per_unit
            gain_scale = 1.0 / meters_per_unit if meters_per_unit > 0 else 1.0
            kind = "slide"
        else:
            raise ValueError(f"Unsupported USD joint type {j_prim.GetTypeName()} at {j_prim.GetPath()}")

        stiffness = 0.0
        damping = 0.0
        max_force: float | None = None
        default_pos = 0.0
        has_drive = j_prim.HasAPI(UsdPhysics.DriveAPI, drive_type)
        if has_drive:
            drive = UsdPhysics.DriveAPI(j_prim, drive_type)
            raw_kp = drive.GetStiffnessAttr().Get()
            raw_kd = drive.GetDampingAttr().Get()
            raw_mf = drive.GetMaxForceAttr().Get()
            raw_tp = drive.GetTargetPositionAttr().Get()
            if raw_kp is not None and math.isfinite(float(raw_kp)):
                stiffness = float(raw_kp) * gain_scale
            if raw_kd is not None and math.isfinite(float(raw_kd)):
                damping = float(raw_kd) * gain_scale
            if raw_mf is not None and math.isfinite(float(raw_mf)) and float(raw_mf) > 0.0:
                max_force = float(raw_mf)
            if raw_tp is not None and math.isfinite(float(raw_tp)):
                default_pos = float(raw_tp) * pos_scale

        state_pos = _custom_float(
            j_prim,
            (
                f"state:{drive_type}:physics:position",
                f"newton:{drive_type}:position",
            ),
            default=math.nan,
        )
        if math.isfinite(state_pos):
            default_pos = state_pos * pos_scale

        armature = _custom_float(
            j_prim,
            ("mjc:armature", "newton:armature", "physxJoint:armature"),
            default=0.0,
        )
        frictionloss = _custom_float(
            j_prim,
            ("mjc:frictionloss", "newton:friction", "physics:jointFriction"),
            default=0.0,
        )
        actuated = has_drive and (stiffness > 0.0 or damping > 0.0)
        j_name = _unique_name(j_prim.GetName(), used_joint_names)
        return DerivedJoint(
            name=j_name,
            prim_path=str(j_prim.GetPath()),
            kind=kind,
            axis=axis_vec,
            pos=anchor_in_child,
            limited=limited,
            lower=lo,
            upper=hi,
            default_position=default_pos,
            stiffness=stiffness,
            damping=damping,
            max_force=max_force,
            armature=armature,
            frictionloss=frictionloss,
            actuated=actuated,
        )

    def _add_body_geoms_and_sites(body_prim: Usd.Prim, body_elem: ET.Element) -> int:
        b_world = xform_cache.GetLocalToWorldTransform(body_prim)
        b_world_inv = b_world.GetInverse()
        collision_geom_count = 0

        stack = list(reversed(body_prim.GetFilteredChildren(Usd.TraverseInstanceProxies())))
        while stack:
            prim = stack.pop()
            if not prim.IsActive() or _is_rigid_body(prim) or prim.IsA(UsdPhysics.Joint):
                continue

            is_geom = prim.IsA(UsdGeom.Gprim)
            if is_geom:
                imageable = UsdGeom.Imageable(prim)
                purpose = str(imageable.ComputePurpose() or "default")
                has_col = prim.HasAPI(UsdPhysics.CollisionAPI) and (
                    UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not False
                )
                if purpose == "guide" and not has_col:
                    continue
                rel_mat = xform_cache.GetLocalToWorldTransform(prim) * b_world_inv
                g_pos, g_quat, g_scale = _decompose_matrix(rel_mat, meters_per_unit)
                g_name = _unique_name(f"{body_prim.GetName()}_{prim.GetName()}", used_geom_names)
                common_attrs = {
                    "name": g_name,
                    "pos": _fmt(g_pos),
                    "contype": "1" if has_col else "0",
                    "conaffinity": "1" if has_col else "0",
                    "group": "3" if has_col else "1",
                }
                if not has_col:
                    gprim = UsdGeom.Gprim(prim)
                    colors = gprim.GetDisplayColorAttr().Get()
                    opacities = gprim.GetDisplayOpacityAttr().Get()
                    if colors and len(colors) > 0:
                        c = colors[0]
                        alpha = float(opacities[0]) if opacities and len(opacities) > 0 else 1.0
                        common_attrs["rgba"] = _fmt((float(c[0]), float(c[1]), float(c[2]), alpha))

                emitted = False
                if prim.IsA(UsdGeom.Cube):
                    edge = float(UsdGeom.Cube(prim).GetSizeAttr().Get() or 2.0) * meters_per_unit
                    half = (0.5 * edge * g_scale[0], 0.5 * edge * g_scale[1], 0.5 * edge * g_scale[2])
                    ET.SubElement(
                        body_elem,
                        "geom",
                        {**common_attrs, "type": "box", "quat": _fmt(g_quat), "size": _fmt(half)},
                    )
                    emitted = True
                elif prim.IsA(UsdGeom.Sphere):
                    radius = (
                        float(UsdGeom.Sphere(prim).GetRadiusAttr().Get() or 1.0)
                        * max(g_scale)
                        * meters_per_unit
                    )
                    ET.SubElement(
                        body_elem,
                        "geom",
                        {**common_attrs, "type": "sphere", "quat": _fmt(g_quat), "size": _fmt_scalar(radius)},
                    )
                    emitted = True
                elif prim.IsA(UsdGeom.Capsule):
                    cap = UsdGeom.Capsule(prim)
                    axis_tok = str(cap.GetAxisAttr().Get() or "Z").upper()
                    radial_scale = g_scale[1] if axis_tok == "X" else g_scale[0]
                    axis_scales = {"X": g_scale[0], "Y": g_scale[1], "Z": g_scale[2]}
                    axial_scale = axis_scales.get(axis_tok, g_scale[2])
                    radius = float(cap.GetRadiusAttr().Get() or 0.5) * radial_scale * meters_per_unit
                    half_h = 0.5 * float(cap.GetHeightAttr().Get() or 1.0) * axial_scale * meters_per_unit
                    oriented = _quat_mul(g_quat, _Z_TO_AXIS_QUAT.get(axis_tok, (1.0, 0.0, 0.0, 0.0)))
                    ET.SubElement(
                        body_elem,
                        "geom",
                        {
                            **common_attrs,
                            "type": "capsule",
                            "quat": _fmt(oriented),
                            "size": _fmt((radius, half_h)),
                        },
                    )
                    emitted = True
                elif prim.IsA(UsdGeom.Cylinder):
                    cyl = UsdGeom.Cylinder(prim)
                    axis_tok = str(cyl.GetAxisAttr().Get() or "Z").upper()
                    radial_scale = g_scale[1] if axis_tok == "X" else g_scale[0]
                    axis_scales = {"X": g_scale[0], "Y": g_scale[1], "Z": g_scale[2]}
                    axial_scale = axis_scales.get(axis_tok, g_scale[2])
                    radius = float(cyl.GetRadiusAttr().Get() or 1.0) * radial_scale * meters_per_unit
                    half_h = max(
                        1e-5,
                        0.5 * float(cyl.GetHeightAttr().Get() or 2.0) * axial_scale * meters_per_unit,
                    )
                    oriented = _quat_mul(g_quat, _Z_TO_AXIS_QUAT.get(axis_tok, (1.0, 0.0, 0.0, 0.0)))
                    ET.SubElement(
                        body_elem,
                        "geom",
                        {
                            **common_attrs,
                            "type": "cylinder",
                            "quat": _fmt(oriented),
                            "size": _fmt((radius, half_h)),
                        },
                    )
                    emitted = True
                elif prim.IsA(UsdGeom.Mesh):
                    mesh = UsdGeom.Mesh(prim)
                    pts = mesh.GetPointsAttr().Get() or []
                    counts = mesh.GetFaceVertexCountsAttr().Get() or []
                    indices = mesh.GetFaceVertexIndicesAttr().Get() or []
                    verts: list[Vec3] = [
                        (
                            float(p[0]) * g_scale[0] * meters_per_unit,
                            float(p[1]) * g_scale[1] * meters_per_unit,
                            float(p[2]) * g_scale[2] * meters_per_unit,
                        )
                        for p in pts
                    ]
                    tris: list[tuple[int, int, int]] = []
                    cursor = 0
                    for count in counts:
                        c = int(count)
                        if c >= 3 and cursor + c <= len(indices):
                            i0 = int(indices[cursor])
                            for k in range(1, c - 1):
                                tris.append((i0, int(indices[cursor + k]), int(indices[cursor + k + 1])))
                        cursor += c
                    if len(verts) == 3 and len(tris) >= 1:
                        mid = (
                            0.5 * (verts[0][0] + verts[1][0]),
                            0.5 * (verts[0][1] + verts[1][1]),
                            0.5 * (verts[0][2] + verts[1][2]),
                        )
                        verts.append(mid)
                        t0 = tris[0]
                        tris = [(t0[0], 3, t0[2]), (3, t0[1], t0[2]), *tris[1:]]
                    if len(verts) >= 4 and tris:
                        volumetric = _mesh_is_volumetric(verts)
                        if has_col and not volumetric:
                            # Coplanar collision submesh cannot form a 3D convex hull in MuJoCo.
                            common_attrs["contype"] = "0"
                            common_attrs["conaffinity"] = "0"
                            has_col = False
                        m_name = _unique_name(f"mesh_{body_prim.GetName()}_{prim.GetName()}", used_mesh_names)
                        mesh_attrs = {
                            "name": m_name,
                            "vertex": "  ".join(_fmt(v) for v in verts),
                            "face": "  ".join(f"{a} {b} {c}" for a, b, c in tris),
                        }
                        if not volumetric or not has_col:
                            mesh_attrs["inertia"] = "shell"
                        ET.SubElement(asset_elem, "mesh", mesh_attrs)
                        ET.SubElement(
                            body_elem,
                            "geom",
                            {**common_attrs, "type": "mesh", "quat": _fmt(g_quat), "mesh": m_name},
                        )
                        emitted = True

                if emitted and has_col:
                    collision_geom_count += 1
            elif prim.IsA(UsdGeom.Xformable):
                children = prim.GetFilteredChildren(Usd.TraverseInstanceProxies())
                p_name_lower = prim.GetName().lower()
                if not children or "site" in p_name_lower or "tcp" in p_name_lower:
                    rel_mat = xform_cache.GetLocalToWorldTransform(prim) * b_world_inv
                    s_pos, s_quat, _ = _decompose_matrix(rel_mat, meters_per_unit)
                    s_name = _unique_name(prim.GetName(), used_site_names)
                    ET.SubElement(
                        body_elem,
                        "site",
                        {"name": s_name, "pos": _fmt(s_pos), "quat": _fmt(s_quat), "size": "0.005"},
                    )
                    derived_sites.append(s_name)

            for child in reversed(prim.GetFilteredChildren(Usd.TraverseInstanceProxies())):
                stack.append(child)

        return collision_geom_count

    def _emit_body(
        body_path: Sdf.Path,
        parent_elem: ET.Element,
        rel_pos: Vec3,
        rel_quat: QuatWXYZ,
        joint_prim: Usd.Prim | None,
        *,
        is_root: bool,
    ) -> None:
        b_prim = body_by_path[body_path]
        b_name = _unique_name(b_prim.GetName(), used_body_names)
        body_elem = ET.SubElement(
            parent_elem,
            "body",
            {"name": b_name, "pos": _fmt(rel_pos), "quat": _fmt(rel_quat)},
        )

        _, _, child_scale = _decompose_matrix(
            xform_cache.GetLocalToWorldTransform(b_prim), meters_per_unit
        )

        # Inertial properties
        mass_val: float | None = None
        com_val: Vec3 = (0.0, 0.0, 0.0)
        diag_val: Vec3 | None = None
        axes_quat: QuatWXYZ = (1.0, 0.0, 0.0, 0.0)
        if b_prim.HasAPI(UsdPhysics.MassAPI):
            m_api = UsdPhysics.MassAPI(b_prim)
            raw_m = m_api.GetMassAttr().Get()
            if raw_m is not None and math.isfinite(float(raw_m)) and float(raw_m) > 0.0:
                mass_val = float(raw_m) * kg_per_unit
            raw_com = m_api.GetCenterOfMassAttr().Get()
            if raw_com is not None and all(math.isfinite(float(c)) for c in raw_com):
                com_val = (
                    float(raw_com[0]) * child_scale[0] * meters_per_unit,
                    float(raw_com[1]) * child_scale[1] * meters_per_unit,
                    float(raw_com[2]) * child_scale[2] * meters_per_unit,
                )
            raw_diag = m_api.GetDiagonalInertiaAttr().Get()
            if raw_diag is not None and all(math.isfinite(float(d)) and float(d) > 0.0 for d in raw_diag):
                inertia_scale = kg_per_unit * (meters_per_unit**2)
                diag_val = (
                    float(raw_diag[0]) * inertia_scale,
                    float(raw_diag[1]) * inertia_scale,
                    float(raw_diag[2]) * inertia_scale,
                )
            raw_axes = m_api.GetPrincipalAxesAttr().Get()
            if raw_axes is not None:
                axes_quat = _gf_quat_to_wxyz(raw_axes)

        if is_root and not is_fixed_base and (joint_prim is None or not joint_prim.IsValid()):
            ET.SubElement(body_elem, "freejoint", {"name": "floating_base_joint"})
        elif joint_prim is not None and joint_prim.IsValid():
            j_spec = _extract_joint_spec(joint_prim, child_scale)
            if j_spec is not None:
                derived_joints.append(j_spec)
                j_attrs = {
                    "name": j_spec.name,
                    "type": j_spec.kind,
                    "pos": _fmt(j_spec.pos),
                    "axis": _fmt(j_spec.axis),
                    "armature": _fmt_scalar(j_spec.armature),
                    "frictionloss": _fmt_scalar(j_spec.frictionloss),
                }
                if j_spec.limited:
                    j_attrs["limited"] = "true"
                    j_attrs["range"] = _fmt((j_spec.lower, j_spec.upper))
                else:
                    j_attrs["limited"] = "false"
                if j_spec.max_force is not None:
                    j_attrs["actuatorfrclimited"] = "true"
                    j_attrs["actuatorfrcrange"] = _fmt((-j_spec.max_force, j_spec.max_force))
                if not j_spec.actuated and j_spec.damping > 0.0:
                    j_attrs["damping"] = _fmt_scalar(j_spec.damping)
                ET.SubElement(body_elem, "joint", j_attrs)

                if j_spec.actuated:
                    act_attrs = {
                        "name": j_spec.name,
                        "joint": j_spec.name,
                        "kp": _fmt_scalar(j_spec.stiffness),
                        "kv": _fmt_scalar(j_spec.damping),
                        "ctrllimited": "false",
                        "inheritrange": "0",
                    }
                    if j_spec.max_force is not None:
                        act_attrs["forcelimited"] = "true"
                        act_attrs["forcerange"] = _fmt((-j_spec.max_force, j_spec.max_force))
                    else:
                        act_attrs["forcelimited"] = "false"
                    ET.SubElement(actuator_elem, "position", act_attrs)

        col_count = _add_body_geoms_and_sites(b_prim, body_elem)
        if mass_val is not None and diag_val is not None:
            inertial_elem = ET.Element(
                "inertial",
                {
                    "pos": _fmt(com_val),
                    "quat": _fmt(axes_quat),
                    "mass": _fmt_scalar(mass_val),
                    "diaginertia": _fmt(diag_val),
                },
            )
            body_elem.insert(0, inertial_elem)
        elif mass_val is not None and col_count == 0:
            iso = max(1e-8, 1e-4 * mass_val)
            inertial_elem = ET.Element(
                "inertial",
                {
                    "pos": _fmt(com_val),
                    "mass": _fmt_scalar(mass_val),
                    "diaginertia": _fmt((iso, iso, iso)),
                },
            )
            body_elem.insert(0, inertial_elem)
        elif col_count == 0 and not (is_root and is_fixed_base and not children_by_parent[body_path]):
            body_elem.insert(
                0,
                ET.Element("inertial", {"pos": "0 0 0", "mass": "1e-5", "diaginertia": "1e-8 1e-8 1e-8"}),
            )

        # Recurse into child rigid bodies in deterministic stage order
        _, _, parent_scale = _decompose_matrix(
            xform_cache.GetLocalToWorldTransform(b_prim), meters_per_unit
        )
        for child_path, c_joint_prim in children_by_parent[body_path]:
            c_prim = body_by_path[child_path]
            _, _, c_scale = _decompose_matrix(
                xform_cache.GetLocalToWorldTransform(c_prim), meters_per_unit
            )
            if c_joint_prim and c_joint_prim.IsValid():
                cj = UsdPhysics.Joint(c_joint_prim)
                lp0 = cj.GetLocalPos0Attr().Get()
                lr0 = _gf_quat_to_wxyz(cj.GetLocalRot0Attr().Get())
                p0 = (
                    float(lp0[0]) * parent_scale[0] * meters_per_unit if lp0 is not None else 0.0,
                    float(lp0[1]) * parent_scale[1] * meters_per_unit if lp0 is not None else 0.0,
                    float(lp0[2]) * parent_scale[2] * meters_per_unit if lp0 is not None else 0.0,
                )
                lp1 = cj.GetLocalPos1Attr().Get()
                lr1 = _gf_quat_to_wxyz(cj.GetLocalRot1Attr().Get())
                p1 = (
                    float(lp1[0]) * c_scale[0] * meters_per_unit if lp1 is not None else 0.0,
                    float(lp1[1]) * c_scale[1] * meters_per_unit if lp1 is not None else 0.0,
                    float(lp1[2]) * c_scale[2] * meters_per_unit if lp1 is not None else 0.0,
                )
                inv_p1, inv_q1 = _pose_inv(p1, lr1)
                c_rel_pos, c_rel_quat = _pose_mul(p0, lr0, inv_p1, inv_q1)
            else:
                rel_mat = (
                    xform_cache.GetLocalToWorldTransform(c_prim)
                    * xform_cache.GetLocalToWorldTransform(b_prim).GetInverse()
                )
                c_rel_pos, c_rel_quat, _ = _decompose_matrix(rel_mat, meters_per_unit)

            _emit_body(child_path, body_elem, c_rel_pos, c_rel_quat, c_joint_prim, is_root=False)

    root_joint_arg = (
        world_joint_prim
        if (
            world_joint_prim
            and world_joint_prim.IsValid()
            and not world_joint_prim.IsA(UsdPhysics.FixedJoint)
        )
        else None
    )
    _emit_body(root_path, worldbody_elem, root_pos, root_quat, root_joint_arg, is_root=True)

    ET.indent(mj_root, space="  ")
    mjcf_xml = ET.tostring(mj_root, encoding="unicode")
    root_body_name = worldbody_elem.find("body").attrib["name"]  # type: ignore[union-attr]
    return DerivedArticulation(
        model_name=mj_root.attrib["model"],
        root_body=root_body_name,
        fixed_base=is_fixed_base,
        root_pos=root_pos,
        root_quat=root_quat,
        joints=tuple(derived_joints),
        sites=tuple(derived_sites),
        mjcf_xml=mjcf_xml,
    )


def _load_obs_spec_mapping(obs_spec: Any) -> Mapping[str, Any]:
    if hasattr(obs_spec, "model_dump"):
        return obs_spec.model_dump(by_alias=True)
    if isinstance(obs_spec, Mapping):
        return obs_spec
    path = Path(obs_spec)
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    import yaml

    return yaml.safe_load(text)


def _expected_joint_keys(
    derived: DerivedArticulation,
    action_keys: Sequence[str],
    entity_key: str | None,
) -> list[str]:
    if entity_key is not None:
        prefix = entity_key.rstrip("/")
        prim_paths = list(derived.joint_prim_paths)
        if prim_paths:
            # Replace the authored root prim segment (e.g. "/Asset") with entity_key.
            first_parts = PurePosixPath(prim_paths[0]).parts
            asset_root = "/" + first_parts[1] if len(first_parts) >= 2 else ""
            if asset_root and all(
                p == asset_root or p.startswith(asset_root + "/") for p in prim_paths
            ):
                return [prefix + p[len(asset_root) :] for p in prim_paths]
        return [f"{prefix}/{j.name}" for j in derived.joints]
    if action_keys and all(str(k).startswith("/") for k in action_keys):
        return list(derived.joint_prim_paths)
    return list(derived.joint_names)


def verify_against_observation_spec(
    derived: DerivedArticulation,
    obs_spec: Any,
    *,
    entity_key: str | None = None,
    expected_kp: Mapping[str, float] | Sequence[float] | None = None,
    expected_kd: Mapping[str, float] | Sequence[float] | None = None,
    check_observation_offsets: bool = False,
    atol: float = 1e-5,
) -> None:
    """Assert exact agreement between derived USD articulation joints/drives and an ObservationSpec."""
    doc = _load_obs_spec_mapping(obs_spec)
    schema = doc.get("schema")
    if schema is not None and schema != "lw.obs-spec/1":
        raise ValueError(f"Unsupported ObservationSpec schema {schema!r}; expected 'lw.obs-spec/1'")

    action = doc.get("action") or {}
    action_keys = [str(k) for k in (action.get("jointKeys") or ())]
    expected_keys = _expected_joint_keys(derived, action_keys, entity_key)
    expected_defaults = [joint.default_position for joint in derived.joints]
    expected_lower = [joint.lower for joint in derived.joints]
    expected_upper = [joint.upper for joint in derived.joints]
    expected_units = ["rad" if joint.kind == "hinge" else "m" for joint in derived.joints]

    if action_keys != expected_keys:
        raise ValueError(
            f"action.jointKeys mismatch: spec has {action_keys!r}, derived USD has {expected_keys!r}"
        )

    if action.get("units") is not None:
        spec_units = [str(u) for u in action["units"]]
        if spec_units != expected_units:
            raise ValueError(
                f"action.units mismatch: spec has {spec_units!r}, derived USD has {expected_units!r}"
            )

    if action.get("defaultPosition") is not None:
        act_defaults = [float(v) for v in action["defaultPosition"]]
        if len(act_defaults) != len(expected_defaults) or any(
            abs(a - b) > atol for a, b in zip(act_defaults, expected_defaults, strict=True)
        ):
            raise ValueError(
                f"action.defaultPosition mismatch: spec has {act_defaults}, "
                f"derived USD has {expected_defaults}"
            )

    for bound_field, ref_bounds in (("lower", expected_lower), ("upper", expected_upper)):
        if action.get(bound_field) is not None:
            spec_bounds = [float(v) for v in action[bound_field]]
            if len(spec_bounds) != len(ref_bounds) or any(
                abs(a - b) > atol for a, b in zip(spec_bounds, ref_bounds, strict=True)
            ):
                raise ValueError(
                    f"action.{bound_field} mismatch: spec has {spec_bounds}, derived USD has {ref_bounds}"
                )

    index_by_key = {key: idx for idx, key in enumerate(expected_keys)}
    for item in doc.get("observations") or ():
        source = str(item.get("source") or "")
        if not source.startswith("joint_"):
            continue
        obs_keys = [str(k) for k in (item.get("keys") or ())]
        unknown = [k for k in obs_keys if k not in index_by_key]
        if unknown:
            raise ValueError(
                f"observation {item.get('name')!r} keys {unknown!r} not in "
                f"derived USD joints {expected_keys!r}"
            )
        indices = [index_by_key[k] for k in obs_keys]
        if indices != sorted(indices) or len(set(indices)) != len(indices):
            raise ValueError(
                f"observation {item.get('name')!r} keys {obs_keys!r} are out of "
                f"derived USD joint order {expected_keys!r}"
            )
        obs_unit = item.get("unit")
        if obs_unit is not None:
            for idx in indices:
                j = derived.joints[idx]
                want_unit = (
                    ("rad" if j.kind == "hinge" else "m")
                    if source == "joint_position"
                    else ("rad/s" if j.kind == "hinge" else "m/s")
                )
                if obs_unit != want_unit:
                    raise ValueError(
                        f"observation {item.get('name')!r} unit {obs_unit!r} "
                        f"mismatches joint {j.name!r} ({want_unit!r})"
                    )
        if check_observation_offsets and source == "joint_position" and item.get("offset") is not None:
            obs_offsets = [float(v) for v in item["offset"]]
            ref_offsets = [expected_defaults[idx] for idx in indices]
            if len(obs_offsets) != len(ref_offsets) or any(
                abs(a - b) > atol for a, b in zip(obs_offsets, ref_offsets, strict=True)
            ):
                raise ValueError(
                    f"observation {item.get('name')!r} offset mismatch: {obs_offsets} != {ref_offsets}"
                )

    for joint in derived.joints:
        if joint.limited and not (joint.lower - atol <= joint.default_position <= joint.upper + atol):
            raise ValueError(
                f"Joint {joint.name!r} default_position={joint.default_position} outside "
                f"[{joint.lower}, {joint.upper}]"
            )

    def _check_gains(label: str, provided: Mapping[str, float] | Sequence[float] | None, attr: str) -> None:
        if provided is None:
            return
        if isinstance(provided, Mapping):
            target = [
                float(provided[key] if key in provided else provided[j.name])
                for key, j in zip(expected_keys, derived.joints, strict=True)
            ]
        else:
            target = [float(v) for v in provided]
        actual = [float(getattr(j, attr)) for j in derived.joints]
        if len(target) != len(actual) or any(
            abs(a - b) > atol for a, b in zip(target, actual, strict=True)
        ):
            raise ValueError(f"Drive {label} mismatch: expected {target}, derived USD has {actual}")

    _check_gains("kp", expected_kp, "stiffness")
    _check_gains("kd", expected_kd, "damping")

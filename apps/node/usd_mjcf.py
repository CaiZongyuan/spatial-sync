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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pxr import Ar, Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdUtils

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


Mat3 = tuple[Vec3, Vec3, Vec3]


@dataclass(frozen=True)
class _ShapeMass:
    volume: float
    centroid: Vec3
    unit_inertia: Mat3


def _rotate_mat3(q: QuatWXYZ, mat: Mat3) -> Mat3:
    # Columns of R(q) map local axes to parent/body frame: I_body = R @ mat @ R^T
    c0 = _quat_rotate(q, (1.0, 0.0, 0.0))
    c1 = _quat_rotate(q, (0.0, 1.0, 0.0))
    c2 = _quat_rotate(q, (0.0, 0.0, 1.0))
    rows_r = (
        (c0[0], c1[0], c2[0]),
        (c0[1], c1[1], c2[1]),
        (c0[2], c1[2], c2[2]),
    )
    rm = tuple(
        tuple(sum(rows_r[i][k] * mat[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )
    return (
        tuple(sum(rm[0][k] * rows_r[j][k] for k in range(3)) for j in range(3)),  # type: ignore[return-value]
        tuple(sum(rm[1][k] * rows_r[j][k] for k in range(3)) for j in range(3)),  # type: ignore[return-value]
        tuple(sum(rm[2][k] * rows_r[j][k] for k in range(3)) for j in range(3)),  # type: ignore[return-value]
    )


def _rot3_to_quat_wxyz(r: list[list[float]]) -> QuatWXYZ:
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (r[2][1] - r[1][2]) / s
        y = (r[0][2] - r[2][0]) / s
        z = (r[1][0] - r[0][1]) / s
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2.0
        w = (r[2][1] - r[1][2]) / s
        x = 0.25 * s
        y = (r[0][1] + r[1][0]) / s
        z = (r[0][2] + r[2][0]) / s
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2.0
        w = (r[0][2] - r[2][0]) / s
        x = (r[0][1] + r[1][0]) / s
        y = 0.25 * s
        z = (r[1][2] + r[2][1]) / s
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2.0
        w = (r[1][0] - r[0][1]) / s
        x = (r[0][2] + r[2][0]) / s
        y = (r[1][2] + r[2][1]) / s
        z = 0.25 * s
    return _quat_normalize((w, x, y, z))


def _diagonalize_inertia_3x3(mat: list[list[float]]) -> tuple[Vec3, QuatWXYZ]:
    """Cyclic Jacobi eigendecomposition of a 3x3 symmetric inertia matrix (pure stdlib)."""
    a = [[float(mat[i][j]) for j in range(3)] for i in range(3)]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(32):
        p, q = 0, 1
        max_off = abs(a[0][1])
        for cand_p, cand_q in ((0, 2), (1, 2)):
            if abs(a[cand_p][cand_q]) > max_off:
                max_off = abs(a[cand_p][cand_q])
                p, q = cand_p, cand_q
        if max_off < 1e-15:
            break
        diff = a[q][q] - a[p][p]
        if abs(a[p][q]) < abs(diff) * 1e-18:
            t = a[p][q] / diff
        else:
            phi = 0.5 * diff / a[p][q]
            t = 1.0 / (abs(phi) + math.sqrt(1.0 + phi * phi))
            if phi < 0.0:
                t = -t
        c = 1.0 / math.sqrt(1.0 + t * t)
        s = t * c
        tau = s / (1.0 + c)
        apq = a[p][q]
        a[p][q] = a[q][p] = 0.0
        a[p][p] -= t * apq
        a[q][q] += t * apq
        r_idx = 3 - p - q
        arp, arq = a[r_idx][p], a[r_idx][q]
        a[r_idx][p] = a[p][r_idx] = arp - s * (arq + tau * arp)
        a[r_idx][q] = a[q][r_idx] = arq + s * (arp - tau * arq)
        for i in range(3):
            vip, viq = v[i][p], v[i][q]
            v[i][p] = vip - s * (viq + tau * vip)
            v[i][q] = viq + s * (vip - tau * viq)

    # Ensure right-handed eigenvector basis (det(V) == +1)
    det_v = (
        v[0][0] * (v[1][1] * v[2][2] - v[1][2] * v[2][1])
        - v[0][1] * (v[1][0] * v[2][2] - v[1][2] * v[2][0])
        + v[0][2] * (v[1][0] * v[2][1] - v[1][1] * v[2][0])
    )
    if det_v < 0.0:
        for i in range(3):
            v[i][2] = -v[i][2]
    diag = (max(1e-9, a[0][0]), max(1e-9, a[1][1]), max(1e-9, a[2][2]))
    return (diag, _rot3_to_quat_wxyz(v))


def _mesh_shape_mass(
    verts: list[Vec3], tris: list[tuple[int, int, int]], pos: Vec3, quat: QuatWXYZ
) -> _ShapeMass | None:
    vol6 = 0.0
    cx = cy = cz = 0.0
    for i0, i1, i2 in tris:
        a, b, c = verts[i0], verts[i1], verts[i2]
        det = (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
        vol6 += det
        cx += det * (a[0] + b[0] + c[0])
        cy += det * (a[1] + b[1] + c[1])
        cz += det * (a[2] + b[2] + c[2])

    if abs(vol6) > 1e-12:
        sign = 1.0 if vol6 > 0.0 else -1.0
        vol = abs(vol6) / 6.0
        inv_c = 1.0 / (4.0 * vol6)
        cm_local = (cx * inv_c, cy * inv_c, cz * inv_c)
        cov = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
        for i0, i1, i2 in tris:
            pa = (verts[i0][0] - cm_local[0], verts[i0][1] - cm_local[1], verts[i0][2] - cm_local[2])
            pb = (verts[i1][0] - cm_local[0], verts[i1][1] - cm_local[1], verts[i1][2] - cm_local[2])
            pc = (verts[i2][0] - cm_local[0], verts[i2][1] - cm_local[1], verts[i2][2] - cm_local[2])
            det = sign * (
                pa[0] * (pb[1] * pc[2] - pb[2] * pc[1])
                - pa[1] * (pb[0] * pc[2] - pb[2] * pc[0])
                + pa[2] * (pb[0] * pc[1] - pb[1] * pc[0])
            )
            pts = (pa, pb, pc)
            for u in range(3):
                for w in range(3):
                    s_uw = sum(pts[m][u] * pts[n][w] for m in range(3) for n in range(3) if m == n)
                    s_uw += sum(pts[m][u] * pts[n][w] for m in range(3) for n in range(3))
                    cov[u][w] += det * s_uw / 120.0
        trace = cov[0][0] + cov[1][1] + cov[2][2]
        i_local: Mat3 = (
            (trace - cov[0][0], -cov[0][1], -cov[0][2]),
            (-cov[1][0], trace - cov[1][1], -cov[1][2]),
            (-cov[2][0], -cov[2][1], trace - cov[2][2]),
        )
    else:
        xs = [p[0] for p in verts]
        ys = [p[1] for p in verts]
        zs = [p[2] for p in verts]
        hx = max(1e-5, 0.5 * (max(xs) - min(xs)))
        hy = max(1e-5, 0.5 * (max(ys) - min(ys)))
        hz = max(1e-5, 0.5 * (max(zs) - min(zs)))
        vol = 8.0 * hx * hy * hz
        cm_local = (0.5 * (max(xs) + min(xs)), 0.5 * (max(ys) + min(ys)), 0.5 * (max(zs) + min(zs)))
        i_local = (
            (vol * (hy * hy + hz * hz) / 3.0, 0.0, 0.0),
            (0.0, vol * (hx * hx + hz * hz) / 3.0, 0.0),
            (0.0, 0.0, vol * (hx * hx + hy * hy) / 3.0),
        )

    rot_cm = _quat_rotate(quat, cm_local)
    cm_body = (pos[0] + rot_cm[0], pos[1] + rot_cm[1], pos[2] + rot_cm[2])
    return _ShapeMass(volume=vol, centroid=cm_body, unit_inertia=_rotate_mat3(quat, i_local))


def _combine_shape_masses(shapes: Sequence[_ShapeMass], target_mass: float) -> tuple[Vec3, Vec3, QuatWXYZ]:
    total_vol = sum(s.volume for s in shapes)
    if total_vol <= 1e-15:
        iso = max(1e-8, 1e-4 * target_mass)
        return ((0.0, 0.0, 0.0), (iso, iso, iso), (1.0, 0.0, 0.0, 0.0))
    c_geom = (
        sum(s.volume * s.centroid[0] for s in shapes) / total_vol,
        sum(s.volume * s.centroid[1] for s in shapes) / total_vol,
        sum(s.volume * s.centroid[2] for s in shapes) / total_vol,
    )
    density_scale = target_mass / total_vol
    i_tot = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    for s in shapes:
        m_i = density_scale * s.volume
        dx = s.centroid[0] - c_geom[0]
        dy = s.centroid[1] - c_geom[1]
        dz = s.centroid[2] - c_geom[2]
        d2 = dx * dx + dy * dy + dz * dz
        dv = (dx, dy, dz)
        for r in range(3):
            for col in range(3):
                shift = m_i * ((d2 if r == col else 0.0) - dv[r] * dv[col])
                i_tot[r][col] += density_scale * s.unit_inertia[r][col] + shift
    diag, quat = _diagonalize_inertia_3x3(i_tot)
    return (c_geom, diag, quat)


def _decompose_matrix(mat: Gf.Matrix4d, meters_per_unit: float) -> tuple[Vec3, QuatWXYZ, Vec3]:
    pos = mat.ExtractTranslation()
    sx = Gf.Vec3d(mat[0][0], mat[0][1], mat[0][2]).GetLength() or 1.0
    sy = Gf.Vec3d(mat[1][0], mat[1][1], mat[1][2]).GetLength() or 1.0
    sz = Gf.Vec3d(mat[2][0], mat[2][1], mat[2][2]).GetLength() or 1.0
    rot_mat = Gf.Matrix4d(mat)
    if mat.GetDeterminant3() < 0.0:
        sx = -sx
        rot_mat = Gf.Matrix4d().SetScale(Gf.Vec3d(-1.0, 1.0, 1.0)) * rot_mat
    ortho = rot_mat.RemoveScaleShear()
    quat = _gf_quat_to_wxyz(ortho.ExtractRotationQuat())
    return (
        (
            float(pos[0]) * meters_per_unit,
            float(pos[1]) * meters_per_unit,
            float(pos[2]) * meters_per_unit,
        ),
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


def _confined_regular_file(owner_root: Path, rel_posix: str) -> Path:
    if owner_root.is_symlink():
        raise ValueError(f"USD package root symlink is not allowed: {owner_root}")
    root_resolved = owner_root.resolve()
    parts = PurePosixPath(rel_posix).parts
    if not parts or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"Invalid relative USD path: {rel_posix!r}")
    cur = root_resolved
    for part in parts:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError(f"USD dependency symlink is not allowed: {cur}")
    resolved = cur.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise ValueError(f"USD dependency escapes package root: {rel_posix!r}")
    if not cur.is_file():
        raise ValueError(f"Missing USD dependency file: {rel_posix!r}")
    return resolved


def _open_usd_stage(
    source: str | Path | Usd.Stage, asset_packages: Mapping[str, str | Path] | None
) -> tuple[Usd.Stage, list[Sdf.Layer]]:
    if isinstance(source, Usd.Stage):
        comp_errors = source.GetCompositionErrors()
        if comp_errors:
            details = "; ".join(
                str(err.GetMessage() if hasattr(err, "GetMessage") else err) for err in comp_errors
            )
            raise ValueError(f"USD stage composition failed: {details}")
        return (source, [])

    raw_path = Path(source)
    if raw_path.is_symlink():
        raise ValueError(f"USD stage symlink is not allowed: {raw_path}")
    resolved_source = raw_path.resolve()
    if not resolved_source.is_file():
        raise ValueError(f"Unable to open USD layer: {raw_path}")

    packages = _resolve_asset_packages(asset_packages)
    for pkg_dir in packages.values():
        if pkg_dir.is_symlink():
            raise ValueError(f"USD asset package symlink is not allowed: {pkg_dir}")

    env_scene_pkg = os.environ.get("LW_SCENE_PACKAGE", "").strip()
    if env_scene_pkg and not Path(env_scene_pkg).is_symlink():
        scene_pkg_resolved = Path(env_scene_pkg).resolve()
        if resolved_source.is_relative_to(scene_pkg_resolved):
            stage_owner = scene_pkg_resolved
        else:
            stage_owner = resolved_source.parent
    else:
        matching_pkg = next(
            (
                pkg.resolve()
                for pkg in packages.values()
                if pkg.exists() and resolved_source.is_relative_to(pkg.resolve())
            ),
            None,
        )
        stage_owner = matching_pkg if matching_pkg is not None else resolved_source.parent

    stage_rel = resolved_source.relative_to(stage_owner).as_posix()
    clones: dict[str, Sdf.Layer] = {}
    rewrite_errors: list[str] = []

    def _resolve_reference(owner_root: Path, current_rel: str, ref: str) -> tuple[Path, str] | None:
        if not ref:
            return None
        if Ar.IsPackageRelativePath(ref):
            raise ValueError(f"Unsupported packaged USD member reference: {ref}")
        if ref.startswith("library:"):
            rem = ref.removeprefix("library:")
            if "\\" in rem or ":" in rem or PureWindowsPath(rem).drive or rem.startswith("/"):
                raise ValueError(f"Invalid USD library URI: {ref}")
            parts = PurePosixPath(rem).parts
            if any(p in ("", ".", "..") for p in parts) or len(parts) < 5 or parts[0] != "assets":
                raise ValueError(f"Unsupported or escaping USD library URI: {ref}")
            kind, name, rev = parts[1], parts[2], parts[3]
            pkg_root = (
                packages.get(f"{kind}/{name}@{rev}")
                or packages.get(f"asset/{kind}/{name}@{rev}")
                or packages.get(f"assets/{kind}/{name}/{rev}")
            )
            if pkg_root is None:
                raise ValueError(f"Unresolved USD library asset reference: {ref}")
            if pkg_root.is_symlink():
                raise ValueError(f"USD asset package symlink is not allowed: {pkg_root}")
            return (pkg_root.resolve(), "/".join(parts[4:]))
        if PureWindowsPath(ref).drive or "\\" in ref or ":" in ref:
            raise ValueError(f"External host paths and URI references are not allowed: {ref}")
        rel_ref = PurePosixPath(ref)
        if rel_ref.is_absolute():
            raise ValueError(f"Absolute USD references are not allowed: {ref}")
        stack = [p for p in PurePosixPath(current_rel).parent.parts if p not in ("", ".")]
        for part in rel_ref.parts:
            if part == "..":
                if not stack:
                    raise ValueError(f"USD dependency escapes its owning package: {ref}")
                stack.pop()
            elif part not in ("", "."):
                stack.append(part)
        if not stack:
            raise ValueError(f"Invalid empty USD reference: {ref}")
        return (owner_root, "/".join(stack))

    def _clone_layer(owner_root: Path, rel_posix: str) -> Sdf.Layer:
        actual = _confined_regular_file(owner_root, rel_posix)
        key = str(actual)
        if key in clones:
            return clones[key]
        if len(clones) >= 4096:
            raise ValueError("USD dependency count exceeds package budget (4096)")
        orig = Sdf.Layer.FindOrOpen(key)
        if orig is None:
            raise ValueError(f"Unable to open USD layer: {actual}")
        clone = Sdf.Layer.CreateAnonymous(".usda")
        clone.TransferContent(orig)
        clones[key] = clone
        comp_refs = set(orig.GetExternalReferences())

        def _confined_rewrite(asset_path: str) -> str:
            try:
                resolved_target = _resolve_reference(owner_root, rel_posix, asset_path)
                if resolved_target is None:
                    return asset_path
                t_owner, t_rel = resolved_target
                suffix = PurePosixPath(t_rel).suffix.lower()
                if suffix == ".usdz" or (
                    asset_path in comp_refs and suffix not in (".usd", ".usda", ".usdc")
                ):
                    raise ValueError(f"Unsupported USD layer format: {t_rel}")
                t_actual = _confined_regular_file(t_owner, t_rel)
                if asset_path in comp_refs:
                    return _clone_layer(t_owner, t_rel).identifier
                return t_actual.as_posix()
            except (ValueError, OSError) as exc:
                rewrite_errors.append(str(exc))
                return ""

        UsdUtils.ModifyAssetPaths(clone, _confined_rewrite)
        return clone

    root_clone = _clone_layer(stage_owner, stage_rel)
    if rewrite_errors:
        raise ValueError("; ".join(dict.fromkeys(rewrite_errors)))
    stage = Usd.Stage.Open(root_clone)
    if stage is None:
        raise ValueError(f"Unable to compose rewritten USD stage: {raw_path}")
    comp_errors = stage.GetCompositionErrors()
    if comp_errors:
        details = "; ".join(
            str(err.GetMessage() if hasattr(err, "GetMessage") else err) for err in comp_errors
        )
        raise ValueError(f"USD stage composition failed: {details}")
    return (stage, list(clones.values()))


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
    stage, _pinned_layers = _open_usd_stage(source, asset_packages)
    up_axis = str(UsdGeom.GetStageUpAxis(stage) or "Z").upper()
    if up_axis != "Z":
        raise ValueError(f"Expected canonical Z-up USD stage, got upAxis={up_axis!r}")

    raw_mpu = float(UsdGeom.GetStageMetersPerUnit(stage))
    if not math.isclose(raw_mpu, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(f"Expected canonical metersPerUnit=1.0 USD stage, got metersPerUnit={raw_mpu!r}")
    meters_per_unit = 1.0
    raw_kpu = float(UsdPhysics.GetStageKilogramsPerUnit(stage) or 1.0)
    if not math.isclose(raw_kpu, 1.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(
            f"Expected canonical kilogramsPerUnit=1.0 USD stage, got kilogramsPerUnit={raw_kpu!r}"
        )
    kg_per_unit = 1.0
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
    if len(root_candidates) > 1:
        roots_str = ", ".join(str(p) for p in root_candidates)
        raise ValueError(
            f"Multiple disconnected articulation roots under {scope_path}: {roots_str}; "
            "pass prim_path to select a single articulation"
        )
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
            raw_drive_type = drive.GetTypeAttr().Get()
            if raw_drive_type is not None and str(raw_drive_type) != "force":
                raise ValueError(
                    f"Unsupported UsdPhysics.DriveAPI type {str(raw_drive_type)!r} on {j_prim.GetPath()}; "
                    "only 'force' drives are supported"
                )
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

    def _add_body_geoms_and_sites(
        body_prim: Usd.Prim, body_elem: ET.Element
    ) -> tuple[int, list[_ShapeMass]]:
        b_world = xform_cache.GetLocalToWorldTransform(body_prim)
        b_world_inv = b_world.GetInverse()
        collision_geom_count = 0
        col_shapes: list[_ShapeMass] = []

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
                rel_mat = xform_cache.GetLocalToWorldTransform(prim) * b_world_inv
                g_pos, g_quat, g_scale = _decompose_matrix(rel_mat, meters_per_unit)
                if purpose == "guide" and not has_col:
                    s_name = _unique_name(prim.GetName(), used_site_names)
                    ET.SubElement(
                        body_elem,
                        "site",
                        {"name": s_name, "pos": _fmt(g_pos), "quat": _fmt(g_quat), "size": "0.005"},
                    )
                    derived_sites.append(s_name)
                    for child in reversed(prim.GetFilteredChildren(Usd.TraverseInstanceProxies())):
                        stack.append(child)
                    continue

                abs_scale = (abs(g_scale[0]), abs(g_scale[1]), abs(g_scale[2]))
                g_name = _unique_name(f"{body_prim.GetName()}_{prim.GetName()}", used_geom_names)
                common_attrs = {
                    "name": g_name,
                    "pos": _fmt(g_pos),
                    "contype": "1" if has_col else "0",
                    "conaffinity": "1" if has_col else "0",
                    "group": "3" if has_col else "1",
                }
                if not has_col:
                    common_attrs["density"] = "0"
                    gprim = UsdGeom.Gprim(prim)
                    colors = gprim.GetDisplayColorAttr().Get()
                    opacities = gprim.GetDisplayOpacityAttr().Get()
                    if colors and len(colors) > 0:
                        c = colors[0]
                        alpha = float(opacities[0]) if opacities and len(opacities) > 0 else 1.0
                        common_attrs["rgba"] = _fmt((float(c[0]), float(c[1]), float(c[2]), alpha))

                emitted = False
                shape_mass: _ShapeMass | None = None
                if prim.IsA(UsdGeom.Cube):
                    edge = float(UsdGeom.Cube(prim).GetSizeAttr().Get() or 2.0) * meters_per_unit
                    half = (0.5 * edge * abs_scale[0], 0.5 * edge * abs_scale[1], 0.5 * edge * abs_scale[2])
                    ET.SubElement(
                        body_elem,
                        "geom",
                        {**common_attrs, "type": "box", "quat": _fmt(g_quat), "size": _fmt(half)},
                    )
                    emitted = True
                    if has_col:
                        hx, hy, hz = half
                        vol = 8.0 * hx * hy * hz
                        ix = vol * (hy * hy + hz * hz) / 3.0
                        iy = vol * (hx * hx + hz * hz) / 3.0
                        iz = vol * (hx * hx + hy * hy) / 3.0
                        shape_mass = _ShapeMass(
                            volume=vol,
                            centroid=g_pos,
                            unit_inertia=_rotate_mat3(
                                g_quat, ((ix, 0.0, 0.0), (0.0, iy, 0.0), (0.0, 0.0, iz))
                            ),
                        )
                elif prim.IsA(UsdGeom.Sphere):
                    radius = (
                        float(UsdGeom.Sphere(prim).GetRadiusAttr().Get() or 1.0)
                        * max(abs_scale)
                        * meters_per_unit
                    )
                    ET.SubElement(
                        body_elem,
                        "geom",
                        {**common_attrs, "type": "sphere", "quat": _fmt(g_quat), "size": _fmt_scalar(radius)},
                    )
                    emitted = True
                    if has_col:
                        vol = (4.0 / 3.0) * math.pi * (radius**3)
                        isph = vol * 0.4 * radius * radius
                        shape_mass = _ShapeMass(
                            volume=vol,
                            centroid=g_pos,
                            unit_inertia=((isph, 0.0, 0.0), (0.0, isph, 0.0), (0.0, 0.0, isph)),
                        )
                elif prim.IsA(UsdGeom.Capsule):
                    cap = UsdGeom.Capsule(prim)
                    axis_tok = str(cap.GetAxisAttr().Get() or "Z").upper()
                    radial_scale = abs_scale[1] if axis_tok == "X" else abs_scale[0]
                    axis_scales = {"X": abs_scale[0], "Y": abs_scale[1], "Z": abs_scale[2]}
                    axial_scale = axis_scales.get(axis_tok, abs_scale[2])
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
                    if has_col:
                        h_full = 2.0 * half_h
                        v_cyl = math.pi * radius * radius * h_full
                        v_sph = (4.0 / 3.0) * math.pi * (radius**3)
                        vol = v_cyl + v_sph
                        ixy = v_cyl * (3.0 * radius * radius + h_full * h_full) / 12.0 + v_sph * (
                            0.4 * radius * radius + half_h * half_h + 0.75 * radius * half_h
                        )
                        iz = v_cyl * 0.5 * radius * radius + v_sph * 0.4 * radius * radius
                        shape_mass = _ShapeMass(
                            volume=vol,
                            centroid=g_pos,
                            unit_inertia=_rotate_mat3(
                                oriented, ((ixy, 0.0, 0.0), (0.0, ixy, 0.0), (0.0, 0.0, iz))
                            ),
                        )
                elif prim.IsA(UsdGeom.Cylinder):
                    cyl = UsdGeom.Cylinder(prim)
                    axis_tok = str(cyl.GetAxisAttr().Get() or "Z").upper()
                    radial_scale = abs_scale[1] if axis_tok == "X" else abs_scale[0]
                    axis_scales = {"X": abs_scale[0], "Y": abs_scale[1], "Z": abs_scale[2]}
                    axial_scale = axis_scales.get(axis_tok, abs_scale[2])
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
                    if has_col:
                        h_full = 2.0 * half_h
                        vol = math.pi * radius * radius * h_full
                        ixy = vol * (3.0 * radius * radius + h_full * h_full) / 12.0
                        iz = vol * 0.5 * radius * radius
                        shape_mass = _ShapeMass(
                            volume=vol,
                            centroid=g_pos,
                            unit_inertia=_rotate_mat3(
                                oriented, ((ixy, 0.0, 0.0), (0.0, ixy, 0.0), (0.0, 0.0, iz))
                            ),
                        )
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
                    reflected = (g_scale[0] * g_scale[1] * g_scale[2]) < 0.0
                    tris: list[tuple[int, int, int]] = []
                    cursor = 0
                    for count in counts:
                        c = int(count)
                        if c >= 3 and cursor + c <= len(indices):
                            i0 = int(indices[cursor])
                            for k in range(1, c - 1):
                                i1 = int(indices[cursor + k])
                                i2 = int(indices[cursor + k + 1])
                                tris.append((i0, i2, i1) if reflected else (i0, i1, i2))
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
                            common_attrs["contype"] = "0"
                            common_attrs["conaffinity"] = "0"
                            common_attrs["density"] = "0"
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
                        if has_col and volumetric:
                            shape_mass = _mesh_shape_mass(verts, tris, g_pos, g_quat)

                if emitted and has_col:
                    collision_geom_count += 1
                    if shape_mass is not None:
                        col_shapes.append(shape_mass)
            elif prim.IsA(UsdGeom.Xformable):
                children = prim.GetFilteredChildren(Usd.TraverseInstanceProxies())
                p_name_lower = prim.GetName().lower()
                is_geom_container = (
                    p_name_lower in ("visuals", "visual", "collisions", "collision")
                    and bool(children)
                    and all(c.IsA(UsdGeom.Gprim) for c in children)
                )
                if not is_geom_container:
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

        return (collision_geom_count, col_shapes)

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
        has_authored_com = False
        diag_val: Vec3 | None = None
        axes_quat: QuatWXYZ = (1.0, 0.0, 0.0, 0.0)
        has_authored_axes = False
        if b_prim.HasAPI(UsdPhysics.MassAPI):
            m_api = UsdPhysics.MassAPI(b_prim)
            raw_m = m_api.GetMassAttr().Get()
            if raw_m is not None and math.isfinite(float(raw_m)) and float(raw_m) > 0.0:
                mass_val = float(raw_m) * kg_per_unit
            raw_com = m_api.GetCenterOfMassAttr().Get()
            if (
                m_api.GetCenterOfMassAttr().HasAuthoredValue()
                and raw_com is not None
                and all(math.isfinite(float(c)) for c in raw_com)
            ):
                has_authored_com = True
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
            if m_api.GetPrincipalAxesAttr().HasAuthoredValue() and raw_axes is not None:
                has_authored_axes = True
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

        col_count, col_shapes = _add_body_geoms_and_sites(b_prim, body_elem)
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
        elif mass_val is not None and col_shapes:
            c_geom, computed_diag, computed_quat = _combine_shape_masses(col_shapes, mass_val)
            final_com = com_val if has_authored_com else c_geom
            final_quat = axes_quat if has_authored_axes else computed_quat
            inertial_elem = ET.Element(
                "inertial",
                {
                    "pos": _fmt(final_com),
                    "quat": _fmt(final_quat),
                    "mass": _fmt_scalar(mass_val),
                    "diaginertia": _fmt(computed_diag),
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
    if not action_keys:
        raise ValueError("action.jointKeys must not be empty")
    if len(set(action_keys)) != len(action_keys):
        raise ValueError(f"action.jointKeys contains duplicate keys: {action_keys!r}")

    canonical_keys = _expected_joint_keys(derived, action_keys, entity_key)
    joint_by_key = dict(zip(canonical_keys, derived.joints, strict=True))
    actuated_keys = [
        key for key, joint in zip(canonical_keys, derived.joints, strict=True) if joint.actuated
    ]

    unknown_action = [key for key in action_keys if key not in joint_by_key]
    if unknown_action:
        raise ValueError(
            f"action.jointKeys {unknown_action!r} not in derived USD joints {canonical_keys!r}"
        )
    unactuated_action = [key for key in action_keys if not joint_by_key[key].actuated]
    if unactuated_action:
        raise ValueError(
            f"action.jointKeys {unactuated_action!r} are unactuated in derived USD "
            f"(actuated={actuated_keys!r})"
        )

    action_joints = [joint_by_key[key] for key in action_keys]
    expected_defaults = [joint.default_position for joint in action_joints]
    expected_lower = [joint.lower for joint in action_joints]
    expected_upper = [joint.upper for joint in action_joints]
    expected_units = ["rad" if joint.kind == "hinge" else "m" for joint in action_joints]

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

    for item in doc.get("observations") or ():
        source = str(item.get("source") or "")
        if not source.startswith("joint_"):
            continue
        obs_keys = [str(k) for k in (item.get("keys") or ())]
        if len(set(obs_keys)) != len(obs_keys):
            raise ValueError(
                f"observation {item.get('name')!r} keys contain duplicate entries: {obs_keys!r}"
            )
        unknown = [k for k in obs_keys if k not in joint_by_key]
        if unknown:
            raise ValueError(
                f"observation {item.get('name')!r} keys {unknown!r} not in "
                f"derived USD joints {canonical_keys!r}"
            )
        obs_joints = [joint_by_key[k] for k in obs_keys]
        obs_unit = item.get("unit")
        if obs_unit is not None:
            for j in obs_joints:
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
            ref_offsets = [j.default_position for j in obs_joints]
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
            missing = [
                key
                for key, j in zip(action_keys, action_joints, strict=True)
                if key not in provided and j.name not in provided
            ]
            if missing:
                raise ValueError(f"Drive {label} missing controlled joints: {missing!r}")
            target = [
                float(provided[key] if key in provided else provided[j.name])
                for key, j in zip(action_keys, action_joints, strict=True)
            ]
        else:
            target = [float(v) for v in provided]
        actual = [float(getattr(j, attr)) for j in action_joints]
        if len(target) != len(actual) or any(
            abs(a - b) > atol for a, b in zip(target, actual, strict=True)
        ):
            raise ValueError(f"Drive {label} mismatch: expected {target}, derived USD has {actual}")

    _check_gains("kp", expected_kp, "stiffness")
    _check_gains("kd", expected_kd, "damping")

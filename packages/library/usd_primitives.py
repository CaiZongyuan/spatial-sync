"""GPU-free extraction of protocol Primitive Visuals from ASCII USDA and asset metadata."""

from __future__ import annotations

import math
import re
from pathlib import Path

_HEADER_RE = re.compile(r"^[ \t]*#[Uu][Ss][Dd][Aa]\b")
_PRIM_RE = re.compile(r'^(?:def|over)\s+(?:([A-Za-z_][A-Za-z0-9_]*)\s+)?"([^"]+)"')
_FLOAT_RE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_VEC3_RE = re.compile(rf"\(\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*\)")
_QUAT_RE = re.compile(
    rf"\(\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*,\s*({_FLOAT_RE})\s*\)"
)
_SCALAR_ATTR_RE = re.compile(
    r'^(?:uniform\s+)?(?:double|float|int|token|string)\s+([A-Za-z0-9_:]+)\s*=\s*(.+)$'
)
_VEC_ATTR_RE = re.compile(
    r'^(?:uniform\s+)?(?:double3|float3|quatf|quatd|vec3f|vec3d)\s+([A-Za-z0-9_:]+)\s*=\s*(.+)$'
)

_S = math.sqrt(0.5)
_AXIS_QUATS = {
    "Z": (0.0, 0.0, 0.0, 1.0),
    "X": (0.0, _S, 0.0, _S),
    "Y": (-_S, 0.0, 0.0, _S),
}
_Y_UP_TO_Z_UP = (_S, 0.0, 0.0, _S)


def _quat_mul(a: tuple[float, float, float, float], b: tuple[float, float, float, float]):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _quat_norm(q: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    length = math.sqrt(sum(c * c for c in q))
    if length <= 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    return tuple(c / length for c in q)  # type: ignore[return-value]


def _quat_rotate(q: tuple[float, float, float, float], v: tuple[float, float, float]):
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


def _strip_comments(line: str) -> str:
    in_quotes = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_quotes:
            escaped = True
            continue
        if char == '"':
            in_quotes = not in_quotes
        elif char == "#" and not in_quotes:
            return line[:index]
    return line


def _parse_xform(attrs: dict[str, str], unit_scale: float):
    translate = (0.0, 0.0, 0.0)
    orient = (0.0, 0.0, 0.0, 1.0)
    scale = (1.0, 1.0, 1.0)
    if raw := attrs.get("xformOp:translate"):
        if match := _VEC3_RE.search(raw):
            translate = tuple(float(match.group(i)) * unit_scale for i in (1, 2, 3))
    if raw := attrs.get("xformOp:orient"):
        if match := _QUAT_RE.search(raw):
            w, x, y, z = (float(match.group(i)) for i in (1, 2, 3, 4))
            orient = _quat_norm((x, y, z, w))
    if raw := attrs.get("xformOp:scale"):
        if match := _VEC3_RE.search(raw):
            scale = tuple(abs(float(match.group(i))) for i in (1, 2, 3))
    return translate, orient, scale


def _compose(parent_p, parent_q, parent_s, local_p, local_q, local_s):
    scaled_local = (local_p[0] * parent_s[0], local_p[1] * parent_s[1], local_p[2] * parent_s[2])
    rotated = _quat_rotate(parent_q, scaled_local)
    return (
        (parent_p[0] + rotated[0], parent_p[1] + rotated[1], parent_p[2] + rotated[2]),
        _quat_norm(_quat_mul(parent_q, local_q)),
        (parent_s[0] * local_s[0], parent_s[1] * local_s[1], parent_s[2] * local_s[2]),
    )


def _float_attr(attrs: dict[str, str], name: str, default: float) -> float:
    raw = attrs.get(name)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


def _token_attr(attrs: dict[str, str], name: str, default: str) -> str:
    raw = attrs.get(name)
    if raw is None:
        return default
    return raw.strip().strip('"') or default


def _prim_to_visual(prim_type: str, attrs: dict[str, str], world_p, world_q, world_s, unit_scale: float):
    if _token_attr(attrs, "visibility", "inherited") == "invisible":
        return None
    if _token_attr(attrs, "purpose", "default") == "guide":
        return None
    sx, sy, sz = world_s
    q = world_q
    if prim_type == "Cube":
        half = _float_attr(attrs, "size", 2.0) * 0.5 * unit_scale
        dims = (half * sx, half * sy, half * sz)
        if any(value <= 0 or not math.isfinite(value) for value in dims):
            return None
        geometry = {"type": "box", "halfExtents": [round(v, 9) for v in dims]}
    elif prim_type == "Sphere":
        radius = _float_attr(attrs, "radius", 1.0) * unit_scale * max(sx, sy, sz)
        if radius <= 0 or not math.isfinite(radius):
            return None
        geometry = {"type": "sphere", "radius": round(radius, 9)}
    elif prim_type in ("Cylinder", "Capsule", "Cone"):
        axis = _token_attr(attrs, "axis", "Z").upper()
        if axis not in _AXIS_QUATS:
            axis = "Z"
        if axis == "X":
            r_scale, h_scale = max(sy, sz), sx
        elif axis == "Y":
            r_scale, h_scale = max(sx, sz), sy
        else:
            r_scale, h_scale = max(sx, sy), sz
        radius = _float_attr(attrs, "radius", 1.0) * unit_scale * r_scale
        half_height = _float_attr(attrs, "height", 2.0) * 0.5 * unit_scale * h_scale
        if radius <= 0 or half_height < 0 or (prim_type != "Capsule" and half_height <= 0):
            return None
        if not (math.isfinite(radius) and math.isfinite(half_height)):
            return None
        q = _quat_norm(_quat_mul(world_q, _AXIS_QUATS[axis]))
        geometry = {
            "type": prim_type.lower(),
            "radius": round(radius, 9),
            "halfHeight": round(half_height, 9),
        }
    else:
        return None
    return {
        "kind": "primitive",
        "geometry": geometry,
        "localPose": {
            "p": [round(v, 9) for v in world_p],
            "q": [round(v, 9) for v in q],
        },
    }


def extract_usda_primitives(path: Path) -> list[dict]:
    """Extract Cube/Sphere/Cylinder/Capsule/Cone primitives from an ASCII .usda file."""
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return []
    if not _HEADER_RE.match(raw_bytes[:64].decode("utf-8", errors="ignore")):
        return []
    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()

    unit_scale = 1.0
    up_axis = "Z"
    index = 0
    # Scan optional stage metadata header (...) before the first prim.
    while index < len(lines):
        stripped = _strip_comments(lines[index]).strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if stripped.startswith("("):
            header_lines = [stripped]
            if ")" not in stripped:
                index += 1
                while index < len(lines):
                    part = _strip_comments(lines[index]).strip()
                    header_lines.append(part)
                    if ")" in part:
                        break
                    index += 1
            header = "\n".join(header_lines)
            if match := re.search(rf"metersPerUnit\s*=\s*({_FLOAT_RE})", header):
                unit_scale = float(match.group(1)) or 1.0
            if match := re.search(r'upAxis\s*=\s*"([YZ])"', header):
                up_axis = match.group(1)
            index += 1
        break

    root_q = _Y_UP_TO_Z_UP if up_axis == "Y" else (0.0, 0.0, 0.0, 1.0)
    stack: list[dict] = []
    pending_prim: tuple[str, str] | None = None
    in_parens = False
    visuals: list[dict] = []

    def finalize_prim(frame: dict):
        prim_type = frame["type"]
        if prim_type in ("Cube", "Sphere", "Cylinder", "Capsule", "Cone"):
            item = _prim_to_visual(
                prim_type,
                frame["attrs"],
                frame["world_p"],
                frame["world_q"],
                frame["world_s"],
                unit_scale,
            )
            if item is not None:
                visuals.append(item)

    def ensure_xform(frame: dict):
        if frame["xform_done"]:
            return
        frame["xform_done"] = True
        parent_p, parent_q, parent_s = (
            (stack[-2]["world_p"], stack[-2]["world_q"], stack[-2]["world_s"])
            if len(stack) >= 2
            else ((0.0, 0.0, 0.0), root_q, (1.0, 1.0, 1.0))
        )
        local_p, local_q, local_s = _parse_xform(frame["attrs"], unit_scale)
        world_p, world_q, world_s = _compose(parent_p, parent_q, parent_s, local_p, local_q, local_s)
        frame["world_p"], frame["world_q"], frame["world_s"] = world_p, world_q, world_s

    for line in lines[index:]:
        clean = _strip_comments(line).strip()
        if not clean:
            continue
        if pending_prim is not None:
            if in_parens:
                if ")" in clean:
                    in_parens = False
                    clean = clean.split(")", 1)[1].strip()
                else:
                    continue
            elif clean.startswith("("):
                if ")" in clean:
                    clean = clean.split(")", 1)[1].strip()
                else:
                    in_parens = True
                    continue
            if "{" in clean:
                prim_type, prim_name = pending_prim
                pending_prim = None
                stack.append({
                    "type": prim_type,
                    "name": prim_name,
                    "attrs": {},
                    "xform_done": False,
                    "world_p": (0.0, 0.0, 0.0),
                    "world_q": root_q,
                    "world_s": (1.0, 1.0, 1.0),
                })
                rest = clean.split("{", 1)[1].strip()
                if not rest:
                    continue
                clean = rest
            else:
                continue

        if clean == "}":
            if stack:
                frame = stack.pop()
                if not frame["xform_done"]:
                    parent_p, parent_q, parent_s = (
                        (stack[-1]["world_p"], stack[-1]["world_q"], stack[-1]["world_s"])
                        if stack
                        else ((0.0, 0.0, 0.0), root_q, (1.0, 1.0, 1.0))
                    )
                    local_p, local_q, local_s = _parse_xform(frame["attrs"], unit_scale)
                    wp, wq, ws = _compose(parent_p, parent_q, parent_s, local_p, local_q, local_s)
                    frame["world_p"], frame["world_q"], frame["world_s"] = wp, wq, ws
                finalize_prim(frame)
            continue

        if match := _PRIM_RE.match(clean):
            if stack:
                ensure_xform(stack[-1])
            prim_type = match.group(1) or "Xform"
            prim_name = match.group(2)
            tail = clean[match.end() :].strip()
            if tail.startswith("("):
                if ")" in tail:
                    tail = tail.split(")", 1)[1].strip()
                else:
                    pending_prim = (prim_type, prim_name)
                    in_parens = True
                    continue
            if "{" in tail:
                stack.append({
                    "type": prim_type,
                    "name": prim_name,
                    "attrs": {},
                    "xform_done": False,
                    "world_p": (0.0, 0.0, 0.0),
                    "world_q": root_q,
                    "world_s": (1.0, 1.0, 1.0),
                })
                after_brace = tail.split("{", 1)[1].strip()
                if after_brace == "}":
                    frame = stack.pop()
                    ensure_xform_standalone = (
                        (stack[-1]["world_p"], stack[-1]["world_q"], stack[-1]["world_s"])
                        if stack
                        else ((0.0, 0.0, 0.0), root_q, (1.0, 1.0, 1.0))
                    )
                    lp, lq, ls = _parse_xform(frame["attrs"], unit_scale)
                    wp, wq, ws = _compose(*ensure_xform_standalone, lp, lq, ls)
                    frame["world_p"], frame["world_q"], frame["world_s"] = wp, wq, ws
                    finalize_prim(frame)
            else:
                pending_prim = (prim_type, prim_name)
            continue

        if stack:
            attrs = stack[-1]["attrs"]
            if match := _VEC_ATTR_RE.match(clean):
                attrs[match.group(1)] = match.group(2)
            elif match := _SCALAR_ATTR_RE.match(clean):
                attrs[match.group(1)] = match.group(2)

    return visuals


def fallback_asset_visuals(metadata: dict, package_path: Path) -> list[dict]:
    """Return fallback Primitive Visual dicts when an Asset declares no visual GLB."""
    if metadata.get("visual"):
        return []
    stage = metadata.get("stage")
    if isinstance(stage, str):
        stage_path = package_path / stage
        try:
            resolved_stage = stage_path.resolve()
            resolved_pkg = package_path.resolve()
        except OSError:
            resolved_stage = None
            resolved_pkg = None
        if (
            resolved_stage is not None
            and resolved_pkg is not None
            and resolved_stage.is_relative_to(resolved_pkg)
            and resolved_stage.is_file()
        ):
            extracted = extract_usda_primitives(resolved_stage)
            if extracted:
                return extracted
    dimensions = metadata.get("dimensionsM")
    if (
        isinstance(dimensions, list)
        and len(dimensions) == 3
        and all(
            isinstance(v, (int, float))
            and not isinstance(v, bool)
            and v > 0
            and math.isfinite(v)
            for v in dimensions
        )
    ):
        dx, dy, dz = (float(v) for v in dimensions)
        return [
            {
                "kind": "primitive",
                "geometry": {
                    "type": "box",
                    "halfExtents": [round(dx * 0.5, 9), round(dy * 0.5, 9), round(dz * 0.5, 9)],
                },
                "localPose": {
                    "p": [0.0, 0.0, round(dz * 0.5, 9)],
                    "q": [0.0, 0.0, 0.0, 1.0],
                },
            }
        ]
    return []

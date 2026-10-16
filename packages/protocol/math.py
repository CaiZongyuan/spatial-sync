"""Small coordinate helpers shared by exporters and contract fixtures; no engine dependency."""

from __future__ import annotations

import math
from collections.abc import Sequence


def normalize_entity_key(label: str) -> str:
    if not isinstance(label, str) or not label or "?" in label or "#" in label:
        raise ValueError("entity label must be nonempty and cannot contain ? or #")
    key = label if label.startswith("/") else "/" + label
    if any(not segment for segment in key[1:].split("/")):
        raise ValueError("entity label cannot contain empty path segments")
    return key


def normalize_quaternion(q: Sequence[float]) -> tuple[float, float, float, float]:
    if len(q) != 4 or not all(math.isfinite(x) for x in q):
        raise ValueError("quaternion must contain four finite xyzw components")
    magnitude = math.hypot(*q)
    if magnitude < 1e-12:
        raise ValueError("quaternion cannot have zero length")
    return tuple(x / magnitude for x in q)  # type: ignore[return-value]


def same_hemisphere(previous: Sequence[float], current: Sequence[float]) -> tuple[float, ...]:
    a, b = normalize_quaternion(previous), normalize_quaternion(current)
    return tuple(-x for x in b) if sum(x * y for x, y in zip(a, b)) < 0 else b


def quaternion_multiply(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def rotate_vector(q: Sequence[float], vector: Sequence[float]) -> tuple[float, float, float]:
    x, y, z, w = normalize_quaternion(q)
    result = quaternion_multiply(quaternion_multiply((x, y, z, w), (*vector, 0)), (-x, -y, -z, w))
    return result[:3]


def compose_pose(body: dict, local: dict) -> dict:
    """Return worldBodyPose × localPose, preserving physics world coordinates."""
    offset = rotate_vector(body["q"], local["p"])
    return {
        "p": [a + b for a, b in zip(body["p"], offset)],
        "q": list(normalize_quaternion(quaternion_multiply(body["q"], local["q"]))),
    }


def z_up_to_y_up(vector: Sequence[float]) -> tuple[float, float, float]:
    x, y, z = vector
    return x, z, -y


def primitive_dimensions(geometry: dict, infinite_plane_size: float = 100) -> dict:
    """Three.js constructor dimensions; axial geometry rotates +π/2 locally once."""
    kind = geometry["type"]
    if kind == "box":
        return {"size": [2 * x for x in geometry["halfExtents"]], "rotateX": 0}
    if kind == "sphere":
        return {"radius": geometry["radius"], "rotateX": 0}
    if kind in ("capsule", "cylinder", "cone"):
        return {"radius": geometry["radius"], "height": 2 * geometry["halfHeight"], "rotateX": math.pi / 2}
    if kind == "plane":
        return {"size": [geometry["width"] or infinite_plane_size, geometry["length"] or infinite_plane_size], "rotateX": 0}
    raise ValueError(f"unsupported primitive: {kind}")


def joint_position_error(kind: str, parent: dict, child: dict, axis: Sequence[float] | None = None) -> float | None:
    if kind in ("free", "d6") or kind not in ("fixed", "revolute", "prismatic", "ball"):
        return None
    delta = tuple(a - b for a, b in zip(child["p"], parent["p"]))
    if kind == "prismatic":
        if axis is None:
            raise ValueError("prismatic joint requires a parent-anchor local axis")
        length = math.sqrt(sum(x * x for x in axis))
        if length < 1e-12:
            raise ValueError("joint axis cannot have zero length")
        world_axis = rotate_vector(parent["q"], tuple(x / length for x in axis))
        travel = sum(x * y for x, y in zip(delta, world_axis))
        delta = tuple(x - travel * y for x, y in zip(delta, world_axis))
    return math.sqrt(sum(x * x for x in delta))

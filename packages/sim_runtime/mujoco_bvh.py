"""Rebuild native CPU MuJoCo body collision bounds in their current inertial frames."""

import numpy as np

_ROUNDING = 64 * np.finfo(np.float64).eps
_FLAT_EPSILON = 1e-14  # MuJoCo 3.15 compiler: user_objects.cc, flat AAMM rule.


def _rotation(quaternion):
    import mujoco

    result = np.empty(9, dtype=np.float64)
    mujoco.mju_quat2Mat(result, np.asarray(quaternion, dtype=np.float64))
    return result.reshape(3, 3)


def _box(lower, upper):
    flat = np.abs(upper - lower) < _FLAT_EPSILON
    lower = np.where(flat, lower - _FLAT_EPSILON, lower)
    upper = np.where(flat, upper + _FLAT_EPSILON, upper)
    centre = lower + (upper - lower) * 0.5
    half = np.maximum(centre - lower, upper - centre)
    return np.r_[centre, half]


def _union(first, second):
    return _box(
        np.minimum(first[:3] - first[3:], second[:3] - second[3:]),
        np.maximum(first[:3] + first[3:], second[:3] + second[3:]),
    )


def normalize_body_bvh(model) -> tuple[int, ...]:
    """Normalize trusted native-compiled body trees, preserving mesh/flex trees and topology.

    Newton can update a body's principal inertia axes after native compilation.
    Rebuild from current geometry, collecting complete candidates before writing.
    Existing conservative trees are retained; returned IDs identify repaired bodies.
    """
    boxes = np.asarray(model.bvh_aabb)
    children = np.asarray(model.bvh_child)
    identities = np.asarray(model.bvh_nodeid)
    updates = []
    for body in range(model.nbody):
        address, count = int(model.body_bvhadr[body]), int(model.body_bvhnum[body])
        if count == 0:
            continue
        if address < 0 or address + count > len(boxes):
            raise ValueError("Invalid rigid body BVH range")
        current = boxes[address : address + count]
        if not np.all(np.isfinite(current)) or np.any(current[:, 3:] < 0):
            raise ValueError("Invalid rigid body BVH bounds")
        order = []
        seen = np.zeros(count, dtype=np.uint8)
        stack = [(0, False)]
        while stack:
            node, done = stack.pop()
            if not 0 <= node < count:
                raise ValueError("Rigid body BVH child is not body-local")
            if done:
                seen[node] = 2
                order.append(node)
                continue
            if seen[node]:
                raise ValueError("Rigid body BVH must be a tree")
            seen[node] = 1
            identity = int(identities[address + node])
            first, second = map(int, children[address + node])
            stack.append((node, True))
            if identity >= 0:
                if (
                    first != -1
                    or second != -1
                    or identity >= model.ngeom
                    or int(model.geom_bodyid[identity]) != body
                ):
                    raise ValueError("Invalid rigid body BVH geom leaf")
            else:
                if first < 0 or second < 0 or first == second:
                    raise ValueError("Invalid rigid body BVH internal node")
                stack.extend(((second, False), (first, False)))
        if not np.all(seen == 2):
            raise ValueError("Rigid body BVH contains unreachable nodes")
        inertia_position = np.asarray(model.body_ipos[body], dtype=np.float64)
        inertia_rotation = _rotation(model.body_iquat[body])
        raw = np.empty_like(current, dtype=np.float64)
        candidate = np.empty_like(raw)
        roundoff = np.empty((count, 3), dtype=np.float64)
        valid = True
        for node in order:
            identity = int(identities[address + node])
            if identity >= 0:
                geom = np.asarray(model.geom_aabb[identity], dtype=np.float64)
                position = np.asarray(model.geom_pos[identity], dtype=np.float64)
                rotation = _rotation(model.geom_quat[identity])
                if not np.all(
                    np.isfinite(
                        np.r_[geom, position, rotation.ravel(), inertia_position, inertia_rotation.ravel()]
                    )
                ) or np.any(geom[3:] < 0):
                    raise ValueError("Invalid current geometry for rigid body BVH")
                transform = inertia_rotation.T @ rotation
                centre = inertia_rotation.T @ (position - inertia_position + rotation @ geom[:3])
                half = np.abs(transform) @ geom[3:]
                raw[node] = _box(centre - half, centre + half)
                # Coordinate-scaled arithmetic error; this does not change geometry/contact margins.
                scale = np.abs(inertia_rotation.T) @ (
                    np.abs(position)
                    + np.abs(inertia_position)
                    + np.abs(rotation) @ (np.abs(geom[:3]) + geom[3:])
                )
                roundoff[node] = _ROUNDING * np.maximum(scale, np.abs(centre) + half)
                candidate[node] = raw[node]
                candidate[node, 3:] = np.nextafter(raw[node, 3:] + roundoff[node], np.inf)
            else:
                first, second = map(int, children[address + node])
                raw[node] = _union(raw[first], raw[second])
                roundoff[node] = np.maximum(
                    np.maximum(roundoff[first], roundoff[second]),
                    _ROUNDING * (np.abs(raw[node, :3]) + raw[node, 3:]),
                )
                candidate[node] = _union(candidate[first], candidate[second])
                candidate[node, 3:] = np.nextafter(candidate[node, 3:] + roundoff[node], np.inf)
            lower, upper = current[node, :3] - current[node, 3:], current[node, :3] + current[node, 3:]
            needed_lower, needed_upper = raw[node, :3] - raw[node, 3:], raw[node, :3] + raw[node, 3:]
            valid &= bool(
                np.all(lower - roundoff[node] <= needed_lower)
                and np.all(upper + roundoff[node] >= needed_upper)
            )
        if not valid:
            if not np.all(np.isfinite(candidate)) or np.any(candidate[:, 3:] < 0):
                raise ValueError("Rigid body BVH candidate is not finite")
            updates.append((body, address, candidate))
    for _, address, candidate in updates:
        boxes[address : address + len(candidate)] = candidate
    return tuple(body for body, _, _ in updates)

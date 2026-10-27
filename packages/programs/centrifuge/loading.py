"""Conservative whole-body loading checks, separate from seated occupancy."""

import numpy as np

from packages.programs.tube.facts import tube_location


def rotation(quaternion):
    q = np.asarray(quaternion, dtype=float)
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    if not np.isfinite(q).all() or not np.isfinite(norm).all() or np.any(norm < 1e-12):
        raise ValueError("Invalid physical orientation")
    x, y, z, w = np.moveaxis(q / norm, -1, 0)
    return np.stack([1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w),
                     2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w),
                     2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)], axis=-1).reshape(*q.shape[:-1], 3, 3)


def transformed_bounds(bounds, pose, frame):
    """Enclose a body's local collision bounds in the actual rotor frame."""
    inverse = rotation(frame[3:]).T
    basis = inverse @ rotation(pose[..., 3:])
    center = (pose[..., :3]-frame[:3]) @ inverse.T + np.einsum(
        "...ij,...j->...i", basis, bounds.mean(axis=-2))
    extent = np.einsum("...ij,...j->...i", np.abs(basis), (bounds[..., 1, :]-bounds[..., 0, :])/2)
    return np.stack([center-extent, center+extent], axis=-2)


def physical_loading(port, tubes, sites, site_keys, rotor, bounds, held=()):
    slots, issues = [None]*len(sites), []
    if bounds is None:
        return slots, [{"reason": "unknown_geometry"}]
    try:
        rotor_pose = port.body[rotor]
        if not np.isfinite(rotor_pose).all():
            raise ValueError("Invalid rotor position")
        rotation(rotor_pose[3:])
        site_poses = port.site_poses(sites)
        if not np.isfinite(site_poses).all():
            raise ValueError("Invalid Site position")
        for site in site_poses:
            rotation(site[3:])
    except (ValueError, TypeError, IndexError, KeyError):
        return slots, [{"reason": "invalid_rotor_pose"}]
    region = bounds[rotor].copy()
    region[1, 2] += max((value[1, 2]-value[0, 2] for key, value in bounds.items() if key != rotor),
                        default=0)
    if not tubes:
        return slots, issues
    identities = list(tubes)
    bodies = list(tubes.values())
    try:
        poses = port.body[bodies]
    except (TypeError, IndexError, KeyError):
        return slots, [{"reason": "invalid_physics"}]
    if not np.isfinite(poses).all():
        return slots, [{"entityKey": key, "reason": "invalid_physics"}
                       for key, pose in zip(identities, poses, strict=True) if not np.isfinite(pose).all()]
    try:
        actual = transformed_bounds(np.array([bounds[body] for body in bodies]), poses, rotor_pose)
    except ValueError:
        return slots, [{"entityKey": key, "reason": "invalid_physics"}
                       for key, pose in zip(identities, poses, strict=True)
                       if np.linalg.norm(pose[3:]) < 1e-12 or not np.isfinite(np.linalg.norm(pose[3:]))]
    overlaps = np.all(actual[:, 1] >= region[0], axis=1) & np.all(actual[:, 0] <= region[1], axis=1)
    for tube_index in np.flatnonzero(overlaps):
        key, body, pose = identities[tube_index], bodies[tube_index], poses[tube_index]
        try:
            if key in held:
                issues.append({"entityKey": key, "reason": "held_in_rotor"})
                continue
            velocity = port.relative_site_velocity(body, sites)
            location = tube_location(pose, velocity, site_poses, site_keys)
            if location not in site_keys:
                issues.append({"entityKey": key, "reason": "not_seated"})
                continue
            index = site_keys.index(location)
            if slots[index] is not None:
                issues.append({"entityKey": key, "reason": "duplicate_occupancy", "site": location,
                               "otherEntityKey": slots[index]})
            else:
                slots[index] = key
        except (ValueError, FloatingPointError):
            issues.append({"entityKey": key, "reason": "invalid_physics"})
    return slots, issues

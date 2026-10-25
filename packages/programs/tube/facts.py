"""Physical location facts. Tube body origins and Sites both denote the bottom."""

import math

import numpy as np

UPRIGHT_COSINE = math.cos(math.radians(20))


def upright(quaternion):
    q = np.asarray(quaternion, dtype=float)
    norm = float(q @ q)
    return (
        norm > 0 and math.isfinite(norm) and 1 - 2 * (q[0] ** 2 + q[1] ** 2) / norm > UPRIGHT_COSINE
    )


def _site_positions(pose, sites):
    pose = np.asarray(pose, dtype=float)
    q = sites[:, 3:] / np.linalg.norm(sites[:, 3:], axis=1)[:, None]
    if pose.ndim == 1:
        delta = pose[:3] - sites[:, :3]
    else:
        delta = pose[None, :, :3] - sites[:, None, :3]
        q = q[:, None, :]
    inverse = -q[..., :3]
    x, y, z = inverse[..., 0], inverse[..., 1], inverse[..., 2]
    dx, dy, dz = delta[..., 0], delta[..., 1], delta[..., 2]
    # Both 3D cross products preserve the original multiply/subtract/add order.
    tx = 2 * (y * dz - z * dy)
    ty = 2 * (z * dx - x * dz)
    tz = 2 * (x * dy - y * dx)
    w = q[..., 3]
    return np.stack(
        (dx + w * tx + (y * tz - z * ty),
         dy + w * ty + (z * tx - x * tz),
         dz + w * tz + (x * ty - y * tx)), axis=-1
    )


def site_relative_velocities(previous_pose, pose, previous_sites, sites, dt):
    """Derivative of actual tube-bottom position in each actual Site frame.

    This includes the rotational velocity field at an offset tube; subtracting
    only Site-origin world velocity misses that term.
    Batched body poses return [Site, body, xyz]; one body returns [Site, xyz].
    """
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("The shared physics sample interval must be positive")
    previous_sites, sites = np.asarray(previous_sites, dtype=float), np.asarray(sites, dtype=float)
    return (_site_positions(pose, sites) - _site_positions(previous_pose, previous_sites)) / dt


def tube_location(pose, velocity, site_poses, site_keys, *, held=False):
    """Velocity is one vector for static Sites, or one relative vector per Site."""
    if held:
        return "held"
    pose, sites = tuple(map(float, pose)), np.asarray(site_poses, dtype=float)
    if not all(map(math.isfinite, pose)) or not upright(pose[3:]) or len(sites) == 0:
        return "loose"
    velocities = np.atleast_2d(velocity)
    if len(velocities) not in (1, len(sites)):
        raise ValueError("Velocity requires one vector or one vector per Site")
    x, y, z = pose[:3]
    for index, site in enumerate(sites):
        if abs(float(site[2]) - z) < 0.02:
            dx, dy = float(site[0]) - x, float(site[1]) - y
            if math.sqrt(dx * dx + dy * dy) < 0.015:
                # Keep input precision and the strict speed limit; distant Sites need no norm.
                row = 0 if len(velocities) == 1 else index
                if np.linalg.norm(velocities[row:row + 1], axis=1)[0] < 0.05:
                    return site_keys[index]
    return "loose"

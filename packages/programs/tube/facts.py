"""Physical location facts. Tube body origins and Sites both denote the bottom."""

import math

import numpy as np


def upright(quaternion):
    q = np.asarray(quaternion, dtype=float)
    norm = float(q @ q)
    return (
        norm > 0 and np.isfinite(norm) and 1 - 2 * (q[0] ** 2 + q[1] ** 2) / norm > math.cos(math.radians(20))
    )


def _site_positions(pose, sites):
    delta = np.asarray(pose, dtype=float)[:3] - sites[:, :3]
    q = sites[:, 3:] / np.linalg.norm(sites[:, 3:], axis=1)[:, None]
    inverse = -q[:, :3]
    turn = 2 * np.cross(inverse, delta)
    return delta + q[:, 3, None] * turn + np.cross(inverse, turn)


def site_relative_velocities(previous_pose, pose, previous_sites, sites, dt):
    """Derivative of actual tube-bottom position in each actual Site frame.

    This includes the rotational velocity field at an offset tube; subtracting
    only Site-origin world velocity misses that term.
    """
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("The shared physics sample interval must be positive")
    previous_sites, sites = np.asarray(previous_sites, dtype=float), np.asarray(sites, dtype=float)
    return (_site_positions(pose, sites) - _site_positions(previous_pose, previous_sites)) / dt


def tube_location(pose, velocity, site_poses, site_keys, *, held=False):
    """Velocity is one vector for static Sites, or one relative vector per Site."""
    if held:
        return "held"
    pose, sites = np.asarray(pose, dtype=float), np.asarray(site_poses, dtype=float)
    if not np.all(np.isfinite(pose)) or not upright(pose[3:]) or len(sites) == 0:
        return "loose"
    delta = sites[:, :3] - pose[:3]
    speeds = np.linalg.norm(np.atleast_2d(velocity), axis=1)
    matches = np.flatnonzero(
        (np.linalg.norm(delta[:, :2], axis=1) < 0.015) & (np.abs(delta[:, 2]) < 0.02) & (speeds < 0.05)
    )
    return site_keys[int(matches[0])] if len(matches) else "loose"

"""Grasp frames move with their physical carrier, never with a target Site."""

import numpy as np

from packages.programs.centrifuge.loading import rotation


def grasp_geometry(body_pose, tcp_pose, offset, diameter, opening):
    """Qualify a cylindrical handle between the calibrated FR3 finger pads.

    Dimensions are metres. The 2 mm closure allowance accommodates pad contact;
    it cannot qualify an open hand or a handle wider than the physical opening.
    """
    try:
        body, tcp, offset = (np.asarray(value, dtype=float) for value in (body_pose, tcp_pose, offset))
        if body.shape != (7,) or tcp.shape != (7,) or offset.shape != (3,):
            return False
        if not np.isfinite(np.r_[body, tcp, offset, diameter, opening]).all():
            return False
        if not 0 < diameter <= 0.078 or not 0 <= opening <= 0.081:
            return False
        body_basis, tcp_basis = rotation(body[3:]), rotation(tcp[3:])
        point = body[:3] + body_basis @ offset
        local = tcp_basis.T @ (point - tcp[:3])
        upright = body_basis[2, 2] >= np.cos(np.deg2rad(5))
        return bool(
            upright
            and abs(local[0]) < 0.012
            and abs(local[1]) < 0.008
            and abs(local[2]) < 0.012
            and diameter - 0.002 <= opening <= diameter + 2 * abs(local[1]) + 0.002
        )
    except (TypeError, ValueError, IndexError):
        return False

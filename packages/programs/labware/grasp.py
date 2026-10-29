"""Grasp frames move with their physical carrier, never with a target Site."""

import numpy as np

from packages.programs.centrifuge.loading import rotation


def grasp_geometry(body_pose, tcp_pose, offset, diameter, opening):
    """Check the moving grip frame and upper opening bound, without contact proof.

    Dimensions are metres. Joint-coordinate sums are not actual pad aperture;
    physical_grasp must independently establish bilateral contact.
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
            and opening <= diameter + 2 * abs(local[1]) + 0.002
        )
    except (TypeError, ValueError, IndexError):
        return False


def physical_grasp(
    body_pose, tcp_pose, offset, diameter, opening, contacts, *, body, fingers, pad_shapes, shape_prefix,
    contact_allowance_m=.001,
):
    """Require two actual pads on the intended surface within the scene allowance."""
    if not np.isfinite(contact_allowance_m) or not 0 < contact_allowance_m <= .002:
        return False
    if not grasp_geometry(body_pose, tcp_pose, offset, diameter, opening):
        return False
    touching = set()
    try:
        if len(set(fingers)) != 2 or set(pad_shapes) != set(fingers):
            return False
        for contact in contacts:
            if body not in contact.bodies:
                continue
            index = contact.bodies.index(body)
            other = contact.bodies[1 - index]
            shape = contact.shapes[index]
            if other not in fingers or not (shape == shape_prefix or shape.startswith(shape_prefix + "/")):
                continue
            if not np.isfinite(contact.distance_m) or contact.distance_m < -contact_allowance_m:
                return False
            if contact.distance_m <= 0:
                if contact.shapes[1 - index] != pad_shapes[other]:
                    return False
                touching.add(other)
        return touching == set(fingers)
    except (ValueError, TypeError, IndexError, AttributeError):
        return False

"""Tube grasp coordinates; real TCP and bottom occupancy stay distinct."""

import math

from packages.protocol.math import rotate_vector


def validate_height(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("grasp height must be a finite nonnegative number")
    return value


def tube_grasp_point(position, rotation, height=0.0):
    """The actual tube-local +Z point, expressed in world coordinates."""
    height = validate_height(height)
    if height == 0:
        return tuple(position)
    offset = rotate_vector(rotation, (0, 0, height))
    return tuple(value + step for value, step in zip(position, offset, strict=True))


def grasp_point_in_tcp(bottom_displacement, body_rotation, tcp_rotation, height=0.0):
    """Transform a body-bottom displacement and its rotated offset into real F.

    Keeping the existing bottom subtraction at the caller preserves the zero
    path's floating-point order; no new body rotation is needed when h is zero.
    """
    point = tube_grasp_point(bottom_displacement, body_rotation, height)
    inverse = [-tcp_rotation[0], -tcp_rotation[1], -tcp_rotation[2], tcp_rotation[3]]
    return rotate_vector(inverse, point)


def nominal_grasp_target(bottom_position, height=0.0):
    """An upright seated tube uses world +Z, independent of the hand's rotation."""
    height = validate_height(height)
    x, y, z = bottom_position
    target = (x, y, z + height)
    if not all(math.isfinite(value) for value in target):
        raise ValueError("nominal grasp target must be finite")
    return target


def upright_tube_bottom(hand_position, height=0.0):
    """Synthetic's upright tube bottom lies below the unchanged virtual hand."""
    height = validate_height(height)
    x, y, z = hand_position
    return x, y, z - height

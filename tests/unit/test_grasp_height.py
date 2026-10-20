"""Independent coordinate counterexamples; no physics engine or Source."""

import math

import numpy as np
import pytest

from packages.programs.robot_arm.grasp import (
    grasp_point_in_tcp,
    nominal_grasp_target,
    tube_grasp_point,
    upright_tube_bottom,
)

IDENTITY = [0.0, 0.0, 0.0, 1.0]


def test_tilted_tube_uses_its_upper_point_even_when_bottom_is_aligned():
    rotation = [0, math.sin(math.radians(9.5)), 0, math.cos(math.radians(9.5))]
    point = tube_grasp_point([0, 0, 0], rotation, 0.100)
    assert point == pytest.approx([0.03255681544571567, 0, 0.09455185755993168], abs=1e-14)
    local = grasp_point_in_tcp([0, 0, -0.100], rotation, IDENTITY, 0.100)
    assert local == pytest.approx([0.03255681544571567, 0, -0.00544814244006832], abs=1e-14)
    assert abs(local[0]) > 0.016  # Existing real-finger region must reject it.


def test_height_offset_is_rotated_into_the_real_tcp_for_release_y():
    body_rotation = [0, math.sin(math.radians(9.5)), 0, math.cos(math.radians(9.5))]
    tcp_rotation = [0, 0, math.sqrt(0.5), math.sqrt(0.5)]
    local = grasp_point_in_tcp([0, 0, -0.1], body_rotation, tcp_rotation, 0.1)
    assert local == pytest.approx([0, -0.03255681544571567, -0.00544814244006832], abs=1e-14)


def test_zero_preserves_the_original_real_tcp_displacement():
    delta = np.array([0.01, -0.005, 0.02], dtype=np.float32)
    tcp_rotation = [0, 0, math.sqrt(0.5), math.sqrt(0.5)]
    local = grasp_point_in_tcp(delta, IDENTITY, tcp_rotation)
    assert local == pytest.approx([-0.005, -0.01, 0.02], abs=1e-9)
    assert tube_grasp_point([1, 2, 3], IDENTITY) == (1, 2, 3)


def test_nominal_height_keeps_bottom_site_and_does_not_reinterpret_old_fk():
    bottom = [1.2, -0.4, 0.962]
    original = list(bottom)
    target = nominal_grasp_target(bottom, 0.080)
    assert target == pytest.approx([1.2, -0.4, 1.042])
    assert math.dist(bottom, target) == pytest.approx(0.080)
    assert bottom == original


def test_synthetic_upright_bottom_is_below_the_unchanged_hand():
    hand = [0.55, -0.3, 0.90]
    bottom = upright_tube_bottom(hand, 0.080)
    assert bottom == pytest.approx([0.55, -0.3, 0.82])
    assert tube_grasp_point(bottom, IDENTITY, 0.080) == pytest.approx(hand)
    assert upright_tube_bottom(hand) == tuple(hand)


@pytest.mark.parametrize("height", [-0.001, float("nan"), float("inf"), -float("inf"), True, "0.08", None])
def test_coordinate_entrypoints_reject_invalid_heights(height):
    with pytest.raises(ValueError, match="finite nonnegative"):
        nominal_grasp_target([0, 0, 0], height)

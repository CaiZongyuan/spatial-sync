import math

import numpy as np
import pytest

from packages.programs.tube.facts import site_relative_velocities, tube_location, upright


def test_tube_bottom_origin_distance_speed_and_tilt_boundaries():
    sites = np.array([[1, 2, 3, 0, 0, 0, 1]])
    pose = [1, 2, 3, 0, 0, 0, 1]
    assert tube_location(pose, [0, 0, 0], sites, ["slot"]) == "slot"
    assert tube_location(pose, [0.05, 0, 0], sites, ["slot"]) == "loose"
    assert tube_location([1.0151, 2, 3, 0, 0, 0, 1], [0, 0, 0], sites, ["slot"]) == "loose"
    assert tube_location([1, 2, 3.0201, 0, 0, 0, 1], [0, 0, 0], sites, ["slot"]) == "loose"
    assert tube_location(pose, [1, 0, 0], sites, ["slot"], held=True) == "held"
    assert upright([0, 0, 0, 1])
    assert not upright([0.174, 0, 0, 0.985])


def test_fixed_fourteen_mm_offset_corotates_at_sixty_rpm_without_relative_motion():
    previous_sites = [[0, 0, 0, 0, 0, 0, 1]]
    current_sites = [[0, 0, 0, 0, 0, math.sqrt(0.5), math.sqrt(0.5)]]
    previous = [0.014, 0, 0, 0, 0, 0, 1]
    current = [0, 0.014, 0, 0, 0, 0, 1]
    # One quarter turn in .25s is60rpm. The tube's world speed exceeds .05,
    # while its location inside the actual rotating Site frame is constant.
    assert np.linalg.norm(np.subtract(current[:3], previous[:3])) / 0.25 > 0.05
    relative = site_relative_velocities(previous, current, previous_sites, current_sites, 0.25)
    np.testing.assert_allclose(relative, [[0, 0, 0]], atol=1e-15)
    assert tube_location(current, relative, current_sites, ["slot"]) == "slot"


def test_actual_relative_slip_rejects_even_inside_distance_tolerance():
    previous_sites = [[0, 0, 0, 0, 0, 0, 1]]
    current_sites = [[0, 0, 0, 0, 0, math.sqrt(0.5), math.sqrt(0.5)]]
    previous = [0, 0, 0, 0, 0, 0, 1]
    current = [0, 0.014, 0, 0, 0, 0, 1]
    relative = site_relative_velocities(previous, current, previous_sites, current_sites, 0.25)
    np.testing.assert_allclose(relative, [[0.056, 0, 0]], atol=1e-15)
    assert tube_location(current, relative, current_sites, ["slot"]) == "loose"


def test_static_site_motion_reduces_to_world_bottom_origin_motion():
    sites = [[1, 2, 3, 0, 0, 0, 1]]
    previous = [1, 2, 3, 0, 0, 0, 1]
    current = [1.01, 2, 3, 0, 0, 0, 1]
    relative = site_relative_velocities(previous, current, sites, sites, 0.1)
    np.testing.assert_allclose(relative, [[0.1, 0, 0]], atol=1e-14)
    assert tube_location(current, relative, sites, ["slot"]) == "loose"
    with pytest.raises(ValueError):
        site_relative_velocities(previous, current, sites, sites, 0)


def test_velocity_threshold_is_evaluated_for_each_candidate_site():
    sites = [[0, 0, 0, 0, 0, 0, 1], [0.01, 0, 0, 0, 0, 0, 1]]
    pose = [0.005, 0, 0, 0, 0, 0, 1]
    assert tube_location(pose, [[0.06, 0, 0], [0, 0, 0]], sites, ["moving", "stationary"]) == "stationary"


def test_batched_bottom_origins_keep_independent_corotation_and_slip():
    previous = [[0.014, 0, 0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0, 1]]
    current = [[0, 0.014, 0, 0, 0, 0, 1], [0, 0.014, 0, 0, 0, 0, 1]]
    before = [[0, 0, 0, 0, 0, 0, 1], [0.01, 0, 0, 0, 0, math.sqrt(0.5), math.sqrt(0.5)]]
    after = [[0, 0, 0, 0, 0, math.sqrt(0.5), math.sqrt(0.5)], [0, 0.01, 0, 0, 0, 1, 0]]
    velocity = site_relative_velocities(previous, current, before, after, 0.25)
    np.testing.assert_allclose(
        velocity, [[[0, 0, 0], [0.056, 0, 0]], [[0, 0, 0], [0, -0.056, 0]]], atol=1e-15
    )


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_each_velocity_dtype_keeps_the_strict_fifty_mm_per_second_boundary(dtype):
    limit = dtype(0.05)
    sites = [[0, 0, 0, 0, 0, 0, 1]]
    pose = [0, 0, 0, 0, 0, 0, 1]
    below = np.array([np.nextafter(limit, dtype(0)), 0, 0], dtype=dtype)
    at = np.array([limit, 0, 0], dtype=dtype)
    above = np.array([np.nextafter(limit, dtype(1)), 0, 0], dtype=dtype)
    assert tube_location(pose, below, sites, ["slot"]) == "slot"
    assert tube_location(pose, at, sites, ["slot"]) == "loose"
    assert tube_location(pose, above, sites, ["slot"]) == "loose"


def test_strict_spatial_boundaries_and_first_matching_site_order():
    sites = [[0, 0, 0, 0, 0, 0, 1]]
    pose = [0, 0, 0, 0, 0, 0, 1]
    for coordinate, limit in ((0, 0.015), (2, 0.02)):
        boundary = pose.copy()
        boundary[coordinate] = limit
        assert tube_location(boundary, [0, 0, 0], sites, ["slot"]) == "loose"
        boundary[coordinate] = np.nextafter(limit, 0)
        assert tube_location(boundary, [0, 0, 0], sites, ["slot"]) == "slot"
    overlapping = [[0.014, 0, 0, 0, 0, 0, 1], [0, 0, 0, 0, 0, 0, 1]]
    assert tube_location(pose, [0, 0, 0], overlapping, ["first", "nearest"]) == "first"
    assert tube_location(pose, [[0.06, 0, 0], [0, 0, 0]], overlapping, ["first", "nearest"]) == "nearest"


@pytest.mark.parametrize("scale", [1, 3, 1e100])
def test_nonunit_quaternions_keep_tilt_classification_on_both_sides_of_twenty_degrees(scale):
    def quaternion(degrees):
        angle = math.radians(degrees) / 2
        return [scale*math.sin(angle), 0, 0, scale*math.cos(angle)]

    assert upright(quaternion(19.999))
    assert not upright(quaternion(20.001))
    assert not upright([0, 0, 0, 0])


@pytest.mark.parametrize("coordinate", range(7))
def test_nonfinite_pose_is_loose_but_a_held_tube_does_not_depend_on_pose(coordinate):
    pose = [0, 0, 0, 0, 0, 0, 1]
    pose[coordinate] = math.nan
    sites = [[0, 0, 0, 0, 0, 0, 1]]
    assert tube_location(pose, [0, 0, 0], sites, ["slot"]) == "loose"
    assert tube_location(pose, [0, 0, 0], sites, ["slot"], held=True) == "held"


def test_translated_nonunit_x_rotation_keeps_batched_corotation_and_independent_slip():
    before_site = [[0, 0, 0, 0, 0, 0, 1]]
    after_site = [[1, 2, 3, 3*math.sqrt(0.5), 0, 0, 3*math.sqrt(0.5)]]
    before = [[0, 0.014, 0, 0, 0, 0, 1], [0, 0, 0.007, 0, 0, 0, 1]]
    after = [[1, 2, 3.014, 0, 0, 0, 1], [1, 1.993, 3.006, 0, 0, 0, 1]]
    velocity = site_relative_velocities(before, after, before_site, after_site, 0.1)
    np.testing.assert_allclose(velocity, [[[0, 0, 0], [0, 0.06, 0]]], atol=1e-14)

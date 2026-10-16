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

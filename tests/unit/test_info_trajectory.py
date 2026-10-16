import numpy as np
import pytest

from packages.programs.robot_arm.trajectory import plan_trajectory


def test_synchronized_trapezoid_matches_worked_one_radian_motion():
    motion = plan_trajectory([0, 2], [1, 2.25], [1, 1], speed_scale=1)
    assert motion.duration == pytest.approx(1.5)
    for t, q, v in [(0.25, 0.0625, 0.5), (0.5, 0.25, 1), (0.75, 0.5, 1), (1.25, 0.9375, 0.5), (1.5, 1, 0)]:
        positions, velocities = motion.sample(t)
        assert positions[0] == pytest.approx(q)
        assert velocities[0] == pytest.approx(v)
    q, v = motion.sample(10)
    np.testing.assert_allclose(q, [1, 2.25])
    np.testing.assert_allclose(v, [0, 0])


def test_triangular_motion_direction_stationary_axis_and_speed_scaling():
    motion = plan_trajectory([1, 3], [0.5, 3], [5, 5], speed_scale=1)
    assert motion.duration == pytest.approx(1)
    q, v = motion.sample(0.5)
    np.testing.assert_allclose(q, [0.75, 3])
    np.testing.assert_allclose(v, [-1, 0])
    slow = plan_trajectory([0], [1], [1], speed_scale=0.5, speed=0.5)
    assert slow.duration == pytest.approx(4.125)
    q, v = slow.sample(2.0625)
    assert q[0] == pytest.approx(0.5)
    assert v[0] == pytest.approx(0.25)
    zero = plan_trajectory([2], [2], [1])
    assert zero.duration == 0
    assert zero.sample(0)[0].tolist() == [2]


@pytest.mark.parametrize(
    "start,end,limits", [([0], [1, 2], [1]), ([float("nan")], [0], [1]), ([0], [1], [0])]
)
def test_trajectory_rejects_invalid_limits_and_nonfinite_coordinates(start, end, limits):
    with pytest.raises(ValueError):
        plan_trajectory(start, end, limits)

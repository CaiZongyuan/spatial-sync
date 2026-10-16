import numpy as np
import pytest

from packages.sim_runtime.pose_filter import PoseFilter


def test_invalid_pose_holds_previous_authority_and_warns_after_30_consecutive_frames():
    validator = PoseFilter(1)
    valid = np.array([[1, 2, 3, 0, 0, 0, 2]], dtype=float)
    assert validator.update(valid)[0].tolist() == [1, 2, 3, 0, 0, 0, 1]
    invalid = np.array([[np.nan, 2, 3, 0, 0, 0, 0]])
    for _ in range(29):
        assert validator.update(invalid)[0].tolist() == [1, 2, 3, 0, 0, 0, 1]
        assert validator.warning_indices == []
    validator.update(invalid)
    assert validator.warning_indices == [0]
    assert validator.total_invalid == 30
    validator.update(valid)
    assert validator.consecutive_invalid.tolist() == [0]


def test_invalid_initial_snapshot_fails_instead_of_inventing_physics_pose():
    with pytest.raises(ValueError, match="initial"):
        PoseFilter(1).update(np.zeros((1, 7)))

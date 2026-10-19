"""CPU bounds for the one authorized time-curve replacement."""

import pytest
import torch

from fr3_contact.motion import quintic_fraction


def test_quintic_retains_endpoints_bounds_and_original_fifteen_step_settle():
    values = [quintic_fraction(min(1.0, (step + 1) / 85)) for step in range(100)]
    assert quintic_fraction(0.0) == 0.0
    assert quintic_fraction(1.0) == 1.0
    assert all(0 <= value <= 1 for value in values)
    assert values == sorted(values)
    assert values[84:] == [1.0] * 16
    assert values[0] < (1 / 85) / 100


def test_quintic_pose_targets_remain_finite_with_normalized_quaternion_and_same_endpoints():
    start = torch.tensor([0.428, 0.0, 0.516])
    target = torch.tensor([0.45, -0.15, 0.135])
    start_q = torch.tensor([0.999, 0.0447102, 0, 0])
    start_q /= start_q.norm()
    target_q = torch.tensor([0.9238795, -0.3826834, 0, 0])
    target_q /= target_q.norm()
    for u in torch.linspace(0, 1, 101).tolist():
        fraction = quintic_fraction(u)
        position = torch.lerp(start, target, fraction)
        quaternion = torch.lerp(start_q, target_q, fraction)
        quaternion /= quaternion.norm()
        assert torch.isfinite(position).all() and torch.isfinite(quaternion).all()
        assert quaternion.norm().item() == pytest.approx(1.0, abs=1e-6)
        assert ((position >= torch.minimum(start, target)) & (position <= torch.maximum(start, target))).all()
    assert torch.lerp(start, target, quintic_fraction(0)).equal(start)
    assert torch.lerp(start, target, quintic_fraction(1)).equal(target)

import pytest

from packages.sim_runtime.pacer import Pacer


def test_realtime_budget_preserves_fraction_and_drops_excess_lag():
    pacer = Pacer(physics_dt=0.01, realtime_factor=2, max_lag=0.25, now=0)
    assert pacer.steps_due(0.004) == 0
    assert pacer.steps_due(0.01) == 2
    assert pacer.steps_due(10) == 50
    assert pacer.dropped_wall_seconds == pytest.approx(9.74)
    pacer.reset_clock(11)
    assert pacer.steps_due(11.005) == 1


def test_unlimited_pacer_returns_bounded_work_without_accumulating_clock_debt():
    pacer = Pacer(physics_dt=1 / 240, realtime_factor=None, now=0, max_batch=8)
    assert pacer.steps_due(0) == 8
    assert pacer.steps_due(100) == 8

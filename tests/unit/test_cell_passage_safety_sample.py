"""A sampling fact advances with the actual safety evaluation, even if True stays True."""

import pytest

from tests.fixtures.cell_passage import build_workcell_host
from tests.unit.test_cell_passage import invoke


def test_quiescence_sample_uses_actual_physics_time_and_refreshes_unchanged_true(tmp_path):
    host, port = build_workcell_host(tmp_path)
    port.last_time = 0.75
    host.tick(1.0, 0.1)
    first = host.values()["/workcell"]
    assert first["controlSafe"] is True and first["controlSafeAtSimTime"] == 0.75
    port.last_time = 0.85
    host.tick(1.1, 0.1)
    second = host.values()["/workcell"]
    assert second["controlSafe"] is True and second["controlSafeAtSimTime"] == 0.85


@pytest.mark.parametrize("sample", [None, float("nan"), float("inf")])
def test_unknown_actual_frame_does_not_fall_back_to_a_new_global_clock(sample, tmp_path):
    host, port = build_workcell_host(tmp_path)
    port.last_time = sample
    host.tick(10.0, 0.1)
    assert host.values()["/workcell"]["controlSafeAtSimTime"] is None


def test_forced_not_safe_is_not_a_new_measurement(tmp_path):
    host, port = build_workcell_host(tmp_path)
    port.last_time = 0.75
    host.tick(1.0, 0.1)
    port.last_time = 0.85
    assert invoke(host, "move", "moving", position=[0.2, 0, 0]).status == "accepted"
    host.tick(1.1, 0.1)
    values = host.values()["/workcell"]
    assert values["controlSafe"] is False
    assert values["controlSafeAtSimTime"] == 0.75


def test_definitive_confirm_not_safe_reports_the_actual_evaluation_frame(tmp_path):
    host, port = build_workcell_host(tmp_path)
    assert invoke(host, "requestControl", "request", nextOwner="next", mode="auto").status == "accepted"
    host.tick(0.1, 0.1)
    port.last_time = 0.15
    port.velocities["pump"] = 0.001
    result = invoke(host, "confirmControl", "confirm", epoch=1)
    assert result.status == "rejected" and result.error.code == "not_safe"
    values = host.values()["/workcell"]
    assert values["controlSafe"] is False and values["controlSafeAtSimTime"] == 0.15
    assert result.error.details["controlSafeAtSimTime"] == 0.15

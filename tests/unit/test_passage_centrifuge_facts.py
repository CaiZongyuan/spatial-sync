"""Passage centrifuge facts accept actual NumPy physics feedback at HostCore."""

from tests.fixtures.centrifuge_cycles import build_cycle_host


def test_host_initializes_lid_and_control_facts_from_numpy_joint_feedback(tmp_path):
    host, _, _ = build_cycle_host(tmp_path)
    facts = host.values()["/cent"]
    assert facts["lidClosed"] is True
    assert facts["lidOpen"] is False
    assert facts["controlSafe"] is True
    assert facts["completedCycle"] == ""

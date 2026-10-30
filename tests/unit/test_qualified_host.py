"""Actual Host/Motion handoff paths over explicit external synthetic feedback."""

from packages.programs.labware.motion import LabwareMotionProgram
from packages.protocol.schema import InvokeRequest
from tests.fixtures.qualified_host import build_qualified_host


def perform(host, port, action, location, events):
    result = host.invoke(
        InvokeRequest(
            invocationId=action,
            requestId=action,
            entityKey="/arm",
            action=action,
            params={"location": location, "owner": "auto", "epoch": 0, "operationId": action},
            epoch=0,
            createdSimTime=host.sim_time,
            createdAtMs=0,
        )
    )
    assert result.status == "accepted", result
    for _ in range(1800):
        port.advance(1 / 60)
        events.extend(host.tick(host.sim_time + 1 / 60, 1 / 60))
        result = next(row for row in host.snapshot().invocations if row.invocationId == action)
        if result.status in ("succeeded", "failed", "cancelled"):
            return result
    raise AssertionError("Synthetic feedback action did not finish")


def test_host_pick_place_swaps_real_constraint_state_and_records_initial_events(tmp_path):
    host, port = build_qualified_host(tmp_path)
    events = host.drain_events()
    initial = [e for e in events if getattr(e, "event", None) == "constraintChanged"]
    assert len(initial) == 2 and all(e.data["operation"] == "initialize" for e in initial)
    assert port.enabled == {(-1, 0), (0, 1)}
    assert perform(host, port, "pick", "source", events).status == "succeeded"
    assert (-1, 0) not in port.enabled and (3, 0) in port.enabled and (0, 1) in port.enabled
    assert port.changes.index(("attach", 3, 0)) < port.changes.index(("detach", -1, 0))
    assert perform(host, port, "place", "destination", events).status == "succeeded"
    assert port.enabled == {(-1, 0), (0, 1)} and host.values()["/arm"]["holding"] is None
    assert port.changes[-2:] == [("attach", -1, 0), ("detach", 3, 0)]
    assert host.values()["/tube"]["station"] == "/destination"
    handoffs = [e.data for e in events if getattr(e, "event", None) == "constraintChanged"]
    assert {e["operation"] for e in handoffs} >= {"initialize", "attach", "detach"}
    assert all(e["positionErrorM"] < e["positionLimitM"] for e in handoffs)


def test_open_fingers_with_actual_remaining_contact_fail_without_retreat(tmp_path):
    host, port = build_qualified_host(tmp_path)
    events = host.drain_events()
    assert perform(host, port, "pick", "source", events).status == "succeeded"
    port.lingering = True
    result = perform(host, port, "place", "destination", events)
    assert result.status == "failed" and result.error.code == "place_failed"
    assert port.enabled == {(-1, 0), (0, 1)}  # Object remains safely fixed, not in the hand.
    assert port.width > 0.049 and port.q[1] == 0  # No retreat until actual finger contact clears.


def motion_context(host):
    index = next(i for i, p in enumerate(host._programs) if isinstance(p, LabwareMotionProgram))
    return host._programs[index], host._contexts[index]


def test_recovery_open_preserves_unanchored_actual_hand_payload(tmp_path):
    host, port = build_qualified_host(tmp_path)
    assert perform(host, port, "pick", "source", []).status == "succeeded"
    motion, ctx = motion_context(host)
    width = port.width
    motion._open(ctx)
    assert motion.gripper_goal == width and not motion.pending_release
    assert (3, 0) in port.enabled and (-1, 0) not in port.enabled
    from types import SimpleNamespace

    rejection = motion.accept(SimpleNamespace(action="setGripper", params={"width": 0.05}), ctx)
    assert rejection.code == "placement_required"


def test_opposite_actual_finger_motion_cannot_release_station(tmp_path):
    import numpy as np

    host, port = build_qualified_host(tmp_path)
    motion, ctx = motion_context(host)
    motion.pick_tube = "/tube"
    port.weld(3, 0)
    motion.stable = 0.19
    motion.last_width = port.width
    port.joint_qd = lambda joint: np.array([0.01 if joint.key == "f0" else -0.01])
    assert not motion._gripper_settled(ctx, "pick", port.width, 0.02)
    assert (-1, 0) in port.enabled and (3, 0) in port.enabled


def test_required_initial_anchor_failure_aborts_host_startup(tmp_path, monkeypatch):
    import pytest

    from packages.programs.labware.placement import QualifiedPlacements

    def fail(self, ctx):
        raise ValueError("initial actual penetration")

    monkeypatch.setattr(QualifiedPlacements, "initialize", fail)
    with pytest.raises(
        ValueError, match="Required program /arm initialization failed.*initial actual penetration"
    ):
        build_qualified_host(tmp_path)


def test_required_anchor_failure_also_aborts_reset(tmp_path, monkeypatch):
    import pytest

    from packages.programs.labware.placement import QualifiedPlacements

    host, _ = build_qualified_host(tmp_path)

    def fail(self, ctx):
        raise ValueError("reset actual penetration")

    monkeypatch.setattr(QualifiedPlacements, "initialize", fail)
    with pytest.raises(
        ValueError, match="Required program /arm initialization failed.*reset actual penetration"
    ):
        host.reset(1)

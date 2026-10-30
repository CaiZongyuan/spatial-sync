"""Actual Host/Motion handoff paths over explicit external synthetic feedback."""

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

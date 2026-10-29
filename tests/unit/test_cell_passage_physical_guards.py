"""Actual carried endpoint and contact safety at the HostCore boundary."""

from types import SimpleNamespace

import pytest

from tests.fixtures.cell_passage import build_workcell_host
from tests.unit.test_cell_passage import execute, invoke


def ready(tmp_path):
    host, port = build_workcell_host(tmp_path, config={"alignmentToleranceM": 0.0003})
    execute(host, port, "reserve", "res", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    return host, port


@pytest.mark.parametrize("fault", ["displaced", "unknown", "fast"])
def test_liquid_rejects_bad_actual_tip_even_when_nominal_head_is_aligned(tmp_path, fault):
    host, port = ready(tmp_path)
    if fault == "displaced":
        port.body_positions["/tip1"] = [0.001, 0, 0]
    elif fault == "unknown":
        port.body_positions["/tip1"] = None
    else:
        port.body_velocities["/tip1"] = [0.001, 0, 0]
    result = invoke(host, "aspirate", "asp", vessel="source", volumeUl=40)
    assert result.status == "rejected"
    assert result.error.code == "not_aligned"
    assert port.targets["pump"] == 0
    assert host.values()["/workcell"]["inFlightUl"] == 0


def test_liquid_uses_actual_carried_fluid_endpoint_not_nominal_head_site(tmp_path):
    host, port = ready(tmp_path)
    port.positions["tip"] = [0.002, 0, 0]
    execute(host, port, "aspirate", "asp", vessel="source", volumeUl=40)
    assert host.values()["/workcell"]["inFlightUl"] == pytest.approx(40)


@pytest.mark.parametrize("fault", ["moving_body", "held_tip", "unknown"])
def test_contact_fault_holds_retains_and_accounts_braking_before_failure(tmp_path, fault):
    host, port = ready(tmp_path)
    assert invoke(host, "aspirate", "asp", vessel="source", volumeUl=100).status == "accepted"
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    port.velocities["pump"] = 0.001
    if fault == "unknown":
        port.contact_error = "backend contact facts missing"
    else:
        body = "/pipette" if fault == "moving_body" else "/tip1"
        port.contacts = [SimpleNamespace(bodies=(body, "/obstacle"), shapes=("a", "b"), distance_m=-0.0011)]
    host.tick(host.sim_time + 0.1, 0.1)
    assert port.targets["pump"] == 0.0004
    assert host.values()["/workcell"]["controlSafe"] is False
    assert port.welds == {("/pipette", "/tip1")}
    assert host.active_invocations("/workcell")
    port.q["pump"] = 0.0005  # Real braking motion after the holding target.
    port.velocities["pump"] = 0
    host.tick(host.sim_time + 0.1, 0.1)
    state = host.values()["/workcell"]
    assert state["inFlightUl"] == pytest.approx(50)
    assert state["materialSnapshot"]["vessels"][0]["volumeUl"] == pytest.approx(450)
    assert port.welds == {("/pipette", "/tip1")}
    assert host.active_invocations("/workcell")
    port.q["pump"] = 0.0004  # Settled at the actual holding position: net transfer is 40 uL.
    host.tick(host.sim_time + 0.1, 0.1)
    assert host.values()["/workcell"]["inFlightUl"] == pytest.approx(40)
    terminal = next(x for x in host.snapshot().invocations if x.invocationId == "asp")
    assert terminal.status == "failed"
    assert terminal.error.code == ("contact_unavailable" if fault == "unknown" else "unsafe_contact")


@pytest.mark.parametrize(
    "body,depth,accepted",
    [("/pipette", 0.001, True), ("/tip1", 0.0009, True),
     ("/unrelated", 0.002, True), ("/tip1", 0.00101, False)],
)
def test_contact_boundary_respects_owned_bodies_and_existing_allowance(tmp_path, body, depth, accepted):
    host, port = ready(tmp_path)
    port.contacts = [SimpleNamespace(bodies=(body, "/obstacle"), shapes=("a", "b"), distance_m=-depth)]
    result = invoke(host, "move", "move", position=[0.1, 0, 0])
    assert (result.status == "accepted") == accepted
    if not accepted:
        assert result.error.code == "unsafe_contact"
        assert host.values()["/workcell"]["lastContactDepthM"] == depth


def test_missing_moving_body_declaration_is_unknown_not_empty_safe_contacts(tmp_path):
    host, _ = build_workcell_host(tmp_path, config={"movingBodyKeys": []})
    assert host.values()["/workcell"]["contactFactsAvailable"] is False
    assert host.values()["/workcell"]["controlSafe"] is False
    assert invoke(host, "move", "move", position=[0.1, 0, 0]).error.code == "contact_unavailable"


def test_liquid_start_rechecks_actual_tip_after_queued_acceptance(tmp_path):
    host, port = ready(tmp_path)
    assert invoke(host, "aspirate", "asp", vessel="source", volumeUl=40).status == "accepted"
    port.body_positions["/tip1"] = [0.001, 0, 0]
    host.tick(host.sim_time + 0.1, 0.1)
    terminal = next(x for x in host.snapshot().invocations if x.invocationId == "asp")
    assert terminal.status == "failed"
    assert terminal.error.code == "not_aligned"
    assert port.targets["pump"] == 0

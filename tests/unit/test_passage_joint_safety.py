"""Idle arm state cannot hide the payload from an active rail or door."""

from types import SimpleNamespace

import pytest

from packages.protocol.schema import InvokeRequest
from tests.fixtures.passage_joint_host import build_joint_host


def invoke(host, action, *, entity="/joint", token=None, **params):
    token = token or action
    return host.invoke(InvokeRequest(
        invocationId="inv-" + token, requestId="req-" + token, entityKey=entity, action=action,
        params=({"owner": "auto", "epoch": 0, "operationId": "op-" + token, **params}
                if entity == "/joint" else params),
        actor={"kind": "cli", "name": "test"}, epoch=0,
        createdSimTime=host.sim_time, createdAtMs=0,
    ))


def test_idle_arm_held_cap_contact_blocks_rail_without_releasing_the_weld(tmp_path):
    host, port = build_joint_host(tmp_path)
    port.contacts = [SimpleNamespace(bodies=("/cap", "/obstacle"), shapes=("cap", "obstacle"),
                                    distance_m=-0.0011)]
    assert not host.active_invocations("/armbody")
    result = invoke(host, "move", position=0.5)
    assert result.status == "rejected" and result.error.code == "unsafe_contact"
    assert port.target == port.q and port.welds == {("/armbody", "/cap")}


@pytest.mark.parametrize("body", ["/armbody", "/cap"])
def test_idle_arm_or_payload_in_actual_door_sweep_blocks_close(tmp_path, body):
    host, port = build_joint_host(tmp_path, door=True)
    port.positions[body] = [0, 0, 1]
    assert not host.active_invocations("/armbody")
    result = invoke(host, "close")
    assert result.status == "rejected" and result.error.code == "arm_in_door_sweep"
    assert port.target == port.q and port.welds


def test_actual_outer_boundary_clearance_is_accepted(tmp_path):
    host, port = build_joint_host(tmp_path, door=True)
    port.positions["/armbody"] = [0.601001, 0, 1]  # minX .501001 is just outside the 1 mm clearance.
    assert invoke(host, "close").status == "accepted"


@pytest.mark.parametrize("unknown", ["contact", "bounds", "pose"])
def test_unknown_physical_safety_never_becomes_empty_safe_data(tmp_path, unknown):
    host, port = build_joint_host(tmp_path, door=True, bounds_error=unknown == "bounds")
    if unknown == "contact":
        port.contact_error = True
    elif unknown == "pose":
        port.positions["/armbody"] = None
    result = invoke(host, "close")
    assert result.status == "rejected"
    assert result.error.code in ("contact_unavailable", "unknown_clearance")
    assert port.target == port.q and port.welds


def latest(host, token):
    return next(row for row in host.snapshot().invocations if row.invocationId == "inv-" + token)


def test_start_rechecks_clearance_after_admission(tmp_path):
    host, port = build_joint_host(tmp_path, door=True)
    assert invoke(host, "close").status == "accepted"
    port.positions["/cap"] = [0, 0, 1]
    host.tick(0.1, 0.1)
    assert latest(host, "close").status == "failed"
    assert latest(host, "close").error.code == "arm_in_door_sweep"
    assert port.target == port.q and port.welds


def test_late_epoch_holds_rail_and_retains_payload(tmp_path):
    host, port = build_joint_host(tmp_path)
    assert invoke(host, "move", position=0.5).status == "accepted"
    host.tick(0.1, 0.1)
    assert invoke(host, "setEpoch", entity="/control", epoch=1).status == "accepted"
    host.tick(0.2, 0.1)
    assert latest(host, "move").status == "failed"
    assert latest(host, "move").error.code == "stale_control"
    assert port.target == port.q and port.welds


def test_epoch_is_rechecked_while_cancelled_motion_is_braking(tmp_path):
    host, port = build_joint_host(tmp_path)
    assert invoke(host, "move", position=0.5).status == "accepted"
    host.tick(0.1, 0.1)
    host.cancel("inv-move")
    assert invoke(host, "setEpoch", entity="/control", epoch=1).status == "accepted"
    host.tick(0.2, 0.1)
    assert latest(host, "move").error.code == "stale_control"
    assert port.target == port.q and port.welds


def test_concurrent_hold_change_is_included_while_rail_is_moving(tmp_path):
    host, port = build_joint_host(tmp_path, holding=None)
    assert invoke(host, "move", position=0.5).status == "accepted"
    host.tick(0.1, 0.1)
    invoke(host, "report", entity="/armbody", holding="/cap")
    port.contacts = [SimpleNamespace(bodies=("/cap", "/obstacle"), shapes=("cap", "obstacle"),
                                    distance_m=-0.0012)]
    host.tick(0.2, 0.1)
    assert latest(host, "move").error.code == "unsafe_contact"
    assert port.target == port.q and port.welds


def test_closed_carried_lid_remains_watched_if_it_slips_out_of_closed_state(tmp_path):
    host, port = build_joint_host(tmp_path, lid_closed=True)
    assert invoke(host, "move", position=0.5).status == "accepted"
    host.tick(0.1, 0.1)
    invoke(host, "report", entity="/cap", lidClosed=False)
    port.contacts = [SimpleNamespace(bodies=("/lid", "/obstacle"), shapes=("lid", "obstacle"),
                                    distance_m=-0.0012)]
    host.tick(0.2, 0.1)
    assert latest(host, "move").error.code == "unsafe_contact"
    assert port.target == port.q and port.welds


def test_exact_contact_allowance_and_unrelated_contacts_do_not_block_rail(tmp_path):
    host, port = build_joint_host(tmp_path)
    port.contacts = [SimpleNamespace(bodies=("/cap", "/obstacle"), shapes=("cap", "obstacle"),
                                    distance_m=-0.001),
                     SimpleNamespace(bodies=("/other", "/obstacle"), shapes=("other", "obstacle"),
                                     distance_m=-0.02)]
    assert invoke(host, "move", position=0.5).status == "accepted"


def test_door_clearance_is_measured_in_the_actual_anchor_frame(tmp_path):
    host, port = build_joint_host(tmp_path, door=True)
    port.positions["/anchor"] = [5, 0, 0]
    port.positions["/door"] = [5, 0, 1]
    port.positions["/cap"] = [5, 0, 1]
    assert invoke(host, "close").error.code == "arm_in_door_sweep"


def test_stop_can_always_establish_a_hold_even_when_contact_facts_are_unknown(tmp_path):
    host, port = build_joint_host(tmp_path)
    port.contact_error = True
    assert invoke(host, "stop").status == "accepted"
    host.tick(0.1, 0.1)
    assert port.target == port.q and port.welds


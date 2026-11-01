"""Completion evidence is measured by the program and submitted with its terminal update."""

import pytest

from packages.protocol.schema import InvokeRequest
from tests.fixtures.centrifuge_cycles import TUBES, advance_until_terminal, build_cycle_host


def start_cycle(host, identity="cycle", *, legacy=False):
    return host.invoke(InvokeRequest(
        invocationId=identity, requestId=identity, entityKey="/cent", action="start", epoch=0,
        createdSimTime=host.sim_time, createdAtMs=0,
        params={"rpm": 500, "durationSeconds": 1, **({} if legacy else {
            "owner": "auto", "epoch": 0, "operationId": identity,
        })},
    ))


def test_passage_cycle_returns_stopped_measured_load_and_turns_in_terminal_result(tmp_path):
    host, port, _ = build_cycle_host(tmp_path)
    assert start_cycle(host).status == "accepted"
    events = []
    terminal = advance_until_terminal(host, port, "cycle", events=events)
    assert terminal.status == "succeeded", terminal
    receipt = terminal.result
    assert receipt["schema"] == "lw.centrifuge-cycle-receipt/1"
    assert receipt["completedCycle"] == receipt["operationId"] == "cycle"
    assert receipt["entityKey"] == "/cent" and receipt["invocationId"] == "cycle"
    assert receipt["owner"] == "auto" and receipt["epoch"] == 0
    assert receipt["completedLoad"] == TUBES[:4]
    assert receipt["cycleRunSeconds"] == pytest.approx(1)
    assert receipt["actualRotorTurns"] > 0
    assert receipt["rotorStopped"] is True and receipt["lidClosed"] is True
    assert receipt["actualRpm"] < .05 * 60 / (2 * 3.141592653589793)
    assert receipt["completedSimTime"] == terminal.endedSimTime
    facts = host.values()["/cent"]
    assert facts["completedCycle"] == receipt["completedCycle"]
    assert facts["completedLoad"] == receipt["completedLoad"]
    updates = [row for row in events if getattr(row, "invocationId", None) == "cycle"
               and getattr(row, "status", None) == "succeeded"]
    assert len(updates) == 1 and updates[0].result == receipt


def test_legacy_centrifuge_keeps_none_result_and_original_event(tmp_path):
    host, port, _ = build_cycle_host(tmp_path, legacy=True)
    assert start_cycle(host, legacy=True).status == "accepted"
    events = []
    terminal = advance_until_terminal(host, port, "cycle", events=events)
    assert terminal.status == "succeeded" and terminal.result is None
    completed = [row for row in events if getattr(row, "event", None) == "runCompleted"]
    assert len(completed) == 1 and completed[0].data == {"rpm": 500, "durationSeconds": 1}


@pytest.mark.parametrize("fault", ["cancel", "zero_turns"])
def test_interrupted_or_unmoved_rotor_cannot_return_a_completed_receipt(tmp_path, fault):
    host, port, _ = build_cycle_host(tmp_path)
    assert start_cycle(host).status == "accepted"
    if fault == "zero_turns":
        port.frozen = True
    else:
        host.tick(.1, .1)
        host.cancel("cycle")
    if fault == "zero_turns":
        for _ in range(100):
            port.advance(.1)
            host.tick(host.sim_time + .1, .1)
        assert host.values()["/cent"]["actualRotorTurns"] == 0
        host.tick(host.sim_time + 3701, 3701)
        terminal = next(row for row in host.snapshot().invocations if row.invocationId == "cycle")
        assert terminal.error.code == "timeout"
    else:
        terminal = advance_until_terminal(host, port, "cycle")
    assert terminal.status != "succeeded"
    assert terminal.result is None
    assert host.values()["/cent"]["completedCycle"] == ""


def test_unknown_final_physical_load_fails_before_submitting_success(tmp_path):
    host, port, _ = build_cycle_host(tmp_path)
    assert start_cycle(host).status == "accepted"
    for _ in range(20):
        port.advance(.1)
        host.tick(host.sim_time + .1, .1)
    port.body[1, :3] = [2, 0, .02]  # One tube is no longer actually seated.
    events = []
    terminal = advance_until_terminal(host, port, "cycle", events=events)
    assert terminal.status == "failed" and terminal.error.code == "cycle_evidence_unavailable"
    assert terminal.result is None
    assert host.values()["/cent"]["completedCycle"] == ""
    assert not any(getattr(row, "event", None) == "runCompleted" for row in events)

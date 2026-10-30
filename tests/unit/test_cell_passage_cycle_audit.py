"""Read-only cycle audit rejects fabricated, stale or incomplete completion evidence."""

from copy import deepcopy

import pytest

from tests.fixtures.centrifuge_cycles import TUBES, advance_until_terminal, build_cycle_host
from tests.unit.test_centrifuge_cycle_receipts import start_cycle
from tools.cell_passage_audit import audit_cycle_receipt


@pytest.fixture(scope="module")
def measured_cycle(tmp_path_factory):
    host, port, _ = build_cycle_host(tmp_path_factory.mktemp("cycle-audit"))
    assert start_cycle(host).status == "accepted"
    return advance_until_terminal(host, port, "cycle").model_dump(mode="json")


def audit(invocation):
    return audit_cycle_receipt(invocation, entity="/cent", operation_id="cycle", load=TUBES[:4],
                               minimum_run_seconds=1)


def test_measured_terminal_result_is_valid_without_current_control_owner(measured_cycle):
    assert audit(measured_cycle)["status"] == "cycle_receipt_valid"


@pytest.mark.parametrize("fault", ["none", "cancelled", "zero_turns", "wrong_load", "wrong_operation",
                                 "wrong_owner", "moving", "short_run", "wrong_end_time", "unknown"])
def test_plan_completion_alone_or_bad_receipt_cannot_establish_a_batch(measured_cycle, fault):
    invocation = deepcopy(measured_cycle)
    if fault == "none":
        invocation["result"] = None
    elif fault == "cancelled":
        invocation["status"] = "cancelled"
    else:
        key, value = {
            "zero_turns": ("actualRotorTurns", 0), "wrong_load": ("completedLoad", TUBES[4:]),
            "wrong_operation": ("operationId", "other"), "wrong_owner": ("owner", "human"),
            "moving": ("actualRpm", 10), "short_run": ("cycleRunSeconds", .5),
            "wrong_end_time": ("completedSimTime", -1), "unknown": ("actualRotorTurns", None),
        }[fault]
        invocation["result"][key] = value
    assert audit(invocation)["status"] in ("invalid", "unavailable")

"""Cell Passage behavior through the public HostCore boundary."""

import pytest

from packages.protocol.schema import InvokeRequest
from tests.fixtures.cell_passage import build_workcell_host as workcell


def invoke(host, action, token, **params):
    return host.invoke(
        InvokeRequest(
            invocationId=token,
            requestId=token,
            entityKey="/workcell",
            action=action,
            epoch=0,
            createdSimTime=host.sim_time,
            createdAtMs=0,
            params={"owner": "auto", "epoch": 0, "operationId": token, **params},
        )
    )


def test_partial_stroke_cancel_retains_liquid_and_operation_identity(tmp_path):
    host, port = workcell(tmp_path)
    assert invoke(host, "reserve", "reserve", resource="/source", device="/workcell").status == "accepted"
    host.tick(0.05, 0.05)
    assert invoke(host, "pickupTip", "pick", tip="T1").status == "accepted"
    host.tick(0.1, 0.1)
    assert invoke(host, "aspirate", "asp", vessel="source", volumeUl=100).status == "accepted"
    host.tick(0.2, 0.1)
    port.advance(0.0004)
    host.tick(0.3, 0.1)
    host.cancel("asp")
    state = host.values()["/workcell"]["materialSnapshot"]
    assert state["vessels"][0]["volumeUl"] == 460
    assert state["tip"]["volumeUl"] == 40
    assert state["operations"][-1]["appliedUl"] == 40
    assert port.targets["pump"] == 0.0004
    retry = invoke(host, "aspirate", "new-request", operationId="asp", vessel="source", volumeUl=100)
    assert retry.error.code == "operation_applied"


def test_paused_control_accept_stops_old_targets_and_start_only_acknowledges(tmp_path):
    host, port = workcell(tmp_path)
    invoke(host, "move", "move", position=[0.2, 0.1, 0.05])
    host.tick(0.1, 0.1)
    assert port.targets["x"] == 0.2
    assert invoke(host, "requestControl", "handoff", nextOwner="human", mode="manual").status == "accepted"
    state = host.values()["/workcell"]
    assert (state["controlEpoch"], state["controlMode"], state["controlSafe"]) == (1, "holding", True)
    assert port.targets["x"] == 0
    assert invoke(host, "move", "late", position=[1, 1, 1]).error.code == "stale_control"
    # A second request identity with the same domain identity cannot apply a second transition.
    assert (
        invoke(
            host, "requestControl", "retry", operationId="handoff", nextOwner="human", mode="manual"
        ).status
        == "accepted"
    )
    host.tick(0.2, 0.1)
    assert host.values()["/workcell"]["controlEpoch"] == 1
    assert invoke(host, "confirmControl", "confirm", epoch=1).status == "accepted"
    assert host.values()["/workcell"]["controlOwner"] == "human"
    host.tick(0.3, 0.1)
    assert (host.values()["/workcell"]["controlEpoch"], host.values()["/workcell"]["controlMode"]) == (
        1,
        "manual",
    )


def test_mix_cancel_retains_actual_aspirated_fraction_of_cells(tmp_path):
    host, port = workcell(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 500,
                "culture": "M1",
                "live": 1000,
                "dead": 100,
            }
        ],
    )
    invoke(host, "reserve", "res", resource="/source", device="/workcell")
    host.tick(0.05, 0.05)
    invoke(host, "pickupTip", "pick", tip="T1")
    host.tick(0.1, 0.05)
    invoke(host, "mix", "mix", vessel="source", volumeUl=100)
    host.tick(0.2, 0.1)
    port.advance(0.0004)
    host.tick(0.3, 0.1)
    host.cancel("mix")
    state = host.values()["/workcell"]["materialSnapshot"]
    assert state["tip"]["volumeUl"] == 40
    assert state["tip"]["cells"][0]["suspendedLive"] == 80
    assert state["vessels"][0]["cells"][0]["suspendedLive"] == 920


def test_partial_dispense_uses_reverse_executed_stroke_and_conserves_cells(tmp_path):
    host, port = workcell(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 500,
                "culture": "M1",
                "live": 1000,
                "dead": 100,
            },
            {
                "id": "child",
                "entityKey": "/child",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "culture": "D1",
                "parent": "M1",
            },
        ],
    )
    for resource in ("/source", "/child"):
        invoke(host, "reserve", resource, resource=resource, device="/workcell")
        host.tick(host.sim_time + 0.05, 0.05)
    invoke(host, "pickupTip", "pick", tip="T1")
    host.tick(0.2, 0.1)
    invoke(host, "aspirate", "asp", vessel="source", volumeUl=100)
    host.tick(0.3, 0.1)
    port.advance(0.001)
    host.tick(0.4, 0.1)
    assert invoke(host, "dispense", "disp", vessel="child", volumeUl=100).status == "accepted"
    host.tick(0.5, 0.1)
    assert port.targets["pump"] == 0
    port.advance(0.0006)
    host.tick(0.6, 0.1)
    host.cancel("disp")
    state = host.values()["/workcell"]["materialSnapshot"]
    assert abs(state["tip"]["volumeUl"] - 60) < 1e-8
    assert abs(state["vessels"][1]["volumeUl"] - 40) < 1e-8
    assert abs(state["vessels"][1]["cells"][0]["suspendedLive"] - 80) < 1e-8
    assert state["vessels"][1]["parent"] == "M1"
    assert host.values()["/workcell"]["auditPassed"] is True


def test_handoff_requires_measured_standstill_not_only_written_hold_target(tmp_path):
    host, port = workcell(tmp_path)
    invoke(host, "move", "move", position=[0.2, 0.1, 0.05])
    host.tick(0.1, 0.1)
    port.velocities["x"] = 0.5
    invoke(host, "requestControl", "handoff", nextOwner="human", mode="manual")
    assert host.values()["/workcell"]["controlSafe"] is False
    assert invoke(host, "confirmControl", "premature", epoch=1).error.code == "not_safe"
    port.velocities["x"] = 0
    host.tick(0.2, 0.1)
    assert invoke(host, "confirmControl", "settled", epoch=1).status == "accepted"


@pytest.mark.parametrize("invalid_feedback", [0.0015, 0.0002])
def test_pump_overtravel_or_reverse_feedback_cannot_report_a_balanced_transfer(tmp_path, invalid_feedback):
    host, port = workcell(tmp_path)
    invoke(host, "reserve", "res", resource="/source", device="/workcell")
    host.tick(0.05, 0.05)
    invoke(host, "pickupTip", "pick", tip="T1")
    host.tick(0.1, 0.05)
    invoke(host, "aspirate", "asp", vessel="source", volumeUl=100)
    host.tick(0.2, 0.1)
    port.advance(0.0004)
    host.tick(0.3, 0.1)
    port.q["pump"] = invalid_feedback  # External actuator fault, beyond target or reversing.
    host.tick(0.4, 0.1)
    state = host.values()["/workcell"]
    assert state["auditPassed"] is False
    assert state["materialSnapshot"]["reconciled"] is False
    assert state["materialSnapshot"]["operations"][-1]["fault"] == "pump_feedback_invalid"
    assert state["materialSnapshot"]["operations"][-1]["observedUl"] == pytest.approx(
        invalid_feedback * 100000
    )
    assert port.targets["pump"] == invalid_feedback


def test_model_pellet_requires_actual_completed_load_and_applies_cycle_once(tmp_path):
    host, _ = workcell(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 500,
                "culture": "M1",
                "live": 1000,
            }
        ],
        facts={},
    )
    assert (
        invoke(
            host, "pellet", "wrong-cycle", centrifuge="/peer", cycle="missing", vessels=["source"]
        ).error.code
        == "cycle_incomplete"
    )
    assert (
        invoke(host, "pellet", "pellet", centrifuge="/peer", cycle="cycle-1", vessels=["source"]).status
        == "accepted"
    )
    host.tick(0.1, 0.1)
    cell = host.values()["/workcell"]["materialSnapshot"]["vessels"][0]["cells"][0]
    assert (cell["suspendedLive"], cell["pelletLive"]) == (0, 1000)
    assert (
        invoke(
            host, "pellet", "duplicate", centrifuge="/peer", cycle="cycle-1", vessels=["source"]
        ).error.code
        == "cycle_applied"
    )


@pytest.mark.parametrize(
    "facts,code",
    [
        ({"completedLoad": []}, "wrong_load"),
        ({"cycleRunSeconds": 0.1}, "cycle_incomplete"),
        ({"actualRotorTurns": 0}, "cycle_incomplete"),
        ({"phase": "decelerating"}, "cycle_incomplete"),
    ],
)
def test_pellet_rejects_unproven_physical_processing(tmp_path, facts, code):
    host, _ = workcell(tmp_path, facts=facts)
    assert (
        invoke(host, "pellet", "pellet", centrifuge="/peer", cycle="cycle-1", vessels=["source"]).error.code
        == code
    )


def test_count_is_simulated_observation_of_actual_loaded_sample_and_never_changes_truth(tmp_path):
    host, _ = workcell(
        tmp_path,
        vessels=[
            {
                "id": "sample",
                "entityKey": "/peer",
                "site": "/sourceSite",
                "capacityUl": 100,
                "volumeUl": 20,
                "culture": "M1",
                "live": 80,
                "dead": 8,
            }
        ],
        config={"imagerStation": "/imager"},
        facts={},
    )
    invoke(host, "reserve", "res", resource="/peer", device="/workcell")
    host.tick(0.1, 0.1)
    assert invoke(host, "count", "count", vessel="sample").status == "accepted"
    host.tick(0.2, 0.1)
    state = host.values()["/workcell"]["materialSnapshot"]
    assert state["observations"][-1]["source"] == "simulated-state-model"
    assert (state["observations"][-1]["live"], state["observations"][-1]["dead"]) == (80, 8)
    assert state["vessels"][0]["volumeUl"] == 20


def test_teaching_wait_only_advances_with_simulation_clock(tmp_path):
    host, _ = workcell(tmp_path)
    assert invoke(host, "wait", "wait", seconds=2).status == "accepted"
    host.tick(0.1, 0.1)
    host.tick(0.1, 0.1)
    assert host.active_invocations("/workcell")[0].status == "running"
    host.tick(2, 1.9)
    assert host.active_invocations("/workcell")[0].status == "running"
    host.tick(2.1, 0.1)
    assert host.active_invocations("/workcell") == []


def execute(host, port, action, token, **params):
    response = invoke(host, action, token, **params)
    assert response.status == "accepted", response
    for _ in range(4):
        host.tick(host.sim_time + 0.1, 0.1)
        if not host.active_invocations("/workcell"):
            terminal = next(row for row in host.snapshot().invocations if row.invocationId == token)
            assert terminal.status == "succeeded", terminal
            return
        port.advance(port.targets["pump"])
        for axis in ("x", "y", "z"):
            port.q[axis] = port.targets[axis]
    raise AssertionError("Fixture actuator did not finish action")


def test_six_origins_remain_independent_across_sample_and_twelve_daughters(tmp_path):
    vessels = [
        {"id": "waste", "entityKey": "/waste", "site": "/sourceSite", "capacityUl": 10000, "waste": True}
    ]
    for index in range(1, 7):
        culture = f"M{index}"
        vessels.append(
            {
                "id": culture,
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
                "culture": culture,
                "live": 1000,
                "dead": 100,
            }
        )
        for destination in ("A", "B", "sample"):
            vessels.append(
                {
                    "id": f"{destination}{index}",
                    "entityKey": "/" + destination,
                    "site": "/sourceSite",
                    "capacityUl": 2000,
                    "culture": f"{destination}{index}",
                    "parent": culture,
                }
            )
    host, port = workcell(
        tmp_path,
        vessels=vessels,
        config={
            "wasteSite": "/sourceSite",
            "tips": [{"id": f"T{index}", "site": "/T1", "bodyKey": f"/tip{index}"} for index in range(1, 7)],
        },
    )
    for resource in ("/source", "/A", "/B", "/sample"):
        execute(host, port, "reserve", resource, resource=resource, device="/workcell")
    for index in range(1, 7):
        execute(host, port, "pickupTip", f"pick{index}", tip=f"T{index}")
        for destination, volume in ((f"sample{index}", 20), (f"A{index}", 490), (f"B{index}", 490)):
            execute(host, port, "aspirate", "asp-" + destination, vessel=f"M{index}", volumeUl=volume)
            execute(host, port, "dispense", "disp-" + destination, vessel=destination, volumeUl=volume)
        execute(host, port, "discardTip", f"discard{index}", waste="waste")
    state = host.values()["/workcell"]
    snapshot = state["materialSnapshot"]
    by_id = {row["id"]: row for row in snapshot["vessels"]}
    for index in range(1, 7):
        assert by_id[f"M{index}"]["volumeUl"] == pytest.approx(0)
        assert by_id[f"sample{index}"]["cells"][0]["suspendedLive"] == pytest.approx(20)
        for daughter in ("A", "B"):
            row = by_id[f"{daughter}{index}"]
            assert row["parent"] == f"M{index}"
            assert row["cells"][0]["culture"] == f"M{index}"
            assert row["cells"][0]["suspendedLive"] == pytest.approx(490)
            assert row["cells"][0]["suspendedDead"] == pytest.approx(49)
    assert state["auditPassed"] is True
    assert snapshot["tip"]["volumeUl"] == 0
    assert all(tip["state"] == "discarded" for tip in snapshot["tips"])


def test_old_media_keeps_attached_cells_and_trypsin_wait_is_required_for_detachment(tmp_path):
    host, port = workcell(
        tmp_path,
        vessels=[
            {
                "id": "mother",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 500,
                "culture": "M1",
                "live": 1000,
                "attached": True,
            },
            {
                "id": "trypsin",
                "entityKey": "/reagent",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 500,
                "reagent": "trypsin",
            },
            {"id": "waste", "entityKey": "/waste", "site": "/sourceSite", "capacityUl": 2000, "waste": True},
        ],
        config={
            "wasteSite": "/sourceSite",
            "tips": [
                {"id": "T1", "site": "/T1", "bodyKey": "/tip1"},
                {"id": "T2", "site": "/T1", "bodyKey": "/tip2"},
            ],
        },
    )
    for resource in ("/source", "/reagent", "/waste"):
        execute(host, port, "reserve", resource, resource=resource, device="/workcell")
    execute(host, port, "pickupTip", "pick-old", tip="T1")
    execute(host, port, "aspirate", "old-media", vessel="mother", volumeUl=500)
    execute(host, port, "dispense", "old-waste", vessel="waste", volumeUl=500)
    state = host.values()["/workcell"]["materialSnapshot"]
    assert state["vessels"][0]["cells"][0]["attachedLive"] == 1000
    assert state["vessels"][2]["cells"][0]["suspendedLive"] == 0
    execute(host, port, "discardTip", "discard-old", waste="waste")
    execute(host, port, "pickupTip", "pick-trypsin", tip="T2")
    execute(host, port, "aspirate", "get-trypsin", vessel="trypsin", volumeUl=200)
    execute(host, port, "dispense", "add-trypsin", vessel="mother", volumeUl=200)
    assert invoke(host, "detach", "premature-detach", vessel="mother").error.code == "exposure_incomplete"
    host.tick(host.sim_time + 2, 2)
    execute(host, port, "detach", "detach", vessel="mother")
    cells = host.values()["/workcell"]["materialSnapshot"]["vessels"][0]["cells"][0]
    assert (cells["attachedLive"], cells["suspendedLive"]) == (0, 1000)


def test_loss_of_alignment_during_executed_pump_progress_is_not_attributed_to_vessel(tmp_path):
    host, port = workcell(tmp_path)
    execute(host, port, "reserve", "res", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "asp", vessel="source", volumeUl=100)
    host.tick(host.sim_time + 0.1, 0.1)
    port.positions["/sourceSite"] = [1, 0, 0]
    port.advance(0.0004)
    host.tick(host.sim_time + 0.1, 0.1)
    state = host.values()["/workcell"]
    assert state["auditPassed"] is False
    assert state["materialSnapshot"]["reconciled"] is False
    assert state["materialSnapshot"]["vessels"][0]["volumeUl"] == 500


@pytest.mark.parametrize(
    "amount,capacity,code", [(501, 1000, "insufficient_liquid"), (100, 50, "capacity_exceeded")]
)
def test_liquid_admission_preserves_state_when_stock_or_tip_capacity_is_insufficient(
    tmp_path, amount, capacity, code
):
    host, port = workcell(tmp_path, config={"tipCapacityUl": capacity})
    execute(host, port, "reserve", "res", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    before = host.values()["/workcell"]["materialSnapshot"]
    rejected = invoke(host, "aspirate", "overflow", vessel="source", volumeUl=amount)
    assert rejected.error.code == code
    assert host.values()["/workcell"]["materialSnapshot"] == before


def test_sample_contact_on_dispense_blocks_return_to_common_reagent_and_wrong_daughter(tmp_path):
    host, port = workcell(
        tmp_path,
        vessels=[
            {
                "id": "medium",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 500,
                "reagent": "medium",
            },
            {
                "id": "mother",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "culture": "M1",
                "live": 1000,
                "attached": True,
            },
            {
                "id": "wrong-child",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "culture": "D2",
                "parent": "M2",
            },
        ],
    )
    execute(host, port, "reserve", "res", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(host, port, "aspirate", "get-medium", vessel="medium", volumeUl=100)
    execute(host, port, "dispense", "add-medium", vessel="mother", volumeUl=50)
    assert (
        invoke(host, "aspirate", "dirty-reagent", vessel="medium", volumeUl=20).error.code == "cross_sample"
    )
    assert (
        invoke(host, "dispense", "wrong-lineage", vessel="wrong-child", volumeUl=20).error.code
        == "cross_sample"
    )


@pytest.mark.parametrize(
    "facts,config,code",
    [
        ({"lidClosed": True}, {"lidProperty": "lidClosed"}, "closed_vessel"),
        ({"station": "/wrong"}, {"requiredStation": "/flex"}, "wrong_station"),
    ],
)
def test_liquid_actions_require_actual_open_vessel_and_settled_station(tmp_path, facts, config, code):
    host, port = workcell(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/peer",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 500,
                **config,
            }
        ],
        facts=facts,
    )
    execute(host, port, "reserve", "res", resource="/peer", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    assert invoke(host, "aspirate", "asp", vessel="source", volumeUl=100).error.code == code


def test_reservation_conflict_and_wrong_device_are_checked_before_motion(tmp_path):
    host, port = workcell(tmp_path, facts={}, config={"managedDevices": ["/peer"]})
    execute(host, port, "reserve", "robot-reservation", resource="/source", device="/peer")
    assert (
        invoke(host, "reserve", "conflict", resource="/source", device="/workcell").error.code
        == "resource_busy"
    )
    execute(host, port, "pickupTip", "pick", tip="T1")
    assert invoke(host, "aspirate", "asp", vessel="source", volumeUl=100).error.code == "reservation_required"


def test_accepted_old_generation_cannot_start_after_control_accept(tmp_path):
    host, port = workcell(tmp_path)
    assert invoke(host, "move", "queued", position=[0.2, 0.1, 0.05]).status == "accepted"
    assert invoke(host, "requestControl", "handoff", nextOwner="human", mode="manual").status == "accepted"
    host.tick(0.1, 0.1)
    queued = next(row for row in host.snapshot().invocations if row.invocationId == "queued")
    assert queued.status == "failed" and queued.error.code == "stale_control"
    assert port.targets["x"] == 0


def test_full_waste_does_not_discard_loaded_tip_or_hide_its_remaining_liquid(tmp_path):
    host, port = workcell(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 500,
            },
            {"id": "waste", "entityKey": "/waste", "site": "/sourceSite", "capacityUl": 20, "waste": True},
        ],
        config={"wasteSite": "/sourceSite"},
    )
    execute(host, port, "reserve", "res", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(host, port, "aspirate", "asp", vessel="source", volumeUl=40)
    assert invoke(host, "discardTip", "discard", waste="waste").error.code == "waste_full"
    assert host.values()["/workcell"]["inFlightUl"] == 40
    assert ("/pipette", "/tip1") in port.welds


def test_safe_standstill_does_not_release_a_resource_still_held_by_robot(tmp_path):
    host, port = workcell(tmp_path, facts={"holding": "/source"}, config={"managedDevices": ["/peer"]})
    execute(host, port, "reserve", "res", resource="/source", device="/peer")
    assert invoke(host, "release", "unsafe", resource="/source").error.code == "unsafe_release"


def test_cancel_accounts_braking_piston_motion_until_measured_standstill(tmp_path):
    host, port = workcell(tmp_path)
    execute(host, port, "reserve", "res", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "asp", vessel="source", volumeUl=100)
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    port.velocities["pump"] = 0.001
    host.tick(host.sim_time + 0.1, 0.1)
    host.cancel("asp")
    assert host.active_invocations("/workcell")[0].status == "running"
    port.q["pump"] = 0.00045
    host.tick(host.sim_time + 0.1, 0.1)
    assert host.values()["/workcell"]["inFlightUl"] == pytest.approx(45)
    port.q["pump"], port.velocities["pump"] = 0.0004, 0
    host.tick(host.sim_time + 0.1, 0.1)
    state = host.values()["/workcell"]
    assert state["inFlightUl"] == pytest.approx(40)
    assert state["materialSnapshot"]["vessels"][0]["volumeUl"] == pytest.approx(460)
    assert state["auditPassed"] is True
    assert not host.active_invocations("/workcell")

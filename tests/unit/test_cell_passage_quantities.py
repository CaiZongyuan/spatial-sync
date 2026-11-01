"""Resolved quantity intents, real-valued inventory and native float32 targets."""

import numpy as np
import pytest

from packages.protocol.schema import InvokeRequest
from tests.fixtures.cell_passage import build_workcell_host
from tests.unit.test_cell_passage import execute, invoke
from tests.unit.test_cell_passage_journal import set_ready


def test_literal_float32_shortfall_can_be_dispensed_without_inventing_liquid(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
                "culture": "M1",
                "live": 600000,
            },
            {
                "id": "daughter",
                "entityKey": "/daughter",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "culture": "D1",
                "parent": "M1",
            },
        ],
    )
    for key in ("/source", "/daughter"):
        execute(host, port, "reserve", key, resource=key, device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "literal", vessel="source", volumeUl=1000)
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(float(np.float32(0.01)))
    host.tick(host.sim_time + 0.1, 0.1)
    before = host.values()["/workcell"]["materialSnapshot"]
    assert before["tip"]["volumeUl"] == pytest.approx(999.999977648258, abs=1e-9)
    assert (
        invoke(host, "dispense", "too-much", vessel="daughter", volumeUl=1000).error.code
        == "insufficient_liquid"
    )
    execute(host, port, "dispenseAll", "available-tip", vessel="daughter")
    state = host.values()["/workcell"]["materialSnapshot"]
    receipt = state["operations"][-1]
    assert receipt["quantityKind"] == "allTip"
    assert receipt["requestedUl"] == before["tip"]["volumeUl"]
    assert receipt["resolvedUl"] <= receipt["requestedUl"]
    assert 0 <= state["tip"]["volumeUl"] <= 0.01
    assert state["vessels"][0]["volumeUl"] + state["tip"]["volumeUl"] + state["vessels"][1][
        "volumeUl"
    ] == pytest.approx(1000, abs=1e-9)
    cells = sum(c["suspendedLive"] for row in [*state["vessels"], state["tip"]] for c in row["cells"])
    assert cells == pytest.approx(600000, abs=1e-6)
    assert receipt["remainderLocation"] == "tip"


@pytest.mark.parametrize(
    "action,params,remaining,kind",
    [
        ("aspirateAvailable", {}, 0, "available"),
        ("aspirateAvailable", {"reserveUl": 50}, 50, "available"),
        ("aspirateFraction", {"fraction": 0.5}, 500, "fraction"),
    ],
)
def test_source_intent_resolves_once_and_keeps_bounded_residue_at_source(
    tmp_path, action, params, remaining, kind
):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
                "culture": "M1",
                "live": 600000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(host, port, action, "intent", vessel="source", **params)
    state = host.values()["/workcell"]["materialSnapshot"]
    row, receipt = state["vessels"][0], state["operations"][-1]
    assert remaining <= row["volumeUl"] <= remaining + 0.01
    assert row["volumeUl"] + state["tip"]["volumeUl"] == pytest.approx(1000, abs=1e-9)
    assert receipt["quantityKind"] == kind and receipt["requestedUl"] == 1000 - remaining
    assert 0 <= receipt["resolutionResidualUl"] <= 0.01
    assert receipt["remainderLocation"] == "source"
    assert receipt["pumpTargetM"] == float(np.float32(receipt["pumpTargetM"]))
    assert (
        invoke(host, action, "new-request", operationId="intent", vessel="source", **params).error.code
        == "operation_applied"
    )


def test_top_up_resolves_actual_deficit_before_aspirating_reagent(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "medium",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 50000,
                "volumeUl": 25000,
                "reagent": "medium",
            },
            {
                "id": "target",
                "entityKey": "/target",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "volumeUl": 250,
                "culture": "M1",
                "live": 600000,
            },
        ],
    )
    for key in ("/source", "/target"):
        execute(host, port, "reserve", key, resource=key, device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(
        host, port, "aspirateTopUp", "supply", vessel="medium", targetVessel="target", targetVolumeUl=1000
    )
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["requestedUl"] == 750
    execute(host, port, "dispenseAll", "fill", vessel="target")
    state = host.values()["/workcell"]["materialSnapshot"]
    assert state["vessels"][1]["volumeUl"] == pytest.approx(1000, abs=0.01)
    assert sum(row["volumeUl"] for row in [*state["vessels"], state["tip"]]) == pytest.approx(25250, abs=1e-8)
    assert state["vessels"][1]["cells"][0]["suspendedLive"] == 600000


def test_below_resolution_intent_is_explicit_without_issuing_a_pump_target(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {"id": "source", "entityKey": "/source", "site": "/sourceSite", "capacityUl": 1, "volumeUl": 1e-8}
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(host, port, "aspirateAvailable", "tiny", vessel="source")
    state = host.values()["/workcell"]["materialSnapshot"]
    receipt = state["operations"][-1]
    assert (receipt["resolvedUl"], receipt["appliedUl"], port.targets["pump"]) == (0, 0, 0)
    assert receipt["quantityOutcome"] == "below_resolution"
    assert receipt["noTransfer"] is True
    assert receipt["untransferredUl"] == 1e-8 and receipt["remainderLocation"] == "source"
    assert state["vessels"][0]["volumeUl"] == 1e-8 and state["tip"]["volumeUl"] == 0


@pytest.mark.parametrize(
    "limits,code",
    [
        (([0], [0.005]), "pump_capacity"),
        (([0], [np.inf]), "pump_limits_unavailable"),
        (([np.nan], [0.01]), "pump_limits_unavailable"),
        (([], []), "pump_limits_unavailable"),
    ],
)
def test_intents_require_known_finite_scalar_limits_and_sufficient_travel(tmp_path, limits, code):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    port.limits["pump"] = tuple(np.asarray(part, dtype=np.float32) for part in limits)
    before = host.values()["/workcell"]["materialSnapshot"]
    result = invoke(host, "aspirateAvailable", "invalid-limits", vessel="source")
    assert result.error.code == code
    assert host.values()["/workcell"]["materialSnapshot"] == before and port.targets["pump"] == 0


def test_available_does_not_silently_reduce_a_large_capacity_shortfall(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "volumeUl": 2000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    assert invoke(host, "aspirateAvailable", "too-large", vessel="source").error.code == "capacity_exceeded"
    assert host.values()["/workcell"]["materialSnapshot"]["vessels"][0]["volumeUl"] == 2000


def test_top_up_binds_target_resource_and_does_not_invent_missing_reagent(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "medium",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 100,
            },
            {"id": "target", "entityKey": "/target", "site": "/sourceSite", "capacityUl": 2000},
        ],
    )
    execute(host, port, "reserve", "source-reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    assert (
        invoke(
            host,
            "aspirateTopUp",
            "no-target-lease",
            vessel="medium",
            targetVessel="target",
            targetVolumeUl=1000,
        ).error.code
        == "reservation_required"
    )
    execute(host, port, "reserve", "target-reserve", resource="/target", device="/workcell")
    before = host.values()["/workcell"]["materialSnapshot"]
    assert (
        invoke(
            host, "aspirateTopUp", "no-stock", vessel="medium", targetVessel="target", targetVolumeUl=1000
        ).error.code
        == "insufficient_liquid"
    )
    assert host.values()["/workcell"]["materialSnapshot"] == before


def test_partial_fraction_keeps_original_resolved_remaining_instead_of_recomputing_half(tmp_path):
    host, port = build_workcell_host(tmp_path)
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    assert invoke(host, "aspirateFraction", "half", vessel="source", fraction=0.5).status == "accepted"
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    host.tick(host.sim_time + 0.1, 0.1)
    host.cancel("half")
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    host.drain_events()
    snapshot = host.values()["/workcell"]["materialSnapshot"]
    receipt = snapshot["operations"][-1]
    assert receipt["requestedUl"] == 250 and receipt["appliedUl"] == pytest.approx(40)
    assert receipt["remainingUl"] == pytest.approx(receipt["resolvedUl"] - 40)
    assert abs(receipt["remainingUl"] - snapshot["vessels"][0]["volumeUl"] * 0.5) > 19
    assert (
        invoke(
            host, "aspirateFraction", "retry", operationId="half", vessel="source", fraction=0.5
        ).error.code
        == "operation_applied"
    )
    execute(host, port, "aspirate", "reviewed-remainder", vessel="source", volumeUl=receipt["remainingUl"])
    assert host.values()["/workcell"]["inFlightUl"] == pytest.approx(receipt["resolvedUl"])


def test_quantity_intent_does_not_make_overtravel_a_successful_clamp(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 100,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateAvailable", "all", vessel="source")
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    host.tick(host.sim_time + 0.1, 0.1)
    port.q["pump"] = 0.0015
    host.tick(host.sim_time + 0.1, 0.1)
    state = host.values()["/workcell"]
    assert state["materialSnapshot"]["reconciled"] is False and state["auditPassed"] is False
    receipt = state["materialSnapshot"]["operations"][-1]
    assert receipt["fault"] == "pump_feedback_invalid" and receipt["observedUl"] == pytest.approx(150)
    assert receipt["appliedUl"] == pytest.approx(40) and receipt["status"] == "failed"


def execute_f32(host, port, action, token, **params):
    result = invoke(host, action, token, **params)
    assert result.status == "accepted", result
    for _ in range(4):
        port.advance(float(np.float32(port.targets["pump"])))
        host.tick(host.sim_time + 0.1, 0.1)
        if not host.active_invocations("/workcell"):
            terminal = next(row for row in host.snapshot().invocations if row.invocationId == token)
            assert terminal.status == "succeeded", terminal
            return terminal.result["receipt"]
    raise AssertionError("External float32 actuator did not finish")


def test_six_samples_and_twelve_daughters_conserve_actual_fractional_cells_and_waste(tmp_path):
    vessels = [
        {
            "id": "medium",
            "entityKey": "/medium",
            "site": "/sourceSite",
            "capacityUl": 50000,
            "volumeUl": 20000,
            "reagent": "medium",
        },
        {"id": "waste", "entityKey": "/waste", "site": "/sourceSite", "capacityUl": 50000, "waste": True},
    ]
    for index in range(1, 7):
        vessels.append(
            {
                "id": f"S{index}",
                "entityKey": f"/S{index}",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
                "culture": f"M{index}",
                "live": 600000,
                "dead": 30000,
            }
        )
        for role in ("sample", "A", "B"):
            vessels.append(
                {
                    "id": f"{role}{index}",
                    "entityKey": "/" + role,
                    "site": "/sourceSite",
                    "capacityUl": 3000,
                    "culture": f"{role}{index}",
                    "parent": f"M{index}",
                }
            )
    host, port = build_workcell_host(
        tmp_path,
        vessels=vessels,
        config={
            "wasteSite": "/sourceSite",
            "tips": [{"id": f"T{index}", "site": "/T1", "bodyKey": f"/tip{index}"} for index in range(1, 31)],
        },
    )
    for resource in sorted({row["entityKey"] for row in vessels}):
        execute_f32(host, port, "reserve", resource, resource=resource, device="/workcell")
    tip = 0

    def transfer(source, target, action, **params):
        nonlocal tip
        tip += 1
        prefix = f"operation-{tip}"
        execute_f32(host, port, "pickupTip", prefix + "-pick", tip=f"T{tip}")
        execute_f32(host, port, action, prefix + "-asp", vessel=source, **params)
        execute_f32(host, port, "dispenseAll", prefix + "-disp", vessel=target)
        execute_f32(host, port, "discardTip", prefix + "-discard", waste="waste")

    for index in range(1, 7):
        transfer(f"S{index}", f"sample{index}", "aspirate", volumeUl=20)
        transfer(f"S{index}", f"A{index}", "aspirateFraction", fraction=0.5)
        transfer(f"S{index}", f"B{index}", "aspirateAvailable")
        for role in ("A", "B"):
            transfer(
                "medium",
                f"{role}{index}",
                "aspirateTopUp",
                targetVessel=f"{role}{index}",
                targetVolumeUl=1000,
            )
    state = host.values()["/workcell"]["materialSnapshot"]
    by_id = {row["id"]: row for row in state["vessels"]}
    for index in range(1, 7):
        daughters = [by_id[f"{role}{index}"] for role in ("A", "B")]
        assert all(row["volumeUl"] == pytest.approx(1000, abs=0.01) for row in daughters)
        live = [sum(cell["suspendedLive"] for cell in row["cells"]) for row in daughters]
        assert abs(live[0] - live[1]) <= 1
        assert all(
            row["parent"] == f"M{index}" and {c["culture"] for c in row["cells"]} == {f"M{index}"}
            for row in daughters
        )
        origin_total = sum(
            sum(cell[key] for key in ("attachedLive", "suspendedLive", "pelletLive"))
            for row in [*state["vessels"], state["tip"]]
            for cell in row["cells"]
            if cell["culture"] == f"M{index}"
        )
        assert abs(origin_total - 600000) <= 1e-6
    assert sum(row["volumeUl"] for row in [*state["vessels"], state["tip"]]) == pytest.approx(26000, abs=1e-8)
    assert sum(c["suspendedLive"] for c in by_id["waste"]["cells"]) > 0
    assert state["tip"]["volumeUl"] == 0 and state["tip"]["id"] == ""
    assert host.values()["/workcell"]["auditPassed"] is True


def test_positive_tiny_resolved_target_cannot_finish_before_any_observed_motion(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1,
                "volumeUl": 0.00014,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateAvailable", "tiny-positive", vessel="source")
    host.tick(host.sim_time + 0.1, 0.1)
    assert host.active_invocations("/workcell")[0].status == "running"
    assert host.values()["/workcell"]["lastResolvedUl"] > 0 and host.values()["/workcell"]["inFlightUl"] == 0
    pending = host.values()["/workcell"]["materialSnapshot"]["operations"][-1]
    assert pending["resolvedUl"] < pending["resolutionUl"]
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["noTransfer"] is False
    port.advance(port.targets["pump"])
    host.tick(host.sim_time + 0.1, 0.1)
    assert host.values()["/workcell"]["inFlightUl"] > 0


def test_top_up_accounts_existing_clean_tip_liquid_and_already_satisfied_goal(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "medium",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
            },
            {
                "id": "target",
                "entityKey": "/target",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "volumeUl": 200,
            },
        ],
    )
    for key in ("/source", "/target"):
        execute(host, port, "reserve", key, resource=key, device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(host, port, "aspirate", "preloaded", vessel="medium", volumeUl=100)
    execute(
        host, port, "aspirateTopUp", "supply", vessel="medium", targetVessel="target", targetVolumeUl=1000
    )
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["requestedUl"] == 700
    execute(host, port, "dispenseAll", "fill", vessel="target")
    goal = host.values()["/workcell"]["materialSnapshot"]["vessels"][1]["volumeUl"]
    assert goal == pytest.approx(1000, abs=0.01)
    # A goal equal to the actual current volume needs no new liquid. Use an
    # empty fresh tip rather than bypassing contact or loaded-tip checks.
    directory = tmp_path / "at-goal"
    directory.mkdir()
    host2, port2 = build_workcell_host(
        directory,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
            },
            {
                "id": "target",
                "entityKey": "/target",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
            },
        ],
    )
    for key in ("/source", "/target"):
        execute(host2, port2, "reserve", key, resource=key, device="/workcell")
    execute(host2, port2, "pickupTip", "pick", tip="T1")
    execute(
        host2,
        port2,
        "aspirateTopUp",
        "already-filled",
        vessel="source",
        targetVessel="target",
        targetVolumeUl=1000,
    )
    receipt = host2.values()["/workcell"]["materialSnapshot"]["operations"][-1]
    assert receipt["quantityOutcome"] == "at_target"
    assert receipt["noTransfer"] is True
    assert receipt["resolvedUl"] == receipt["appliedUl"] == 0 and port2.targets["pump"] == 0


def test_top_up_target_lid_loss_uses_deferred_failure_and_original_quantity(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        facts={},
        vessels=[
            {
                "id": "medium",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
            },
            {
                "id": "target",
                "entityKey": "/peer",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "lidProperty": "lidClosed",
                "requiredStation": "/imager",
            },
        ],
    )
    for key in ("/source", "/peer"):
        execute(host, port, "reserve", key, resource=key, device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateTopUp", "topup", vessel="medium", targetVessel="target", targetVolumeUl=1000)
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    port.velocities["pump"] = 0.001
    assert (
        host.invoke(
            InvokeRequest(
                invocationId="lid-close",
                requestId="lid-close",
                entityKey="/peer",
                action="setLid",
                epoch=0,
                createdSimTime=host.sim_time,
                createdAtMs=0,
                params={"closed": True},
            )
        ).status
        == "accepted"
    )
    host.tick(host.sim_time + 0.1, 0.1)
    assert host.values()["/workcell"]["phase"] == "holding"
    assert host.active_invocations("/workcell")[0].status == "running"
    port.q["pump"] = 0.0004
    port.velocities["pump"] = 0
    host.tick(host.sim_time + 0.1, 0.1)
    receipt = host.values()["/workcell"]["materialSnapshot"]["operations"][-1]
    assert receipt["status"] == "failed" and receipt["fault"] == "closed_vessel"
    assert receipt["requestedUl"] == 1000 and receipt["appliedUl"] == pytest.approx(40)
    assert receipt["remainingUl"] == pytest.approx(receipt["resolvedUl"] - 40)


def test_zero_transfer_at_a_culture_preserves_contact_isolation(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "dry-culture",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "culture": "M1",
                "live": 100,
                "attached": True,
            },
            {
                "id": "stock",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 1000,
                "volumeUl": 100,
            },
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(host, port, "aspirateAvailable", "empty-culture", vessel="dry-culture")
    assert host.values()["/workcell"]["materialSnapshot"]["tip"]["contactCulture"] == "M1"
    assert invoke(host, "aspirateAvailable", "dirty-stock", vessel="stock").error.code == "cross_sample"


def test_observed_native_plateau_establishes_hold_before_success_without_relaxing_velocity_gate(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "volumeUl": 2000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateFraction", "half", vessel="source", fraction=0.5)
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    # Independent, frozen c1e native probe measurements, not a nominal target.
    observed_q = 0.009999988600611687
    port.q["pump"], port.velocities["pump"] = observed_q, 1.0244548320770264e-7
    for _ in range(5):
        host.tick(host.sim_time + 1 / 60, 1 / 60)
    assert port.targets["pump"] == observed_q
    assert host.values()["/workcell"]["controlSafe"] is False
    assert host.active_invocations("/workcell")[0].status == "running"
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["status"] == "settling"
    port.velocities["pump"] = 0
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    terminal = next(row for row in host.snapshot().invocations if row.invocationId == "half")
    assert terminal.status == "succeeded"
    receipt = terminal.result["receipt"]
    assert receipt["appliedUl"] == pytest.approx(999.9988600611687, abs=1e-9)
    assert receipt["untransferredUl"] == pytest.approx(0.0011399388313, abs=1e-9)
    assert host.values()["/workcell"]["materialSnapshot"]["vessels"][0]["volumeUl"] == pytest.approx(
        1000.0011399388313
    )


@pytest.mark.parametrize("reason", ["cancel", "control", "cabinet", "timeout", "large_velocity"])
def test_normal_completion_settling_still_requires_safe_velocity_and_live_conditions(tmp_path, reason):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "volumeUl": 2000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateFraction", "half", vessel="source", fraction=0.5)
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    port.q["pump"] = 0.009999988600611687
    port.velocities["pump"] = 0.5 if reason == "large_velocity" else 1.0244548320770264e-7
    for _ in range(5):
        host.tick(host.sim_time + 1 / 60, 1 / 60)
    assert host.values()["/workcell"]["controlSafe"] is False
    assert host.active_invocations("/workcell")[0].status == "running"
    if reason == "large_velocity":
        return
    if reason == "cancel":
        host.cancel("half")
    elif reason == "control":
        assert (
            invoke(host, "requestControl", "handoff", nextOwner="human", mode="manual").status == "accepted"
        )
    elif reason == "timeout":
        host.tick(host.sim_time + 3601, 3601)
    else:
        set_ready(host, False, "cabinet-off")
        host.tick(host.sim_time + 1 / 60, 1 / 60)
        assert (
            host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["fault"] == "cabinet_not_ready"
        )
    port.velocities["pump"] = 0
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    final = next(row for row in host.snapshot().invocations if row.invocationId == "half")
    assert final.status == ("cancelled" if reason in ("cancel", "control") else "failed")
    if reason == "cabinet":
        assert final.error.code == "cabinet_not_ready"
    elif reason == "timeout":
        assert final.error.code == "timeout"


def test_repeated_reads_and_paused_clock_do_not_count_as_distinct_stable_physics_samples(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "volumeUl": 2000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateFraction", "half", vessel="source", fraction=0.5)
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    requested_target = port.targets["pump"]
    port.q["pump"], port.velocities["pump"] = 0.009999988600611687, 1.0244548320770264e-7
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    for _ in range(10):
        host.values()
        host.tick(host.sim_time, 1 / 60)
    assert port.targets["pump"] == requested_target
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["status"] == "running"
    for _ in range(3):
        host.tick(host.sim_time + 1 / 60, 1 / 60)
    assert port.targets["pump"] == port.q["pump"]
    assert host.active_invocations("/workcell")[0].status == "running"


def test_normal_completion_rechecks_total_remainder_after_actual_braking(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 3000,
                "volumeUl": 2000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateFraction", "half", vessel="source", fraction=0.5)
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    port.q["pump"], port.velocities["pump"] = 0.009999905, 1.1e-7
    for _ in range(5):
        host.tick(host.sim_time + 1 / 60, 1 / 60)
    assert host.active_invocations("/workcell")[0].status == "running"
    port.q["pump"], port.velocities["pump"] = 0.009999895, 0
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    final = next(row for row in host.snapshot().invocations if row.invocationId == "half")
    assert final.status == "failed" and final.error.code == "quantity_unreachable"
    receipt = final.result or host.values()["/workcell"]["materialSnapshot"]["operations"][-1]
    assert receipt["untransferredUl"] > 0.01
    assert host.values()["/workcell"]["auditPassed"] is True


def test_literal_mix_first_completed_stroke_is_not_the_completed_mix(tmp_path):
    host, port = build_workcell_host(
        tmp_path,
        vessels=[
            {
                "id": "source",
                "entityKey": "/source",
                "site": "/sourceSite",
                "capacityUl": 2000,
                "volumeUl": 1000,
                "culture": "M1",
                "live": 600000,
            }
        ],
    )
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "mix", "mix", vessel="source", volumeUl=900)
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    port.advance(float(np.float32(0.009)))
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    assert host.active_invocations("/workcell")[0].status == "running"
    assert port.targets["pump"] == 0
    assert host.values()["/workcell"]["inFlightUl"] > 899
    port.advance(0)
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    final = next(row for row in host.snapshot().invocations if row.invocationId == "mix")
    assert final.status == "succeeded"
    assert host.values()["/workcell"]["inFlightUl"] == 0
    assert host.values()["/workcell"]["materialSnapshot"]["vessels"][0]["volumeUl"] == pytest.approx(1000)


def test_zero_progress_timeout_receipt_cannot_be_cancelled_before_core_marks_it_failed(tmp_path):
    host, port = build_workcell_host(tmp_path)
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirateAvailable", "timeout", vessel="source")
    host.tick(host.sim_time + 1 / 60, 1 / 60)
    messages = host.tick(host.sim_time + 3601, 3601)
    messages.extend(host.tick(host.sim_time + 1 / 60, 1 / 60))
    terminal = next(
        row for row in messages if row.type == "invocation.update" and row.invocationId == "timeout"
    )
    receipts = [
        row.data["receipt"]
        for row in messages
        if row.type == "entity.event"
        and row.event == "operationCompleted"
        and row.data["receipt"]["operationId"] == "timeout"
    ]
    assert terminal.status == "failed" and terminal.error.code == "timeout"
    assert len(receipts) == 1 and receipts[0]["status"] == "failed" and receipts[0]["fault"] == "timeout"
    assert receipts[0]["appliedUl"] == 0 and receipts[0]["noTransfer"] is False

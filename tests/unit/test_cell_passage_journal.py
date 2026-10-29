"""Cabinet interlock and retained operation evidence through the public Host seam."""

import json

import pytest
from pydantic import TypeAdapter

from packages.info.log import SessionLog, read_segment
from packages.info.store import StateStore
from packages.protocol.schema import InvokeRequest, ServerMessage, TransformFrame
from packages.transport import FrameCodec
from tests.fixtures.cell_passage import build_workcell_host
from tests.unit.test_cell_passage import execute, invoke


def set_ready(host, ready, token):
    result = host.invoke(
        InvokeRequest(
            invocationId=token,
            requestId=token,
            entityKey="/cabinet",
            action="setReady",
            epoch=0,
            createdSimTime=host.sim_time,
            createdAtMs=0,
            params={"ready": ready},
        )
    )
    assert result.status == "accepted"


@pytest.mark.parametrize("ready", [False, None])
def test_liquid_admission_requires_confirmed_cabinet_readiness(tmp_path, ready):
    host, _ = build_workcell_host(tmp_path, cabinet_ready=ready)
    before = host.values()["/workcell"]["materialSnapshot"]
    result = invoke(host, "aspirate", "asp", vessel="source", volumeUl=50)
    assert result.error.code == "cabinet_not_ready"
    assert host.values()["/workcell"]["materialSnapshot"] == before


def test_cabinet_loss_before_start_rejects_without_material_changes(tmp_path):
    host, port = build_workcell_host(tmp_path)
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "asp", vessel="source", volumeUl=100)
    set_ready(host, False, "cabinet-off")
    host.tick(host.sim_time + 0.1, 0.1)
    terminal = next(row for row in host.snapshot().invocations if row.invocationId == "asp")
    assert terminal.status == "failed" and terminal.error.code == "cabinet_not_ready"
    assert host.values()["/workcell"]["inFlightUl"] == 0


@pytest.mark.parametrize("ready", [False, None])
def test_cabinet_loss_accounts_braking_then_fails_with_partial_liquid_and_tip_retained(tmp_path, ready):
    host, port = build_workcell_host(tmp_path)
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "asp", vessel="source", volumeUl=100)
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    port.velocities["pump"] = 0.001
    set_ready(host, ready, "cabinet-off")
    host.tick(host.sim_time + 0.1, 0.1)
    state = host.values()["/workcell"]
    assert state["inFlightUl"] == pytest.approx(40)
    assert state["phase"] == "holding" and state["controlSafe"] is False
    assert host.active_invocations("/workcell")[0].status == "running"
    port.q["pump"] = 0.00045
    host.tick(host.sim_time + 0.1, 0.1)
    assert host.values()["/workcell"]["inFlightUl"] == pytest.approx(45)
    port.q["pump"], port.velocities["pump"] = 0.0004, 0
    host.tick(host.sim_time + 0.1, 0.1)
    terminal = next(row for row in host.snapshot().invocations if row.invocationId == "asp")
    assert terminal.status == "failed" and terminal.error.code == "cabinet_not_ready"
    state = host.values()["/workcell"]
    assert state["materialSnapshot"]["vessels"][0]["volumeUl"] == pytest.approx(460)
    assert state["materialSnapshot"]["tip"]["id"] == "T1" and ("/pipette", "/tip1") in port.welds
    assert state["inFlightUl"] == pytest.approx(40) and state["auditPassed"] is True


def wait_history(host, count, prefix="wait"):
    messages = []
    for index in range(count):
        result = invoke(host, "wait", f"{prefix}-{index}", seconds=0)
        assert result.status == "accepted"
        messages.extend(host.tick(host.sim_time + 0.1, 0.1))
    return messages


def test_recent_snapshot_is_bounded_but_old_domain_ids_and_complete_receipts_survive(tmp_path):
    host, _ = build_workcell_host(tmp_path)
    messages = wait_history(host, 90)
    state = host.values()["/workcell"]["materialSnapshot"]
    assert len(state["operations"]) == 64
    assert (state["operationCount"], state["terminalOperationCount"], state["archivedOperationCount"]) == (
        90,
        90,
        26,
    )
    assert state["operations"][0]["operationId"] == "wait-26"
    receipts = [
        row.data["receipt"]
        for row in messages
        if row.type == "entity.event" and row.event == "operationCompleted"
    ]
    terminal = [row for row in messages if row.type == "invocation.update" and row.status == "succeeded"]
    assert [row["operationId"] for row in receipts] == [f"wait-{index}" for index in range(90)]
    assert len(terminal) == len(receipts) == 90
    for result, receipt in zip(terminal, receipts, strict=True):
        assert receipt["invocationId"] == result.invocationId
        assert receipt["status"] == "succeeded" and receipt["appliedUl"] == 0
        assert json.loads(receipt["paramsJson"]) == result.params
        assert result.result["receipt"] == receipt
        assert 0 <= receipt["startedSimTime"] <= receipt["endedSimTime"]
    assert (
        invoke(host, "wait", "old-id-new-request", operationId="wait-0", seconds=0).error.code
        == "operation_applied"
    )
    assert host.values()["/workcell"]["materialSnapshot"] == state
    assert len(json.dumps(state, allow_nan=False).encode()) < 60000


def test_partial_settling_and_control_receipts_are_not_lost_when_ordinary_rows_are_archived(tmp_path):
    host, port = build_workcell_host(tmp_path)
    wait_history(host, 70)
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "partial", vessel="source", volumeUl=100)
    host.tick(host.sim_time + 0.1, 0.1)
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["status"] == "running"
    port.advance(0.0004)
    port.velocities["pump"] = 0.001
    host.tick(host.sim_time + 0.1, 0.1)
    host.cancel("partial")
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["status"] == "settling"
    assert invoke(host, "wait", "unsafe", seconds=0).error.code == "busy"
    port.velocities["pump"] = 0
    host.tick(host.sim_time + 0.1, 0.1)
    wait_history(host, 70, "after")
    snapshot = host.values()["/workcell"]["materialSnapshot"]
    assert len(snapshot["operations"]) == 65
    partial = next(row for row in snapshot["operations"] if row["operationId"] == "partial")
    assert partial["status"] == "cancelled" and partial["appliedUl"] == pytest.approx(40)
    assert host.values()["/workcell"]["auditPassed"] and host.values()["/workcell"][
        "inFlightUl"
    ] == pytest.approx(40)
    count = snapshot["operationCount"]
    invoke(host, "requestControl", "handoff", nextOwner="human", mode="manual")
    host.tick(host.sim_time + 0.1, 0.1)
    invoke(host, "confirmControl", "confirm", epoch=1)
    host.tick(host.sim_time + 0.1, 0.1)
    assert (
        invoke(
            host, "requestControl", "new-request", operationId="handoff", nextOwner="human", mode="manual"
        ).status
        == "accepted"
    )
    host.tick(host.sim_time + 0.1, 0.1)
    values = host.values()["/workcell"]
    assert (values["controlOwner"], values["controlEpoch"], values["controlMode"]) == ("human", 1, "manual")
    assert values["materialSnapshot"]["operationCount"] == count


async def test_recording_replay_retains_every_receipt_after_live_history_compaction(tmp_path):
    host, port = build_workcell_host(tmp_path)
    initial = host.snapshot()
    log = SessionLog(
        tmp_path / "recording",
        "test",
        "test",
        scene_version="test@1",
        scene_content_hash="a" * 64,
        kind="session",
    )
    try:
        frame = FrameCodec("test", "r", 1).encode(
            TransformFrame(
                type="stream.snapshot",
                sessionId="test",
                sceneRevision="r",
                epoch=0,
                seq=1,
                simTime=0,
                sampleTimeMs=initial.sampleTimeMs,
                poses=[],
            )
        )
        assert log.add_revision(
            {"sceneRevision": "r", "revisionNumericId": 1},
            host.catalog.model_dump(),
            frame,
            initial.model_dump_json(),
        )
        messages = []

        def record(batch):
            messages.extend(batch)
            for message in batch:
                assert log.append_json(
                    message.model_dump_json(),
                    getattr(message, "sampleTimeMs", initial.sampleTimeMs),
                    invocation_id=getattr(message, "invocationId", None),
                )

        def tick():
            record(host.tick(host.sim_time + 0.1, 0.1))

        record(wait_history(host, 90))
        for action, token, params in [
            ("reserve", "reserve", {"resource": "/source", "device": "/workcell"}),
            ("pickupTip", "pick", {"tip": "T1"}),
        ]:
            assert invoke(host, action, token, **params).status == "accepted"
            tick()
        invoke(host, "aspirate", "cancelled-transfer", vessel="source", volumeUl=100)
        tick()
        port.advance(0.0004)
        tick()
        host.cancel("cancelled-transfer")
        record(host.drain_events())
        tick()
        invoke(host, "aspirate", "failed-transfer", vessel="source", volumeUl=100)
        tick()
        port.advance(0.0008)
        set_ready(host, False, "cabinet-off")
        tick()
        record(wait_history(host, 70, "after"))
    finally:
        await log.close()
    assert log.error is None
    adapter = TypeAdapter(ServerMessage)
    replay = [
        adapter.validate_json(row.payload)
        for segment in sorted(log.directory.glob("seg-*.bin"))
        for row in read_segment(segment.read_bytes()).records
        if row.kind == 2
    ]
    receipts = [
        row.data["receipt"]
        for row in replay
        if row.type == "entity.event" and row.event == "operationCompleted"
    ]
    terminal = [
        row
        for row in replay
        if row.type == "invocation.update"
        and row.entityKey == "/workcell"
        and row.status in ("succeeded", "failed", "cancelled")
    ]
    assert len(receipts) == len(terminal) == 164
    assert len({row["operationId"] for row in receipts}) == 164
    assert receipts[0]["operationId"] == "wait-0" and receipts[-1]["operationId"] == "after-69"
    for identity, status in [("cancelled-transfer", "cancelled"), ("failed-transfer", "failed")]:
        receipt = next(row for row in receipts if row["operationId"] == identity)
        invocation = next(row for row in terminal if row.invocationId == identity)
        assert receipt["status"] == invocation.status == status
        assert receipt["requestedUl"] == 100 and receipt["appliedUl"] == pytest.approx(40)
    store = StateStore("r")
    for message in replay:
        store.apply(message)
    snapshot = store.entities["/workcell"].values["materialSnapshot"]
    assert len(snapshot["operations"]) == 66  # 64 ordinary rows plus both partial outcomes.
    assert (
        snapshot["operationCount"],
        snapshot["terminalOperationCount"],
        snapshot["archivedOperationCount"],
    ) == (164, 164, 98)
    assert "wait-0" not in {row["operationId"] for row in snapshot["operations"]}
    assert snapshot["tip"]["volumeUl"] == pytest.approx(80)
    assert snapshot["vessels"][0]["volumeUl"] == pytest.approx(420)
    assert store.entities["/workcell"].values["auditPassed"] is True


def test_source_lifetime_ids_survive_reset_but_queued_unexecuted_work_is_not_counted(tmp_path):
    host, _ = build_workcell_host(tmp_path)
    invoke(host, "wait", "queued", seconds=0)
    host.cancel("queued")
    messages = host.tick(0.1, 0.1)
    snapshot = host.values()["/workcell"]["materialSnapshot"]
    assert (
        snapshot["operationCount"],
        snapshot["terminalOperationCount"],
        snapshot["archivedOperationCount"],
    ) == (0, 0, 0)
    assert not any(row.type == "entity.event" and row.event == "operationCompleted" for row in messages)
    wait_history(host, 70)
    host.reset(1)
    result = host.invoke(
        InvokeRequest(
            invocationId="new-request",
            requestId="new-request",
            entityKey="/workcell",
            action="wait",
            epoch=1,
            createdSimTime=0,
            createdAtMs=0,
            params={"owner": "auto", "epoch": 0, "operationId": "wait-0", "seconds": 0},
        )
    )
    assert result.error.code == "operation_applied"
    assert host.values()["/workcell"]["materialSnapshot"]["operationCount"] == 0


def test_timeout_receipt_keeps_failed_invocation_status_after_braking_accounting_finishes(tmp_path):
    host, port = build_workcell_host(tmp_path)
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "timeout", vessel="source", volumeUl=100)
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    port.velocities["pump"] = 0.001
    host.tick(host.sim_time + 0.1, 0.1)
    messages = host.tick(host.sim_time + 3601, 3601)
    timeout = next(
        row for row in messages if row.type == "invocation.update" and row.invocationId == "timeout"
    )
    assert timeout.status == "failed" and timeout.error.code == "timeout"
    assert host.values()["/workcell"]["materialSnapshot"]["operations"][-1]["status"] == "settling"
    port.q["pump"] = 0.00045
    messages.extend(host.tick(host.sim_time + 0.1, 0.1))
    port.q["pump"], port.velocities["pump"] = 0.0004, 0
    messages.extend(host.tick(host.sim_time + 0.1, 0.1))
    messages.extend(host.tick(host.sim_time + 0.1, 0.1))
    receipts = [
        row.data["receipt"]
        for row in messages
        if row.type == "entity.event"
        and row.event == "operationCompleted"
        and row.data["receipt"]["operationId"] == "timeout"
    ]
    assert len(receipts) == 1
    assert receipts[0]["status"] == "failed" and receipts[0]["fault"] == "timeout"
    assert receipts[0]["appliedUl"] == pytest.approx(40)
    assert host.values()["/workcell"]["inFlightUl"] == pytest.approx(40)


def test_unreconciled_fault_row_is_retained_beyond_the_ordinary_history_limit(tmp_path):
    host, port = build_workcell_host(tmp_path)
    wait_history(host, 70)
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    invoke(host, "aspirate", "fault", vessel="source", volumeUl=100)
    host.tick(host.sim_time + 0.1, 0.1)
    port.advance(0.0004)
    host.tick(host.sim_time + 0.1, 0.1)
    port.q["pump"] = 0.0015
    messages = host.tick(host.sim_time + 0.1, 0.1)
    snapshot = host.values()["/workcell"]["materialSnapshot"]
    assert snapshot["reconciled"] is False and len(snapshot["operations"]) == 65
    receipt = next(row.data["receipt"] for row in messages if row.type == "entity.event")
    assert receipt["status"] == "failed" and receipt["fault"] == "pump_feedback_invalid"
    assert receipt["appliedUl"] == pytest.approx(40) and receipt["observedUl"] == pytest.approx(150)
    assert snapshot["operationCount"] == 73 and snapshot["archivedOperationCount"] == 8


def test_reset_rejects_late_old_control_params_even_when_outer_source_epoch_is_current(tmp_path):
    host, port = build_workcell_host(tmp_path)
    previous_generation = host.values()["/workcell"]["controlEpoch"]
    host.reset(1)

    def request(token, source_epoch, generation, action="move", **params):
        return host.invoke(
            InvokeRequest(
                invocationId=token,
                requestId=token,
                entityKey="/workcell",
                action=action,
                epoch=source_epoch,
                createdSimTime=0,
                createdAtMs=0,
                params={"owner": "auto", "epoch": generation, "operationId": token, **params},
            )
        )

    late = request("late-old-params", 1, previous_generation, position=[0.2, 0.1, 0.01])
    assert late.error.code == "stale_control"
    generation = host.values()["/workcell"]["controlEpoch"]
    assert generation > previous_generation
    assert (
        request("old-source-request", 0, generation, position=[0.2, 0.1, 0.01]).error.code == "session_reset"
    )
    assert request("fresh", 1, generation, position=[0.2, 0.1, 0.01]).status == "accepted"
    host.tick(0.1, 0.1)
    assert port.targets["x"] == 0
    host.tick(0.3, 0.2)
    assert 0 < port.targets["x"] < 0.2
    for key, value in zip(("x", "y", "z"), (0.2, 0.1, 0.01), strict=True):
        port.q[key] = value
    host.tick(3, 2.7)
    assert host.values()["/workcell"]["materialSnapshot"]["operationCount"] == 1
    assert (
        next(row for row in host.snapshot().invocations if row.invocationId == "fresh").status == "succeeded"
    )


def test_old_control_receipt_after_reset_is_retrieved_without_applying_it_to_the_new_round(tmp_path):
    host, _ = build_workcell_host(tmp_path)
    params = {"owner": "auto", "epoch": 0, "operationId": "handoff", "nextOwner": "human", "mode": "manual"}
    invoke(host, "requestControl", "handoff", nextOwner="human", mode="manual")
    host.tick(0.1, 0.1)
    original = next(row for row in host.snapshot().invocations if row.invocationId == "handoff").result
    invoke(host, "confirmControl", "confirm", epoch=1)
    host.tick(0.2, 0.1)
    host.reset(1)
    before = host.values()["/workcell"]
    query = host.invoke(
        InvokeRequest(
            invocationId="old-receipt",
            requestId="old-receipt",
            entityKey="/workcell",
            action="requestControl",
            epoch=1,
            createdSimTime=0,
            createdAtMs=0,
            params=params,
        )
    )
    assert query.status == "accepted"
    host.tick(0.1, 0.1)
    receipt = next(row for row in host.snapshot().invocations if row.invocationId == "old-receipt").result
    assert receipt == original
    current = host.values()["/workcell"]
    # The new round's read-only safety sampling advances on this tick; replay
    # must still leave every control/material value untouched.
    assert current == before | {"controlSafeAtSimTime": 0.1}
    assert receipt["epoch"] < current["controlEpoch"]
    assert current["controlMode"] == "auto" and current["materialSnapshot"]["controlRequests"] == []

import asyncio
import uuid

import pytest

from apps.api.app.sources.synthetic_source import CENTRIFUGE, ROBOT, SyntheticSource
from packages.protocol.schema import Command, InvokeRequest, SessionOptions


def state(source):
    return {entity.key: entity.values for entity in source.entity_snapshot().entities}


async def advance(source, seconds):
    remaining = round(seconds / source.options.physicsDt)
    while remaining:
        count = min(1000, remaining)
        await source.execute(Command(requestId=uuid.uuid4().hex, command="step", args={"count": count}))
        remaining -= count


async def invoke(source, key, action, **params):
    token = uuid.uuid4().hex
    return await source.invoke(
        InvokeRequest(
            invocationId=token,
            requestId=token,
            entityKey=key,
            action=action,
            params=params,
            epoch=source.entity_snapshot().epoch,
            createdSimTime=source.sim_time,
            createdAtMs=0,
        )
    )


async def complete(source, key, action, **params):
    accepted = await invoke(source, key, action, **params)
    assert accepted.status == "accepted", accepted.model_dump()
    for _ in range(100):
        await advance(source, 0.25)
        value = next(
            inv for inv in source.entity_snapshot().invocations if inv.invocationId == accepted.invocationId
        )
        if value.status in ("succeeded", "failed", "cancelled"):
            assert value.status == "succeeded", value.model_dump()
            return value
    raise AssertionError("Virtual action exceeded its bounded expected duration")


async def test_real_centrifuge_lifecycle_is_authoritative_paused_and_final_observations_precede_success():
    source = SyntheticSource("info-tracer")
    try:
        manifest, catalog = await source.start("lab-demo", SessionOptions())
        current = dict(state(source)[CENTRIFUGE])
        await source.execute(Command(requestId="pause", command="pause"))

        def request(name, params=None, suffix=""):
            return InvokeRequest(
                invocationId=name + suffix,
                requestId=name + suffix,
                entityKey=CENTRIFUGE,
                action=name,
                params=params or {},
                epoch=0,
                createdSimTime=source.sim_time,
                createdAtMs=0,
            )

        refused = await source.invoke(request("start", {"rpm": 500, "durationSeconds": 1}, "-open"))
        assert refused.status == "rejected" and refused.error.code == "precondition_failed"
        assert refused.error.details["failed"][0]["actual"] is True
        accepted = await source.invoke(request("closeLid"))
        assert accepted.status == "accepted"
        await asyncio.sleep(1 / source.options.controlHz)
        assert source.entity_snapshot().entities[1].values["lidOpen"] is True
        await source.execute(Command(requestId="close-step", command="step", args={"count": 300}))
        assert source.host.values()[CENTRIFUGE]["lidOpen"] is False
        accepted = await source.invoke(request("start", {"rpm": 500, "durationSeconds": 1}))
        assert accepted.status == "accepted"
        await source.execute(Command(requestId="run-step", command="step", args={"count": 900}))
        stream = source.events()
        events = []
        rate_period = max(
            1 / prop["maxRateHz"]
            for prop in catalog.types["centrifuge/v1"]["properties"].values()
            if prop.get("maxRateHz")
        )
        async with asyncio.timeout(rate_period + 2 / source.options.controlHz):
            while True:
                event = await anext(stream)
                events.append(event.type)
                if event.type == "entity.observations":
                    for change in event.changes:
                        if change.key == CENTRIFUGE:
                            current.update(change.values)
                if (
                    event.type == "invocation.update"
                    and event.invocationId == "start"
                    and event.status == "succeeded"
                ):
                    assert (
                        current["phase"] == "idle"
                        and current["rpm"] == 0
                        and current["remainingSeconds"] == 0
                    )
                    break
        assert "entity.event" in events
        assert manifest.sceneRevision == catalog.sceneRevision
    finally:
        await source.close()


async def test_paused_intake_cancel_before_start_and_reset_are_authoritative():
    source = SyntheticSource("paused-actions")
    try:
        await source.start("lab-demo", SessionOptions())
        await source.execute(Command(requestId="pause", command="pause"))
        accepted = await invoke(source, CENTRIFUGE, "closeLid")
        cancelled = await source.cancel(accepted.invocationId)
        assert cancelled.status == "cancelled"
        stream = source.events()
        accepted_event = await asyncio.wait_for(anext(stream), 0.5)
        event = await asyncio.wait_for(anext(stream), 0.5)
        assert accepted_event.type == "invocation.update" and accepted_event.status == "accepted"
        assert event.type == "invocation.update" and event.status == "cancelled"
        assert event.invocationId == accepted.invocationId and source.sim_time == 0
        await advance(source, 2)
        assert state(source)[CENTRIFUGE]["lidOpen"] is True
        original = state(source)[ROBOT]["jointPositions"]
        arm = await invoke(source, ROBOT, "moveJoints", positions=[1, 1, 1, 1, 1, 1, 1])
        assert (await source.cancel(arm.invocationId)).status == "cancelled"
        await advance(source, 2)
        assert state(source)[ROBOT]["jointPositions"] == original
        accepted = await invoke(source, CENTRIFUGE, "closeLid")
        before = source.entity_snapshot()
        await asyncio.sleep(2 / source.options.controlHz)
        assert source.entity_snapshot().model_dump(exclude={"sampleTimeMs"}) == before.model_dump(
            exclude={"sampleTimeMs"}
        )
        reset = await source.execute(Command(requestId="reset", command="reset"))
        assert reset.frame.epoch == 1
        snapshot = source.entity_snapshot()
        invocation = next(inv for inv in snapshot.invocations if inv.invocationId == accepted.invocationId)
        assert invocation.status == "failed" and invocation.error.code == "session_reset"
        assert state(source)[CENTRIFUGE]["lidOpen"] is True
    finally:
        await source.close()


async def test_execution_timeout_starts_at_host_acceptance_not_old_approval_creation_time():
    source = SyntheticSource("approval-clock")
    try:
        await source.start("lab-demo", SessionOptions())
        await source.execute(Command(requestId="pause", command="pause"))
        await advance(source, 31)
        request = InvokeRequest(
            invocationId="approved",
            requestId="approved",
            entityKey=CENTRIFUGE,
            action="closeLid",
            epoch=0,
            createdSimTime=0,
            createdAtMs=0,
        )
        assert (await source.invoke(request)).status == "accepted"
        await advance(source, 1.5)
        result = next(inv for inv in source.entity_snapshot().invocations if inv.invocationId == "approved")
        assert result.status == "succeeded" and result.createdSimTime == 0
        assert result.startedSimTime >= 31
    finally:
        await source.close()


async def test_reliable_acceptance_precedes_running_when_direct_source_reply_is_delayed():
    reply_allowed = asyncio.Event()

    class DelayedReply(SyntheticSource):
        async def invoke(self, request):
            accepted = await super().invoke(request)
            await reply_allowed.wait()
            return accepted

    source = DelayedReply("delayed-acceptance")
    dispatch = None
    try:
        await source.start("lab-demo", SessionOptions())
        dispatch = asyncio.create_task(invoke(source, CENTRIFUGE, "closeLid"))
        stream = source.events()
        accepted = await asyncio.wait_for(anext(stream), 0.5)
        assert accepted.type == "invocation.update" and accepted.status == "accepted"
        running = await asyncio.wait_for(anext(stream), 0.5)
        assert running.type == "invocation.update" and running.status == "running"
        assert running.invocationId == accepted.invocationId
        assert not dispatch.done()
        reply_allowed.set()
        assert (await dispatch).status == "accepted"
    finally:
        reply_allowed.set()
        if dispatch:
            await dispatch
        await source.close()


async def test_virtual_pick_place_balance_busy_stop_and_geometry_share_device_state():
    source = SyntheticSource("virtual-journey")
    try:
        manifest, catalog = await source.start("lab-demo", SessionOptions())
        await source.execute(Command(requestId="pause", command="pause"))
        await complete(source, ROBOT, "pickPlace", **{"from": "rack.A1", "to": "centrifuge.slot1"})
        tube = "/World/Lab/Tubes/Tube01"
        slot = "/World/Lab/Centrifuge/Rotor/Slot1"
        assert state(source)[tube]["location"] == slot
        assert state(source)[CENTRIFUGE]["slots"] == [tube, None, None, None]
        frame = await source.snapshot()
        node = next(node for node in manifest.nodes if node.entityKey == tube)
        pose = next(row for row in frame.poses if row[0] == node.streamId)
        assert pose[1:4] == pytest.approx([-0.4, 0.4, 0.95], abs=1e-6)
        hand = state(source)[ROBOT]["tcpPose"]
        hand_pose = next(row for row in frame.poses if row[0] == 3)
        assert hand_pose[1:] == pytest.approx(hand, abs=1e-6)
        rejected = await invoke(source, CENTRIFUGE, "closeLid")
        assert rejected.error.code == "precondition_failed"  # arm's conservative zone still blocks the lid
        await complete(source, ROBOT, "home")
        await complete(source, CENTRIFUGE, "closeLid")
        rejected = await invoke(source, CENTRIFUGE, "start", rpm=500, durationSeconds=5)
        assert rejected.error.code == "imbalanced"
        await complete(source, CENTRIFUGE, "openLid")
        await complete(source, ROBOT, "pickPlace", **{"from": "rack.A2", "to": "centrifuge.slot3"})
        await complete(source, ROBOT, "home")
        await complete(source, CENTRIFUGE, "closeLid")
        running = await invoke(source, CENTRIFUGE, "start", rpm=500, durationSeconds=20)
        await advance(source, 1)
        refused = await invoke(source, CENTRIFUGE, "start", rpm=500, durationSeconds=1)
        assert refused.error.code == "busy"
        assert (await invoke(source, CENTRIFUGE, "stop")).status == "accepted"
        await advance(source, 2)
        terminal = next(
            inv for inv in source.entity_snapshot().invocations if inv.invocationId == running.invocationId
        )
        assert terminal.status == "cancelled"
        assert state(source)[CENTRIFUGE]["phase"] == "idle"
        # A caller cannot change authoritative host values through its snapshot.
        snapshot = source.entity_snapshot()
        snapshot.entities[0].values["phase"] = "fault"
        assert state(source)[ROBOT]["phase"] == "idle"
    finally:
        await source.close()

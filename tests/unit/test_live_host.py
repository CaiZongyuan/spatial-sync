"""Real HostCore MQTT callbacks, using socket-free per-device mailboxes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from packages.info.approvals import ApprovalStore
from packages.info.catalog import compile_catalog
from packages.info.pipeline import ActionPipeline
from packages.info.plans import PlanStore
from packages.info.program import HostCore, Reject
from packages.info.store import StateStore
from packages.live.mailbox import DeviceMailbox
from packages.programs.incubator.mqtt import IncubatorProgram
from packages.protocol.schema import ActionRequest, Actor, InvokeRequest, SessionOptions

KEY = "/Real/Incubator2"


def host():
    type_path = Path(__file__).parents[2] / "devices/types/incubator.v1.yaml"
    type_doc = yaml.safe_load(type_path.read_text("utf-8"))
    config = IncubatorProgram.Config.model_validate({
        "telemetry": {"topic": "telemetry", "map": {"temperatureC": "$.temp"}, "timeoutSeconds": 10},
        "commands": {"setSetpoint": {"topic": "cmd", "ack": "ack", "timeoutSeconds": 1}},
    })
    catalog = compile_catalog("live-test", {"incubator/v1": type_doc}, {KEY: {
        "key": KEY, "type": "incubator/v1", "program": IncubatorProgram.id,
        "config": config.model_dump(mode="json"),
    }}, {KEY: config})
    mailbox = DeviceMailbox(clock=lambda: core.sim_time)
    def availability(key):
        if not mailbox.online(core.sim_time):
            return Reject(code="device_unavailable", message="No fresh connected telemetry")

    core = HostCore(catalog, {IncubatorProgram.id: IncubatorProgram}, source="device",
                    drivers={KEY: mailbox}, availability=availability)
    return core, mailbox


def request(identity):
    return InvokeRequest(invocationId=identity, requestId=identity, entityKey=KEY,
                         action="setSetpoint", params={"temperatureC": 38}, actor=Actor(), epoch=0,
                         createdSimTime=0, createdAtMs=0)


def test_actual_host_maps_telemetry_requires_matching_ack_and_expires_disconnected_readings():
    core, mailbox = host()
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": "invalid"})
    core.tick(.1, .1)
    assert core.values()[KEY] == {} and not core._disabled
    mailbox.receive("telemetry", {"temp": 37})
    core.tick(.2, .1)
    assert core.values()[KEY]["temperatureC"] == 37
    assert core.invoke(request("first")).status == "accepted"
    core.tick(.3, .1)
    assert list(mailbox.outgoing) == [("cmd", {"requestId": "first", "action": "setSetpoint",
                                                "params": {"temperatureC": 38}})]
    mailbox.mark_dispatched("first", .3)
    mailbox.receive("ack", {"requestId": "unrelated", "status": "succeeded"})
    assert all(getattr(item, "status", None) != "succeeded" for item in core.tick(.4, .1))
    mailbox.receive("ack", {"requestId": "first", "status": "succeeded"})
    assert any(getattr(item, "status", None) == "succeeded" for item in core.tick(.5, .1))
    assert core.invoke(request("second")).status == "accepted"
    core.tick(.6, .1)
    updates = core.tick(1.7, 1.1)
    assert any(getattr(item, "error", None) and item.error.code == "timeout" for item in updates)
    mailbox.disconnect()
    core.tick(3.3, 1.6)
    assert core.metadata()[KEY]["temperatureC"]["stale"] is True
    assert core.invoke(request("offline")).error.code == "device_unavailable"


@pytest.mark.parametrize("topic,identity,retained", [
    ("wrong-device-ack", "first", False), ("ack", "wrong-id", False), ("ack", "first", True),
])
def test_wrong_device_identity_and_retained_ack_cannot_complete_a_command(topic, identity, retained):
    core, mailbox = host()
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": 37})
    core.tick(.1, .1)
    assert core.invoke(request("first")).status == "accepted"
    core.tick(.2, .1)
    mailbox.mark_dispatched("first", .2)
    mailbox.receive(topic, {"requestId": identity, "status": "succeeded"}, retained=retained)
    assert all(getattr(item, "status", None) != "succeeded" for item in core.tick(.3, .1))
    updates = core.tick(1.3, 1)
    assert any(getattr(item, "error", None) and item.error.code == "timeout" for item in updates)


def test_disconnect_after_admission_never_stages_command_and_old_ack_cannot_finish_new_work():
    core, mailbox = host()
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": 37})
    core.tick(.1, .1)
    assert core.invoke(request("first")).status == "accepted"
    mailbox.disconnect()
    updates = core.tick(.2, .1)
    assert not mailbox.outgoing
    assert any(getattr(item, "error", None) and item.error.code == "device_unavailable" for item in updates)
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": 37}, retained=True)
    core.tick(.3, .1)
    assert core.invoke(request("still-offline")).error.code == "device_unavailable"
    mailbox.receive("telemetry", {"temp": 37})
    core.tick(.4, .1)
    assert core.invoke(request("second")).status == "accepted"
    core.tick(.5, .1)
    mailbox.mark_dispatched("second", .5)
    mailbox.receive("ack", {"requestId": "first", "status": "succeeded"})
    assert all(getattr(item, "status", None) != "succeeded" for item in core.tick(.6, .1))
    mailbox.receive("ack", {"requestId": "second", "status": "succeeded"})
    assert any(getattr(item, "status", None) == "succeeded" for item in core.tick(.7, .1))
    mailbox.receive("ack", {"requestId": "second", "status": "succeeded"})
    assert all(getattr(item, "status", None) != "succeeded" for item in core.tick(.8, .1))


def test_late_ack_cannot_turn_timeout_into_success():
    core, mailbox = host()
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": 37})
    core.tick(.1, .1)
    assert core.invoke(request("first")).status == "accepted"
    core.tick(.2, .1)
    mailbox.mark_dispatched("first", .2)
    mailbox.receive("ack", {"requestId": "first", "status": "succeeded"}, received_at=1.3)
    updates = core.tick(1.3, 1.1)
    assert all(getattr(item, "status", None) != "succeeded" for item in updates)
    assert any(getattr(item, "error", None) and item.error.code == "timeout" for item in updates)


def test_actual_pipeline_fast_check_uses_same_availability_before_missing_reading_conditions():
    core, mailbox = host()
    store = StateStore(core.catalog.sceneRevision)
    store.apply(core.snapshot())
    pipeline = ActionPipeline(SimpleNamespace(state="RUNNING", source=core), core.catalog, store,
                              lambda _message: None)
    assert pipeline._check(request("offline"))[0] == "device_unavailable"
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": 37})
    for message in core.tick(.1, .1):
        if hasattr(message, "changes"):
            store.apply(message)
    assert pipeline._check(request("online")) is None
    mailbox.disconnect()
    assert pipeline._check(request("disconnected"))[0] == "device_unavailable"


@pytest.mark.parametrize("actor_kind", ["user", "cli", "agent"])
async def test_shared_pipeline_requires_human_before_any_dispatch_and_cancels_staged_command(
    tmp_path, actor_kind,
):
    core, mailbox = host()
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": 37})
    core.tick(.1, .1)
    store = StateStore(core.catalog.sceneRevision)
    store.apply(core.snapshot())
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    plans = PlanStore(tmp_path / "plans.jsonl", approvals)

    class LocalHost:
        availability = core.availability

        async def invoke(self, wire_request):
            return core.invoke(wire_request)

        async def cancel(self, invocation_id):
            return core.cancel(invocation_id)

    target = SimpleNamespace(state="RUNNING", session_id="live-test", source=LocalHost(),
                             sim_time=.1, epoch=0, approvals=approvals, plans=plans, catalog=core.catalog,
                             options=SessionOptions(approval={"high": "require"}),
                             manifest=SimpleNamespace(sceneRevision=core.catalog.sceneRevision))
    pipeline = ActionPipeline(target, core.catalog, store, lambda _message: None)

    async def current_pipeline():
        return pipeline

    target.action_pipeline = current_pipeline
    try:
        command = ActionRequest(requestId="public-high-risk", entityKey=KEY, action="setSetpoint",
                                params={"temperatureC": 38})
        value = await pipeline.invoke(command, "operator", Actor(kind=actor_kind, name="caller"))
        assert value.status == "pending_approval"
        assert len(approvals.records) == 1
        core.tick(.2, .1)
        assert not mailbox.outgoing and not core.active_invocations(KEY)
        with pytest.raises(PermissionError):
            await pipeline.approve(value.invocationId, "approve", "operator", Actor(kind="agent"))
        assert not mailbox.outgoing
        accepted = await pipeline.approve(value.invocationId, "approve", "operator", Actor(kind="user"))
        assert accepted.status == "accepted", accepted.error
        core.tick(.3, .1)
        assert [payload["requestId"] for _, payload in mailbox.outgoing] == [value.invocationId]
        # Cancel before the transport takes this command: nothing may reach hardware later.
        await pipeline.cancel(value.invocationId, "operator")
        assert not mailbox.outgoing and not mailbox.dispatched
        assert mailbox.take_commands() == ()
        mailbox.disconnect()
        core.tick(3.3, 3)
        offline = await pipeline.invoke(command.model_copy(update={"requestId": "offline"}),
                                        "operator", Actor(kind=actor_kind))
        assert offline.error.code == "device_unavailable"
        assert len(approvals.records) == 1
    finally:
        pipeline.close()
        await plans.close()


def test_telemetry_retains_receipt_time_and_ack_before_dispatch_cannot_succeed():
    core, mailbox = host()
    mailbox.connected = True
    mailbox.receive("telemetry", {"temp": 37}, received_at=.1, sample_time_ms=100)
    core.tick(2.9, 2.9)
    assert core.metadata()[KEY]["temperatureC"]["simTime"] == .1
    assert core.metadata()[KEY]["temperatureC"]["sampleTimeMs"] == 100
    assert core.invoke(request("first")).status == "accepted"
    mailbox.receive("ack", {"requestId": "first", "status": "succeeded"}, received_at=2.9)
    updates = core.tick(3, .1)
    assert all(getattr(item, "status", None) != "succeeded" for item in updates)
    mailbox.mark_dispatched("first", 3)
    # Even after dispatch, the old receipt cannot acquire a new timestamp and succeed.
    mailbox.receive("ack", {"requestId": "first", "status": "succeeded"}, received_at=2.9)
    updates = core.tick(3.1, .1)
    assert all(getattr(item, "status", None) != "succeeded" for item in updates)
    assert core.metadata()[KEY]["temperatureC"]["stale"] is True
    mailbox.receive("ack", {"requestId": "first", "status": "succeeded"}, received_at=3.1)
    updates = core.tick(3.2, .1)
    assert any(getattr(item, "status", None) == "succeeded" for item in updates)

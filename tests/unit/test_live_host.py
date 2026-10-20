"""Real HostCore MQTT callbacks, using socket-free per-device mailboxes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from packages.info.catalog import compile_catalog
from packages.info.pipeline import ActionPipeline
from packages.info.program import HostCore, Reject
from packages.info.store import StateStore
from packages.live.mailbox import DeviceMailbox
from packages.programs.incubator.mqtt import IncubatorProgram
from packages.protocol.schema import Actor, InvokeRequest

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
    mailbox = DeviceMailbox()
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
    mailbox.receive("ack", {"requestId": "first", "status": "succeeded"})
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

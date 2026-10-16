"""Ownership, epoch and JSON export behavior at the validated-message boundary."""

import json

import pytest
from pydantic import ValidationError

from packages.info.store import StateStore
from packages.protocol.schema import EntityObservations, EntitySnapshot, InvocationUpdate


def observation(*, rev=2, at=200, values=None, epoch=0, revision="scene"):
    return EntityObservations(
        sceneRevision=revision,
        epoch=epoch,
        simTime=2,
        sampleTimeMs=at,
        source="virtual",
        changes=[{"key": "/a", "rev": rev, "values": values or {"x": 2}}],
    )


def store():
    result = StateStore("scene")
    result.apply(
        EntitySnapshot(
            sceneRevision="scene",
            epoch=0,
            sampleTimeMs=100,
            entities=[{"key": "/a", "rev": 1, "values": {"x": 1, "object": {"items": [1]}}}],
        )
    )
    return result


def test_validated_updates_own_nested_input_and_leave_retained_snapshots_unchanged():
    state = store()
    previous = state.entities["/a"]
    message = observation(values={"object": {"items": [{"n": 2}]}, "nil": None})
    state.apply(message)
    message.changes[0].values["object"]["items"][0]["n"] = 99
    assert previous.values == {"x": 1, "object": {"items": [1]}}
    assert state.entities["/a"].values == {"x": 1, "object": {"items": [{"n": 2}]}, "nil": None}


def test_json_export_is_immutable_current_and_reset_and_revision_safe():
    state = store()
    before = state.export_json("/a")
    assert isinstance(before, str)
    copied = json.loads(before)
    copied["values"]["object"]["items"][0] = 99
    assert json.loads(state.export_json("/a"))["values"]["object"] == {"items": [1]}
    state.apply(observation(values={"x": 2, "huge": 10**100}))
    current = json.loads(state.export_json("/a"))
    assert current["rev"] == 2 and current["values"]["huge"] == 10**100
    assert current["meta"]["x"] == {"simTime": 2, "sampleTimeMs": 200, "source": "virtual"}
    assert json.loads(before)["values"]["x"] == 1
    assert not state.apply(observation(epoch=1))
    assert not state.apply(observation(revision="old"))
    assert json.loads(state.export_json("/a")) == current
    state.apply(EntitySnapshot(sceneRevision="scene", epoch=1, sampleTimeMs=300))
    assert state.export_json("/a") is None
    state.configure("next")
    assert state.export_json("/a") is None


def test_equal_revision_delayed_properties_keep_individual_timestamp_guards_in_export():
    state = store()
    state.apply(observation(values={"x": 2}))
    state.apply(observation(at=150, values={"x": -1, "delayed": 3}))
    result = json.loads(state.export_json("/a"))
    assert result["values"]["x"] == 2 and result["values"]["delayed"] == 3
    assert result["meta"]["x"]["sampleTimeMs"] == 200
    assert result["meta"]["delayed"]["sampleTimeMs"] == 150
    state.apply(observation(rev=3, at=100, values={"x": -2}))
    assert json.loads(state.export_json("/a"))["values"]["x"] == 2
    assert json.loads(state.export_json("/a"))["rev"] == 3


def test_batch_timestamp_cannot_be_mutated_through_another_entity():
    state = store()
    message = observation()
    message.changes.append(message.changes[0].model_copy(update={"key": "/b"}))
    state.apply(message)
    with pytest.raises(ValidationError):
        state.entities["/a"].meta["x"].source = "device"
    assert state.entities["/b"].meta["x"].source == "virtual"


@pytest.mark.parametrize("kind", ["observation", "invocation"])
async def test_recorded_wire_and_event_history_preserve_null_and_large_integer_semantics(kind):
    state = store()
    message = (
        observation(values={"nil": None, "huge": 10**100})
        if kind == "observation"
        else InvocationUpdate(
            sceneRevision="scene",
            invocationId="inv-one",
            requestId="request-one",
            entityKey="/a",
            action="inspect",
            params={"nil": None, "huge": 10**100},
            epoch=0,
            createdSimTime=0,
            createdAtMs=1,
            status="accepted",
        )
    )
    payload = state.record(message)
    assert json.loads(payload) == message.model_dump(mode="json", exclude_none=True)
    history = await state.events_after(0, 0)
    assert history["events"][0]["message"] == message.model_dump(mode="json")

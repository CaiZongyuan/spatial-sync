"""Retained observations stay readable when publication races a long-poll timeout."""

import asyncio
from collections import deque
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from apps.api.app.evaluations import EvaluationService
from apps.api.app.info_routes import install_info_routes
from packages.info.store import StateStore
from packages.protocol.schema import EntityObservations, EntitySnapshot, ObservationChange

ROBOT = "/World/Lab/RobotA"
TUBE = "/World/Lab/Tubes/Tube01"
SLOT = "/World/Lab/Centrifuge/Rotor/Slot1"


class PublishAfterCancellation(asyncio.Task):
    """Order timeout callback -> producer -> reader resumption, using a real Event."""

    def __init__(self, coroutine, publish):
        super().__init__(coroutine)
        self.publish = publish

    def cancel(self, msg=None):
        cancelled = super().cancel(msg)
        if cancelled and self.publish is not None:
            publish, self.publish = self.publish, None
            publish()
        return cancelled


def observation(values, *, rev=457, at=193417779.3243, sim_time=4.283333333333333, stale=False):
    return EntityObservations(
        sceneRevision="scene",
        epoch=0,
        simTime=sim_time,
        sampleTimeMs=at,
        source="virtual",
        changes=[
            {
                "key": ROBOT,
                "rev": rev,
                "values": values,
                "meta": {
                    prop: {"simTime": sim_time, "sampleTimeMs": at, "source": "virtual", "stale": True}
                    for prop in values
                }
                if stale
                else {},
            }
        ],
    )


@pytest.mark.parametrize("capacity", [1, 2])
async def test_timeout_boundary_returns_available_events_with_their_gap_and_cursor(capacity):
    state = StateStore("scene")
    state._events = deque(maxlen=capacity)
    messages = [observation({"holding": TUBE}), observation({"holding": None}, rev=1019)]
    result = await PublishAfterCancellation(
        state.events_after(0, 0.001),
        lambda: [state.record(message) for message in messages],
    )
    first = 3 - capacity
    assert result == {
        "events": [
            {"seq": index, "message": messages[index - 1].model_dump(mode="json")}
            for index in range(first, 3)
        ],
        "lastSeq": 2,
        "timedOut": False,
        "gap": capacity == 1,
    }
    assert await state.events_after(result["lastSeq"], 0) == {
        "events": [],
        "lastSeq": 2,
        "timedOut": True,
        "gap": False,
    }


async def test_reader_cancellation_propagates_and_does_not_consume_a_boundary_publication():
    state = StateStore("scene")
    message = observation({"holding": TUBE})
    entered = asyncio.Event()

    async def read():
        entered.set()
        return await state.events_after(0, 1)

    waiting = PublishAfterCancellation(read(), lambda: state.record(message))
    await entered.wait()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert (await state.events_after(0, 0))["events"] == [
        {"seq": 1, "message": message.model_dump(mode="json")},
    ]


@pytest.mark.parametrize("prefix", ["/api/v1/sessions/session-one", "/api/v1/live"])
async def test_public_event_endpoint_delivers_a_timeout_boundary_publication(prefix):
    state = StateStore("scene")
    message = observation({"holding": TUBE})

    async def target():
        return SimpleNamespace(info=state)

    app = FastAPI()
    install_info_routes(app, None, lambda _: "operator", prefix=prefix, resolve_target=target)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await PublishAfterCancellation(
            http.get(prefix + "/events", params={"after": 0, "timeoutSeconds": 0.001}),
            lambda: state.record(message),
        )
    assert response.status_code == 200
    assert response.json() == {
        "events": [{"seq": 1, "message": message.model_dump(mode="json")}],
        "lastSeq": 1,
        "timedOut": False,
        "gap": False,
    }


@pytest.mark.parametrize(
    "grasp",
    ["observed", "never", "stale", "gap-after-grasp", "epoch-drift-after-grasp", "wrong-scope-after-grasp"],
)
@pytest.mark.parametrize("success", [True, False])
async def test_episode_latches_only_observed_fresh_transients_independently_of_final_success(grasp, success):
    initial = EntitySnapshot(
        sceneRevision="scene",
        epoch=0,
        entities=[
            {
                "key": ROBOT,
                "rev": 1,
                "values": {"holding": None},
                "meta": {"holding": {"source": "virtual", "simTime": 0, "sampleTimeMs": 0}},
            },
            {
                "key": TUBE,
                "rev": 1,
                "values": {"location": "/World/Lab/Rack/A1"},
                "meta": {"location": {"source": "virtual", "simTime": 0, "sampleTimeMs": 0}},
            },
        ],
    )
    state = StateStore("scene")
    state.apply(initial)
    transient = observation({"holding": TUBE if grasp != "never" else None}, stale=grasp == "stale")
    recovered = observation({"holding": None}, rev=1019, at=193422987.2845, sim_time=9.5)
    recovered.changes.append(
        ObservationChange(
            key=TUBE,
            rev=2,
            values={"location": SLOT if success else "/World/Lab/Rack/A1"},
        )
    )
    invocation = SimpleNamespace(invocationId="inv-recorded", status="running", error=None)

    def publish(message):
        state.apply(message)
        state.record(message)

    original_read = state.events_after
    reads = 0

    async def events_after(after, timeout):
        nonlocal reads
        if reads:
            if grasp == "epoch-drift-after-grasp":
                session.epoch = 1
                publish(initial.model_copy(update={"epoch": 1, "sampleTimeMs": 200000000}))
            else:
                publish(
                    recovered.model_copy(update={"epoch": 1})
                    if grasp == "wrong-scope-after-grasp"
                    else recovered
                )
            invocation.status = "succeeded"
        result = await original_read(after, timeout)
        if grasp == "gap-after-grasp" and reads:
            result["gap"] = True
        reads += 1
        return result

    state.events_after = events_after

    async def invoke(*_):
        return invocation

    pipeline = SimpleNamespace(invoke=invoke, table=SimpleNamespace(get=lambda _: invocation))

    async def action_pipeline():
        return pipeline

    session = SimpleNamespace(
        session_id="owned-observations",
        manifest=SimpleNamespace(sceneRevision="scene"),
        epoch=0,
        state="RUNNING",
        info=state,
        catalog=SimpleNamespace(
            entities={ROBOT: {"type": "robot-arm/v1"}, TUBE: {"type": "tube/v1"}},
            types={
                "robot-arm/v1": {"properties": {"holding": {"schema": {"type": ["string", "null"]}}}},
                "tube/v1": {"properties": {"location": {"schema": {"type": "string"}}}},
            },
        ),
        info_snapshot=lambda: initial,
        action_pipeline=action_pipeline,
    )
    place = {"entity": TUBE, "property": "location", "equals": SLOT}
    task = {
        "robot": ROBOT,
        "implements": "robot-arm/v1.pickPlace",
        "timeoutSeconds": 90,
        "success": {"all": [place, {"property": "holding", "isNull": True}]},
        "milestones": [
            {"id": "grasp", "when": {"property": "holding", "equals": TUBE}},
            {"id": "place", "when": place},
            {
                "id": "never",
                "when": {
                    "property": "holding",
                    "equals": "/World/Lab/Tubes/Tube02",
                },
            },
        ],
    }
    case = {"id": "train-one", "params": {"from": "rack.A1", "to": "centrifuge.slot1"}}
    attempt = {"caseId": "train-one"}
    evaluated = PublishAfterCancellation(
        object.__new__(EvaluationService)._episode(
            session, SimpleNamespace(task=task), case, attempt=attempt
        ),
        lambda: publish(transient),
    )
    if grasp in ("stale", "gap-after-grasp", "epoch-drift-after-grasp", "wrong-scope-after-grasp"):
        with pytest.raises(HTTPException) as unavailable:
            await evaluated
        assert unavailable.value.detail["code"] == (
            "evaluation_observation_gap"
            if grasp == "gap-after-grasp"
            else "evaluation_observation_unavailable"
        )
        assert attempt["milestones"]["grasp"] is (grasp != "stale")
        assert "success" not in attempt
        return
    outcome = await evaluated
    assert reads == 2
    assert state.values()[ROBOT]["holding"] is None
    assert outcome["success"] is success
    assert outcome["milestones"] == {"grasp": grasp == "observed", "place": success, "never": False}
    assert outcome["invocationStatus"] == "succeeded"

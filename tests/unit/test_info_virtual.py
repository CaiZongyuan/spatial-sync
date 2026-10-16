import asyncio

from apps.api.app.sources.synthetic_source import CENTRIFUGE, SyntheticSource
from packages.protocol.schema import Command, InvokeRequest, SessionOptions


async def test_real_centrifuge_lifecycle_is_authoritative_paused_and_final_observations_precede_success():
    source = SyntheticSource("info-tracer")
    try:
        manifest, catalog = await source.start("lab-demo", SessionOptions())
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
        await asyncio.sleep(0.02)
        assert source.entity_snapshot().entities[1].values["lidOpen"] is True
        await source.execute(Command(requestId="close-step", command="step", args={"count": 300}))
        assert source.host.values()[CENTRIFUGE]["lidOpen"] is False
        accepted = await source.invoke(request("start", {"rpm": 500, "durationSeconds": 1}))
        assert accepted.status == "accepted"
        await source.execute(Command(requestId="run-step", command="step", args={"count": 900}))
        stream = source.events()
        current, events = {}, []
        while True:
            event = await asyncio.wait_for(anext(stream), 0.5)
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
                assert current["phase"] == "idle" and current["rpm"] == 0 and current["remainingSeconds"] == 0
                break
        assert "entity.event" in events
        assert manifest.sceneRevision == catalog.sceneRevision
    finally:
        await source.close()

import asyncio

from test_info_virtual import advance, invoke

from apps.api.app.sources.synthetic_source import CENTRIFUGE, SyntheticSource
from packages.protocol.schema import Command, SessionOptions


class PublicationClock:
    now = 0.0

    def __call__(self):
        return self.now


async def test_manual_steps_obey_wall_publication_limits_and_paused_service_flushes_final_values():
    clock = PublicationClock()
    source = SyntheticSource("publication-clock", publication_clock=clock)
    messages = []
    rpm_ready = asyncio.get_running_loop().create_future()
    terminal = asyncio.get_running_loop().create_future()
    progress_ready = asyncio.get_running_loop().create_future()
    run_id = None

    async def consume():
        async for event in source.events():
            messages.append((clock.now, event))
            if event.type == "entity.observations":
                if any(
                    change.key == CENTRIFUGE and change.values.get("rpm") == 500 for change in event.changes
                ):
                    if not rpm_ready.done():
                        rpm_ready.set_result(event)
            if event.type == "invocation.update" and event.invocationId == run_id:
                if event.status == "running" and clock.now >= 0.25 and not progress_ready.done():
                    progress_ready.set_result(event)
                if event.status == "succeeded" and not terminal.done():
                    terminal.set_result(event)

    consumer = None
    try:
        await source.start("lab-demo", SessionOptions())
        await source.execute(Command(requestId="pause", command="pause"))
        consumer = asyncio.create_task(consume())
        await invoke(source, CENTRIFUGE, "closeLid")
        await advance(source, 1.25)
        run = await invoke(source, CENTRIFUGE, "start", rpm=500, durationSeconds=10)
        run_id = run.invocationId
        await advance(source, 0.75)
        await asyncio.sleep(0)  # let the observer consume the completed manual-step batch
        progress = [
            event
            for _, event in messages
            if event.type == "invocation.update"
            and event.invocationId == run_id
            and event.status == "running"
        ]
        assert len(progress) <= 1
        assert not rpm_ready.done()
        frozen_sim_time = source.sim_time
        rpm_period = 1 / source.catalog.property(CENTRIFUGE, "rpm")["maxRateHz"]
        service_period = 1 / source.options.controlHz
        clock.now = 0.25
        await asyncio.wait_for(rpm_ready, rpm_period + service_period)
        await asyncio.wait_for(progress_ready, 0.25 + service_period)
        assert source.sim_time == frozen_sim_time
        await advance(source, 12)
        await asyncio.sleep(0)
        assert not terminal.done()
        frozen_sim_time = source.sim_time
        clock.now += rpm_period
        await asyncio.wait_for(terminal, rpm_period + service_period)
        assert source.sim_time == frozen_sim_time
        successful_index = next(
            i
            for i, (_, event) in enumerate(messages)
            if event.type == "invocation.update"
            and event.invocationId == run_id
            and event.status == "succeeded"
        )
        assert any(
            event.type == "entity.observations"
            and any(c.key == CENTRIFUGE and c.values.get("rpm") == 0 for c in event.changes)
            for _, event in messages[:successful_index]
        )
        progress_times = [
            at
            for at, event in messages
            if event.type == "invocation.update"
            and event.invocationId == run_id
            and event.status == "running"
        ]
        assert all(b - a >= 0.25 for a, b in zip(progress_times, progress_times[1:]))
    finally:
        await source.close()
        if consumer:
            await consumer

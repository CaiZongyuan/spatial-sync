"""A coarse host clock must not accumulate a new delay after every publication."""

import asyncio
import math

import pytest

import apps.api.app.sources.synthetic_source as synthetic_module
from packages.protocol.schema import Command, InvokeRequest, SessionOptions


@pytest.mark.parametrize("interrupt", [None, "pause", "reset"])
async def test_catchup_delivers_observations_before_later_control_ticks(monkeypatch, interrupt):
    source = synthetic_module.SyntheticSource("catchup-delivery")
    original_sleep = asyncio.sleep
    now = 0.0
    finished = asyncio.Event()
    received_at_ticks = []
    commands, completed_advance = [], []

    async def coarse_sleep(delay):
        nonlocal now
        if delay == 0:
            await original_sleep(0)
        elif now == 0:
            now = 0.2  # One coarse wakeup requires 48 physical steps / 12 control ticks.
        else:
            completed_advance.append((
                source._step_index, source._control_index, source.host.tick_index, source._epoch,
            ))
            finished.set()
            await asyncio.Event().wait()

    async def receive():
        async for event in source.events():
            if event.type == "entity.observations":
                received_at_ticks.append(source._control_index)
                if interrupt is not None and not commands:
                    assert source._lock.locked()
                    commands.append(asyncio.create_task(source.execute(Command(
                        requestId="interrupt-catchup", command=interrupt,
                    ))))

    monkeypatch.setattr(synthetic_module.asyncio, "sleep", coarse_sleep)
    monkeypatch.setattr(synthetic_module.time, "monotonic", lambda: now)
    reader = asyncio.create_task(receive())
    try:
        await source.start("lab-demo", SessionOptions())
        accepted = await source.invoke(InvokeRequest(
            invocationId="catchup-move", requestId="catchup-move", epoch=0,
            entityKey=synthetic_module.ROBOT, action="moveTo",
            params={"location": "rack.A1", "pose": "approach"},
            createdSimTime=0, createdAtMs=0,
        ))
        assert accepted.status == "accepted"
        await finished.wait()
        assert completed_advance == [(48, 12, 12, 0)]
        assert received_at_ticks and received_at_ticks[0] < 12
        if commands:
            result = await commands[0]
            assert result.ack.status == "applied"
            if interrupt == "reset":
                assert result.frame.epoch == 1 and result.frame.simTime == 0
                assert source._step_index == source._control_index == source.host.tick_index == 0
            else:
                assert result.state.state == "PAUSED" and result.frame.simTime == 0.2
    finally:
        await source.close()
        await reader
        await asyncio.gather(*commands)


async def test_publication_preserves_rate_under_coarse_wakeups(monkeypatch):
    source = synthetic_module.SyntheticSource("cadence")
    original_sleep = asyncio.sleep
    now = 0.0
    publications = []
    finished = asyncio.Event()
    last_seq = 0

    async def coarse_sleep(delay):
        nonlocal now, last_seq
        if delay == 0:
            await original_sleep(0)
            return
        frame = source.latest_frame()
        if frame and frame.seq != last_seq:
            last_seq = frame.seq
            if frame.type == "transform.frame":
                publications.append(now)
        if now >= 2:
            finished.set()
            await asyncio.Event().wait()  # interrupted by the public close lifecycle below
            return
        now += max(1, math.ceil(delay / (1 / 64))) / 64

    monkeypatch.setattr(synthetic_module.asyncio, "sleep", coarse_sleep)
    monkeypatch.setattr(synthetic_module.time, "monotonic", lambda: now)
    try:
        await source.start("lab-demo", SessionOptions())
        await finished.wait()
        # Sixty real unique publication slots in two seconds, with no catch-up burst.
        assert 59 <= len(publications) <= 60
        assert min(b - a for a, b in zip(publications, publications[1:])) >= 1 / 64
        assert source.sim_time == pytest.approx(2, abs=source.options.physicsDt)
    finally:
        await source.close()


@pytest.mark.parametrize("rtf", [0.1, 1, 10, None])
async def test_running_loop_flushes_without_repeating_a_due_control_ticks_drain(monkeypatch, rtf):
    source = synthetic_module.SyntheticSource("drain-boundary")
    original_sleep = asyncio.sleep
    now = 0.0
    intervals = iter((0.001, 0.003, 0.005, math.nextafter(1 / 60, 0), 1 / 60, 0.02, 0.3, 0.001))
    iterations = []
    finished = asyncio.Event()

    async def controlled_sleep(delay):
        nonlocal now
        if delay == 0:
            await original_sleep(0)
            return
        elapsed = next(intervals, None)
        if elapsed is None:
            finished.set()
            await asyncio.Event().wait()
        else:
            now += elapsed
            iterations.append({"ticks": 0, "preDrains": 0})

    monkeypatch.setattr(synthetic_module.asyncio, "sleep", controlled_sleep)
    monkeypatch.setattr(synthetic_module.time, "monotonic", lambda: now)
    try:
        await source.start("lab-demo", SessionOptions(realtimeFactor=rtf))
        original_tick, original_drain = source.host.tick, source.host.drain_events

        def tick(*args):
            iterations[-1]["ticks"] += 1
            return original_tick(*args)

        def drain():
            iterations[-1]["preDrains"] += 1
            return original_drain()

        monkeypatch.setattr(source.host, "tick", tick)
        monkeypatch.setattr(source.host, "drain_events", drain)
        await finished.wait()
        assert any(row["ticks"] for row in iterations)
        assert all(row["preDrains"] == (0 if row["ticks"] else 1) for row in iterations)
        assert source.host.tick_index == source._control_index
        assert source._control_index == int(source.sim_time * source.options.controlHz + 1e-9)
        if rtf == 10:
            assert max(row["ticks"] for row in iterations) >= 100
        if rtf == 0.1:
            assert any(not row["ticks"] for row in iterations)
    finally:
        await source.close()

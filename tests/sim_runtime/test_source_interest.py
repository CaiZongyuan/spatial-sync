"""Real spawned Runner target changes preserve complete captures and actual heartbeat."""

import asyncio

import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.sources.newton_source import NewtonSource
from packages.protocol.schema import Command, SessionOptions


async def test_cpu_runner_narrows_readback_but_snapshots_and_control_stay_complete():
    source = NewtonSource("interest-cpu")
    try:
        manifest = await source.start("falling-boxes", SessionOptions(device="cpu"))
        ids = {node.streamId for node in manifest.nodes if node.kind == "body"}
        selected = max(ids)
        barrier = await source.set_interest(frozenset([selected]))
        assert {row[0] for row in barrier.poses} == ids and barrier.type == "stream.snapshot"
        async with asyncio.timeout(3):
            while source.latest_frame().seq <= barrier.seq or source.stats()["readbackBodyCount"] != 1:
                await asyncio.sleep(0.02)
        assert {row[0] for row in source.latest_frame().poses} == {selected}
        assert source.stats()["gatherBodyCount"] == 1 and source.stats()["readbackBytes"] == 28
        assert manifest.source.shapeCount == source.stats()["shapeCount"]
        before_full = source.latest_frame()
        full = await source.snapshot()
        assert full.seq > before_full.seq and full.simTime >= before_full.simTime
        assert {row[0] for row in full.poses} == ids
        async with asyncio.timeout(2):
            while source.latest_frame().seq <= full.seq:
                await asyncio.sleep(0.01)
        assert {row[0] for row in source.latest_frame().poses} == {selected}
        pause = await source.execute(Command(requestId="pause", command="pause"))
        assert {row[0] for row in pause.frame.poses} == ids
        paused = await source.snapshot()
        assert (paused.seq, paused.simTime) == (pause.frame.seq, pause.frame.simTime)
        heartbeat = source.stats()["runnerHeartbeatSeq"]
        async with asyncio.timeout(3):
            while source.stats()["runnerHeartbeatSeq"] == heartbeat:
                await asyncio.sleep(0.02)
        assert source.latest_frame().seq == paused.seq
        assert 0 <= source.stats()["runnerHeartbeatMs"] < 1500
        assert source.stats()["runnerHeartbeatAlive"] is True
        expanded = await source.set_interest(None)
        assert expanded.seq == paused.seq and {row[0] for row in expanded.poses} == ids
        reset = await source.execute(Command(requestId="reset", command="reset"))
        assert reset.frame.epoch == 1 and reset.frame.poses[0][3] == pytest.approx(1.5)
        assert {row[0] for row in reset.frame.poses} == ids
    finally:
        await source.close()
    assert source.stats()["runnerHeartbeatAlive"] is False

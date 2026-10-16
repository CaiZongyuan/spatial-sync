import asyncio
import multiprocessing as mp
import os
import signal

import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.sources.newton_source import NewtonSource
from packages.protocol.schema import Command, SessionOptions, SessionState


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda:0", marks=pytest.mark.gpu)])
async def test_spawned_source_preserves_command_frames_reset_seq_and_cleanup(device):
    source = NewtonSource("source-test")
    try:
        manifest = await source.start("falling-boxes", SessionOptions(device=device))
        initial = source.latest_frame()
        assert manifest.sessionId == "source-test"
        assert source.stats()["runnerPID"] != os.getpid()
        assert source.stats()["gatherMode"] == "subscription-union-periodic-full-control"
        bodies = [node for node in manifest.nodes if node.kind == "body"]
        assert source.stats()["gatherBodyCount"] == len(bodies)
        assert {row[0] for row in initial.poses} == {node.streamId for node in bodies}
        assert initial.poses[0][3] == pytest.approx(1.5)
        pause = await source.execute(Command(requestId="pause", command="pause"))
        assert pause.state.state == "PAUSED"
        assert pause.frame.seq == pause.state.seq
        assert pause.frame.simTime == pause.ack.simTime
        rejected = await source.execute(Command(requestId="too-many", command="step", args={"count": 1001}))
        assert rejected.ack.status == "rejected" and rejected.ack.reason == "invalid_args"
        assert rejected.ack.simTime == pause.ack.simTime
        paused_seq = source.latest_frame().seq
        await asyncio.sleep(0.08)
        assert source.latest_frame().seq == paused_seq
        step = await source.execute(Command(requestId="step", command="step", args={"count": 24}))
        assert step.frame.simTime == pytest.approx(pause.ack.simTime + 0.1)
        assert step.frame.seq > paused_seq
        assert source.latest_frame().seq == step.frame.seq
        reset = await source.execute(Command(requestId="reset", command="reset"))
        assert reset.frame.type == "stream.snapshot"
        assert reset.frame.epoch == 1
        assert reset.frame.seq > step.frame.seq
        assert reset.frame.poses[0][3] == pytest.approx(1.5)
        joint = next(j for j in manifest.joints if j.type == "revolute")
        joint_state = await source.query_joint_state(joint.entityKey)
        assert joint_state.positions == pytest.approx([-0.7853981634], abs=1e-6)
        with pytest.raises(KeyError):
            await source.read_mesh("unknown-mesh")
        resume = await source.execute(Command(requestId="resume", command="resume"))
        assert resume.frame.seq > reset.frame.seq
        assert resume.state.state == "RUNNING"
        assert source.stats()["readbackMode"] == (
            "cpu-bulk" if device == "cpu" else "cuda-staged-pinned-double-buffer"
        )
        pid = source.stats()["runnerPID"]
    finally:
        await asyncio.gather(source.close(), source.close())
    assert pid not in [child.pid for child in mp.active_children()]
    assert not source.stats()["readerThreadsAlive"]


async def test_terminated_child_reports_failed_without_blocking_event_loop():
    source = NewtonSource("crash-test")
    try:
        await source.start("falling-boxes", SessionOptions())
        os.kill(source.stats()["runnerPID"], signal.SIGTERM)

        async def failed_state():
            async for event in source.events():
                if isinstance(event, SessionState) and event.state == "FAILED":
                    return event

        ticks = 0

        async def responsive_work():
            nonlocal ticks
            for _ in range(5):
                await asyncio.sleep(0.005)
                ticks += 1

        failed, _ = await asyncio.wait_for(asyncio.gather(failed_state(), responsive_work()), timeout=5)
        assert ticks == 5
        assert failed.cause.kind == "error"
        assert source.latest_frame() is not None
        with pytest.raises(RuntimeError, match="unavailable"):
            await source.execute(Command(requestId="after-crash", command="pause"))
    finally:
        await source.close()

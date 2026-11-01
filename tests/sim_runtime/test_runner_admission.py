"""Real Runner/Pipe/Source/Pipeline intake with a controlled slow solver seam."""

import asyncio
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

pytest.importorskip("warp")
pytestmark = pytest.mark.newton

from apps.api.app.sources.newton_source import NewtonSource
from packages.info.pipeline import ActionPipeline
from packages.protocol.schema import Actor, Command, SceneManifest, SessionOptions, TransformFrame
from packages.sim_runtime.info_host import NewtonInfoHost
from packages.sim_runtime.pacer import Pacer
from packages.sim_runtime.runner import _Runner
from packages.transport import PipeWriter, create_transport
from tests.fixtures.cell_passage import build_workcell_host
from tests.sim_runtime.test_info_host_intake import BufferedPort
from tests.unit.test_cell_passage import execute, invoke


@asynccontextmanager
async def wired_runner(tmp_path, monkeypatch, step_seconds, *, prepare=None, control_tick_every=1):
    host, _ = build_workcell_host(tmp_path)
    port = prepare(host) if prepare else None
    parent, child = create_transport()
    entered, release, handled = (threading.Event() for _ in range(3))
    runner = object.__new__(_Runner)
    runner.transport = child
    runner.options = SessionOptions(physicsDt=0.01, realtimeFactor=None)
    runner.session_id = "test"
    runner.manifest = SceneManifest(sessionId="test", sceneRevision="r", revisionNumericId=1,
                                   sceneName="fixture", source={"kind": "newton", "physicsDt": .01},
                                   nodes=[])
    runner.control = PipeWriter(child.control)
    runner.frames = SimpleNamespace(stopped=threading.Event())
    runner.extractor = SimpleNamespace(error=None)
    runner.pacer = Pacer(physics_dt=.01, realtime_factor=None, max_batch=4)
    runner._steps = runner._metric_steps = runner._heartbeat_seq = 0
    runner._metric_clock = runner._heartbeat_at = time.monotonic()
    runner._render_viewer = lambda now: None
    runner.stats = lambda: {}
    runner.seq = 0
    runner.sim = SimpleNamespace(sim_time=host.sim_time, paused=False, physics_dt=.01, epoch=0)
    runner.sim.pause = lambda: setattr(runner.sim, "paused", True)
    runner.sim.resume = lambda: setattr(runner.sim, "paused", False)
    runner.info = object.__new__(NewtonInfoHost)
    runner.info.host, runner.info.port, runner.info.sim = host, port, runner.sim
    runner.actuator = port
    runner.info.catalog, runner.info.emit = host.catalog, runner._info_events
    runner.info.timings, runner.info.tick_ms = deque(maxlen=10), 0
    admission = []
    invoke = runner.info.invoke

    def observed_invoke(request, **kwargs):
        started = time.monotonic()
        result = invoke(request, **kwargs)
        admission.append((started, time.monotonic(), result.status))
        handled.set()
        return result

    runner.info.invoke = observed_invoke

    def step(count):
        for _ in range(count):
            if not entered.is_set():
                entered.set()
                assert release.wait(2), "Intake was not synchronized with the first physics step"
            time.sleep(step_seconds)
            runner.sim.sim_time += .01
            if round((runner.sim.sim_time - host_start) / .01) % control_tick_every == 0:
                runner.info.tick(runner.sim.sim_time, .01 * control_tick_every)

    host_start = host.sim_time
    runner.sim.step = step

    def frame(kind="transform.frame", *, reliable=False):
        runner.seq += 1
        return TransformFrame(type=kind, sessionId="test", sceneRevision="r", epoch=0, seq=runner.seq,
                              simTime=runner.sim.sim_time, sampleTimeMs=0, poses=[])

    runner._frame = frame
    # Only fixture Catalog loading is substituted; actual ready validation,
    # two-way PipeWriter, Source correlation and action admission remain real.
    monkeypatch.setattr("packages.sim_runtime.info_catalog.load_info_catalog", lambda *a, **kw: host.catalog)
    source = NewtonSource("test")
    source.options = runner.options.model_copy()
    runner.rpc_fence = source._rpc_fence
    source.scene_id, source._loop = "fixture", asyncio.get_running_loop()
    source._transport, source._writer = parent, PipeWriter(parent.control)
    source._ready = source._loop.create_future()
    reader = threading.Thread(target=source._read_control, daemon=True)
    worker = threading.Thread(target=runner.run, daemon=True)
    reader.start()
    worker.start()
    await asyncio.wait_for(source._ready, 2)
    source._starting = False
    session = SimpleNamespace(source=source, epoch=0, sim_time=0.0, session_id="test", state="RUNNING",
                              options=SessionOptions(), supports_plans=False,
                              approvals=SimpleNamespace(for_invocation=lambda token: None),
                              plans=SimpleNamespace(invocation=lambda *args: None))
    pipeline = ActionPipeline(session, host.catalog, source._info_store, lambda update: None)
    for active in host.snapshot().invocations:
        pipeline.table.apply(active)
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        yield source, runner, pipeline, release, handled, admission
    finally:
        release.set()
        await source._request("close", timeout=4)
        await asyncio.to_thread(worker.join, 4)
        source._stop_readers.set()
        await asyncio.to_thread(reader.join, .3)
        await asyncio.gather(*pipeline._cleanup_tasks, return_exceptions=True)
        source._writer.close()
        runner.control.close(drain=True)
        parent.close()
        child.close()
        assert not worker.is_alive()


async def dispatch_control(source, pipeline, release):
    request = SimpleNamespace(requestId="control", entityKey="/workcell", action="requestControl",
                              params={"owner": "auto", "epoch": 0, "operationId": "control",
                                      "nextOwner": "human", "mode": "manual"})
    started = time.monotonic()
    task = asyncio.create_task(pipeline.invoke(request, "operator", Actor()))
    # Ensure the actual invoke packet is offered and sent before slow physics
    # proceeds, rather than testing a request that happens to miss the batch.
    while not source._pending:
        await asyncio.sleep(0)
    await asyncio.to_thread(source._writer.queue.join)
    release.set()
    return await task, time.monotonic() - started


@pytest.mark.asyncio
async def test_action_pipeline_admits_between_slow_physics_steps(tmp_path, monkeypatch):
    async with wired_runner(tmp_path, monkeypatch, .16) as fixture:
        source, runner, pipeline, release, handled, trace = fixture
        result, elapsed = await dispatch_control(source, pipeline, release)
        assert await asyncio.to_thread(handled.wait, 2)
        assert (result.status, runner.info.host.values()["/workcell"]["controlEpoch"]) == ("accepted", 1), (
            result.error, trace, elapsed,
        )
        assert elapsed < .5
        assert runner._steps == pytest.approx(runner.sim.sim_time / .01)


def carried_liquid(host, *, running):
    port = BufferedPort()
    host.physics = port
    execute(host, port, "reserve", "res", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    if running:
        invoke(host, "aspirate", "asp", vessel="source", volumeUl=100)
        host.tick(host.sim_time + .1, .1)
        port.q["pump"], port.velocities["pump"] = .0004, .001
        host.tick(host.sim_time + .1, .1)
    else:
        execute(host, port, "aspirate", "asp", vessel="source", volumeUl=40)
        invoke(host, "wait", "queued", seconds=10)
    port.actual_q.update(port.q)
    port.actual_qd.update(port.velocities)
    port.commit()
    return port


@pytest.mark.asyncio
async def test_timed_out_cancel_remains_a_late_safety_intent_with_net_settlement(tmp_path, monkeypatch):
    async with wired_runner(
        tmp_path, monkeypatch, .56, prepare=lambda h: carried_liquid(h, running=True),
    ) as fixture:
        source, runner, pipeline, release, handled, trace = fixture
        task = asyncio.create_task(pipeline.cancel("asp", "operator"))
        while not source._pending:
            await asyncio.sleep(0)
        await asyncio.to_thread(source._writer.queue.join)
        release.set()
        unconfirmed = await task
        assert unconfirmed.status == "failed" and unconfirmed.error.code == "source_unavailable"
        await source._request("program_execution", key="/workcell", timeout=3)
        port = runner.actuator
        assert port.native_targets["pump"] == .0004
        assert port.welds == {("/pipette", "/tip1")}
        assert runner.info.host.values()["/workcell"]["inFlightUl"] == pytest.approx(40)
        target_time = runner.sim.sim_time + .01
        port.actual_q["pump"] = .00045
        async with asyncio.timeout(3):
            while runner.sim.sim_time < target_time - 1e-9:
                await asyncio.sleep(.01)
        assert runner.info.host.values()["/workcell"]["inFlightUl"] == pytest.approx(45)
        target_time = runner.sim.sim_time + .01
        port.actual_q["pump"], port.actual_qd["pump"] = .0004, 0
        async with asyncio.timeout(3):
            while runner.sim.sim_time < target_time - 1e-9:
                await asyncio.sleep(.01)
        values = runner.info.host.values()["/workcell"]
        assert values["inFlightUl"] == pytest.approx(40)
        assert port.welds == {("/pipette", "/tip1")}
        stopped = next(x for x in runner.info.snapshot().invocations if x.invocationId == "asp")
        assert stopped.status == "cancelled"
        assert pipeline.table.get("asp").status == "failed"  # Never resurrect a gateway terminal.


@pytest.mark.asyncio
async def test_timed_out_revoke_still_cancels_only_the_queued_old_invocation(tmp_path, monkeypatch):
    async with wired_runner(
        tmp_path, monkeypatch, .56, prepare=lambda h: carried_liquid(h, running=False), control_tick_every=2,
    ) as fixture:
        source, runner, pipeline, release, handled, trace = fixture
        task = asyncio.create_task(source.revoke_before_start("queued"))
        while not source._pending:
            await asyncio.sleep(0)
        await asyncio.to_thread(source._writer.queue.join)
        release.set()
        with pytest.raises(TimeoutError):
            await task
        await source._request("program_execution", key="/workcell", timeout=3)
        old = next(x for x in runner.info.snapshot().invocations if x.invocationId == "queued")
        assert old.status == "cancelled" and old.startedSimTime is None
        assert runner.info.host.values()["/workcell"]["inFlightUl"] == pytest.approx(40)
        assert runner.actuator.welds == {("/pipette", "/tip1")}


@pytest.mark.asyncio
async def test_timed_out_step_cannot_later_apply_rejected_control(tmp_path, monkeypatch):
    async with wired_runner(tmp_path, monkeypatch, .56) as fixture:
        source, runner, pipeline, release, handled, trace = fixture
        result, elapsed = await dispatch_control(source, pipeline, release)
        assert result.status == "rejected" and result.error.code == "source_unavailable"
        # Cross the solver boundary and observe what the actual Runner does
        # with the already expired private invoke, including later cleanup.
        await source._request("program_execution", key="/workcell", timeout=3)
        assert runner.info.host.values()["/workcell"]["controlEpoch"] == 0, (trace, elapsed)
        assert not runner.info.host.values()["/workcell"]["materialSnapshot"]["controlRequests"]
        assert pipeline.table.get(result.invocationId).status == "rejected"
        assert not source._failed  # Cleanup of a never-admitted call must not kill the Runner.


@pytest.mark.asyncio
async def test_cancelled_gateway_dispatch_revokes_unexpired_private_admission(tmp_path, monkeypatch):
    async with wired_runner(tmp_path, monkeypatch, .16) as fixture:
        source, runner, pipeline, release, handled, trace = fixture
        task = asyncio.create_task(dispatch_control(source, pipeline, release))
        while not release.is_set():
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await source._request("program_execution", key="/workcell", timeout=2)
        assert runner.info.host.values()["/workcell"]["controlEpoch"] == 0
        assert not trace


@pytest.mark.asyncio
async def test_manual_step_services_intake_without_losing_requested_steps(tmp_path, monkeypatch):
    async with wired_runner(tmp_path, monkeypatch, .16) as fixture:
        source, runner, pipeline, release, handled, trace = fixture
        pause_task = asyncio.create_task(source.execute(Command(requestId="pause", command="pause")))
        while not source._pending:
            await asyncio.sleep(0)
        await asyncio.to_thread(source._writer.queue.join)
        release.set()
        paused = await pause_task
        entered, proceed = threading.Event(), threading.Event()
        step = runner.sim.step

        def manual_step(count):
            if not entered.is_set():
                entered.set()
                assert proceed.wait(2)
            step(count)

        runner.sim.step = manual_step
        command = asyncio.create_task(
            source.execute(Command(requestId="four", command="step", args={"count": 4}))
        )
        assert await asyncio.to_thread(entered.wait, 2)
        result, elapsed = await dispatch_control(source, pipeline, proceed)
        completed = await command
        assert result.status == "accepted", (result.error, trace)
        assert elapsed < .5
        assert completed.ack.simTime == pytest.approx(paused.ack.simTime + .04)
        assert runner._steps == pytest.approx(runner.sim.sim_time / .01)

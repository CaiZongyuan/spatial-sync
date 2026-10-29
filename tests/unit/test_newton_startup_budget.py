"""The real Source startup watchdog with tiny spawned, engine-free workers."""

import asyncio
import math
import multiprocessing as mp
import time

import pytest

from apps.api.app.session_manager import default_source_factory
from apps.api.app.sources.newton_source import NewtonSource
from packages.info.catalog import Catalog
from packages.protocol.schema import (
    EntitySnapshot,
    InvokeRequest,
    SceneManifest,
    SessionOptions,
    TransformFrame,
)


def startup_worker(transport, session_id, scene_id, options, *args, **kwargs):
    def ready():
        manifest = SceneManifest(
            sessionId=session_id, sceneRevision="ready", revisionNumericId=1, sceneName=scene_id,
            source={"kind": "newton", "physicsDt": options["physicsDt"]}, nodes=[],
        )
        catalog = Catalog(sceneRevision="ready")
        transport.control.send({
            "kind": "ready", "manifest": manifest.model_dump(), "catalog": catalog.model_dump(mode="json"),
            "alertRules": {}, "entitySnapshot": EntitySnapshot(sceneRevision="ready", epoch=0,
                                                                  sampleTimeMs=0).model_dump(),
            "snapshot": TransformFrame(type="stream.snapshot", sessionId=session_id, sceneRevision="ready",
                                       epoch=0, seq=1, simTime=0, sampleTimeMs=0, poses=[]).model_dump(),
        })

    try:
        if scene_id == "delayed-ready":
            time.sleep(.03)
            ready()
        while True:
            if transport.control.poll(.1):
                packet = transport.control.recv()
                if packet["kind"] == "close":
                    if scene_id == "no-ready":
                        continue  # An initializer stuck before its command loop must be terminated.
                    if scene_id == "late-ready-on-close":
                        ready()
                    transport.control.send({"kind": "closed", "token": packet["token"]})
                    break
    finally:
        transport.close()


def accelerated_watchdog(source, monkeypatch):
    observed = []
    real_wait = asyncio.wait_for

    async def wait(awaitable, timeout):
        if awaitable is source._ready:
            observed.append(timeout)
            return await real_wait(awaitable, .03)
        return await real_wait(awaitable, timeout)

    monkeypatch.setattr(asyncio, "wait_for", wait)
    return observed


def assert_closed(source):
    assert source._closed and source._process is None
    assert not source._pending
    assert not any(source._rpc_fence.slots)
    assert not any(thread.is_alive() for thread in source._threads)
    assert source._metrics["runnerPID"] not in {child.pid for child in mp.active_children()}


@pytest.mark.asyncio
async def test_default_startup_watchdog_is_180_and_timeout_has_phase_type_and_budget(monkeypatch):
    monkeypatch.setattr("packages.sim_runtime.runner.run_runner", startup_worker)
    source = NewtonSource("no-ready")
    budgets = accelerated_watchdog(source, monkeypatch)
    with pytest.raises(TimeoutError, match=r"TimeoutError.*phase=await_ready.*budget=180.*elapsed="):
        await source.start("no-ready", SessionOptions(device="cpu"))
    assert budgets[0] == pytest.approx(180, abs=.1)
    assert_closed(source)


@pytest.mark.asyncio
async def test_late_ready_cannot_initialize_a_timed_out_closing_source(monkeypatch):
    monkeypatch.setattr("packages.sim_runtime.runner.run_runner", startup_worker)
    source = NewtonSource("late-ready")
    accelerated_watchdog(source, monkeypatch)
    with pytest.raises(TimeoutError):
        await source.start("late-ready-on-close", SessionOptions(device="cpu"))
    await asyncio.sleep(0)
    assert source.latest_frame() is None
    assert not hasattr(source, "manifest")
    assert_closed(source)


@pytest.mark.asyncio
async def test_explicit_startup_budget_allows_delayed_ready_and_does_not_change_intake(monkeypatch):
    monkeypatch.setattr("packages.sim_runtime.runner.run_runner", startup_worker)
    source = NewtonSource("delayed", startup_timeout_seconds=10)
    try:
        manifest, _ = await source.start("delayed-ready", SessionOptions(device="cpu"))
        assert manifest.sessionId == "delayed" and source.latest_frame() is not None
        observed = []

        async def rpc(kind, *, timeout, **payload):
            observed.append((kind, timeout))

        with monkeypatch.context() as patch:
            patch.setattr(source, "_request", rpc)
            await source.invoke(InvokeRequest(
                invocationId="probe", requestId="probe", entityKey="/unknown", action="probe",
                epoch=0, createdSimTime=0, createdAtMs=0,
            ))
            await source.cancel("probe")
            await source.revoke_before_start("probe")
        assert observed == [("invoke", .5), ("cancel", .5), ("revoke_before_start", .5)]
    finally:
        await source.close()
    assert_closed(source)


@pytest.mark.parametrize("budget", [0, -1, math.inf, math.nan, 1801])
def test_startup_budget_is_finite_positive_and_bounded(budget):
    with pytest.raises(ValueError, match="startup"):
        NewtonSource("invalid", startup_timeout_seconds=budget)


@pytest.mark.asyncio
async def test_owned_factory_env_is_explicit_and_other_sources_keep_default(monkeypatch):
    monkeypatch.setenv("SPATIAL_NEWTON_STARTUP_TIMEOUT_SECONDS", "600")
    owned = default_source_factory("newton", "owned")
    other = NewtonSource("default")
    monkeypatch.delenv("SPATIAL_NEWTON_STARTUP_TIMEOUT_SECONDS")
    default = default_source_factory("newton", "without-env")
    try:
        assert owned.startup_timeout_seconds == 600
        assert other.startup_timeout_seconds == 180
        assert default.startup_timeout_seconds == 180
    finally:
        await owned.close()
        await other.close()
        await default.close()

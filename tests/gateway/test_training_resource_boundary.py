"""Slow sources cannot delay node liveness or bypass shared resource admission."""

import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException
from test_training_jobs import HEARTBEAT, NODE, TOKEN, heartbeat

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.internal import SessionOptions
from tests.fixtures.training_task import task_archive, training_plan


async def approved_plan(http):
    task = await http.post(
        "/api/v1/tasks", content=task_archive(), headers={"Content-Type": "application/x-tar"}
    )
    assert task.status_code == 201, task.text
    plan = (await http.post("/api/v1/plans", json=training_plan())).json()
    approved = await http.post(
        f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    return plan, task.json()


async def test_heartbeats_cancel_and_actual_reaper_remain_responsive_during_slow_cpu_start():
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowCPU(SyntheticSource):
        configure_scene = None

        async def start(self, scene_id, options):
            entered.set()
            await release.wait()
            return await super().start("lab-demo", options)

    app = create_app(
        Settings(node_token=TOKEN, recordings_enabled=False),
        source_factory=lambda kind, identity: SlowCPU(identity),
    )
    manager, jobs = app.state.manager, app.state.jobs
    clock = [100.0]
    jobs.clock = lambda: clock[0]
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
            plan, task = await approved_plan(http)
            body = heartbeat(plan, task)
            assert (await http.post(HEARTBEAT, headers=NODE, json=body)).status_code == 200
            job = (await http.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42})).json()
            body["reports"] = [{"jobId": job["jobId"], "leaseId": job["leaseId"], "status": "running"}]
            creation = asyncio.create_task(
                manager.create("falling-boxes", "newton", SessionOptions(device="cpu"))
            )
            try:
                await asyncio.wait_for(entered.wait(), 5)
                # Advance three simulated minutes while the real background reaper also runs.
                for instant in range(100, 281, 30):
                    clock[0] = instant
                    response = await asyncio.wait_for(http.post(HEARTBEAT, headers=NODE, json=body), 0.5)
                    assert response.status_code == 200 and not response.json()["assignments"][0]["cancel"]
                    assert jobs.get(job["jobId"])["status"] == "running"
                    await asyncio.sleep(0.2)
                cancelled = await asyncio.wait_for(http.post(f"/api/v1/jobs/{job['jobId']}/cancel"), 0.5)
                assert cancelled.status_code == 200 and cancelled.json()["cancelRequested"]
                assert not creation.done()

                # A genuinely missing heartbeat still fails and retains physical ownership.
                clock[0] = 341
                lost = (await http.get(f"/api/v1/jobs/{job['jobId']}")).json()
                assert lost["status"] == "failed" and lost["reason"] == "node_lost"
                assert jobs.nodes["local"]["leases"]["gpu-fixture"]["quarantined"]
                started = time.monotonic()
                logs = await http.get(f"/api/v1/jobs/{job['jobId']}/logs", params={"timeoutSeconds": 0.2})
                assert not logs.json()["complete"] and time.monotonic() - started >= 0.15
                body["reports"][0].update(status="cleaned", logs=["last owned output"])
                assert (await http.post(HEARTBEAT, headers=NODE, json=body)).status_code == 200
                logs = (await http.get(f"/api/v1/jobs/{job['jobId']}/logs")).json()
                assert logs["complete"] and logs["lines"] == ["last owned output"]
            finally:
                release.set()
                await asyncio.wait_for(creation, 5)


@pytest.mark.parametrize("device", ["cpu", "cuda:0"])
@pytest.mark.parametrize("phase", ["starting", "reload", "failed_cleanup"])
async def test_starting_reload_and_unclean_sources_keep_their_resource_reservation(device, phase):
    entered, release = asyncio.Event(), asyncio.Event()
    block, fail_cleanup = phase == "starting", phase == "failed_cleanup"

    class ControlledSource(SyntheticSource):
        configure_scene = None

        async def start(self, scene_id, options):
            if fail_cleanup:
                raise RuntimeError("injected source failure")
            if block:
                entered.set()
                await release.wait()
            return await super().start("lab-demo", options)

        async def close(self):
            if fail_cleanup:
                raise RuntimeError("injected unconfirmed cleanup")
            await super().close()

    app = create_app(
        Settings(node_token=TOKEN, recordings_enabled=False),
        source_factory=lambda kind, identity: ControlledSource(identity),
    )
    manager = app.state.manager
    operation = None
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
            try:
                if phase == "failed_cleanup":
                    with pytest.raises(HTTPException):
                        await manager.create("falling-boxes", "newton", SessionOptions(device=device))
                    assert manager.platform_sessions()[0]._starting_source is not None
                elif phase == "reload":
                    session = await manager.create("falling-boxes", "newton", SessionOptions(device=device))
                    await session.close()
                    block = True
                    operation = asyncio.create_task(manager.reload(session.session_id))
                    await asyncio.wait_for(entered.wait(), 5)
                else:
                    operation = asyncio.create_task(
                        manager.create("falling-boxes", "newton", SessionOptions(device=device))
                    )
                    await asyncio.wait_for(entered.wait(), 5)
                plan, task = await approved_plan(http)
                await http.post(HEARTBEAT, headers=NODE, json=heartbeat(plan, task))
                response = await http.post("/api/v1/jobs", json={"planId": plan["id"], "seed": 42})
                assert response.status_code == 202, response.text
                job = response.json()
                assert bool(job["leaseId"]) == (device == "cpu")
                if device != "cpu":
                    assert job["reason"] == "session_gpu_busy"
            finally:
                fail_cleanup = False
                release.set()
                if operation:
                    await asyncio.wait_for(operation, 5)

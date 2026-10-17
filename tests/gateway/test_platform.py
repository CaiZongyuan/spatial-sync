"""Platform reads are cached; native Viewer shares the Session lifecycle lock."""

import asyncio
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.app import platform_monitor as monitor_module
from apps.api.app.main import create_app
from apps.api.app.platform_monitor import platform_snapshot, session_rows, unavailable_system
from apps.api.app.session_manager import SessionManager
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.internal import SessionOptions


class FakeSampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Fake sampler; no host probes"))


class FakeNewton(SyntheticSource):
    async def start(self, scene_id, options):
        return await super().start("lab-demo", options)

    def stats(self):
        return {
            "runnerPID": 123, "runnerAlive": self._state != "STOPPED", "device": "cpu",
            "viewer": {"status": "not_started", "url": None, "clientCount": 0, "reason": None},
        }

    async def open_viewer(self):
        return {"status": "ready", "url": "http://127.0.0.1:12345/", "backendPid": 123}


def make_app(**kwargs):
    return create_app(platform_sampler=FakeSampler(), **kwargs)


def test_platform_reads_cache_without_waiting_for_or_starting_probes():
    started, release = threading.Event(), threading.Event()
    calls = []

    class BlockingSampler:
        def sample(self, rows):
            calls.append(threading.get_ident())
            started.set()
            assert release.wait(3)
            return platform_snapshot(rows, unavailable_system("released"))

    app = create_app(platform_sampler=BlockingSampler())
    try:
        with TestClient(app) as client:
            assert started.wait(2)
            original = client.get("/api/v1/platform").json()
            for _ in range(10):
                response = client.get("/api/v1/platform")
                assert response.status_code == 200 and response.json() == original
            assert len(calls) == 1
            assert original["summary"]["totalSessions"] == 0
            assert original["system"]["cpu"]["percent"] is None
            release.set()
    finally:
        release.set()
    assert app.state.platform_monitor._task is None
    assert app.state.platform_monitor._worker is None


def test_viewer_public_statuses_and_existing_remote_protections():
    with TestClient(make_app(source_factory=lambda kind, sid: FakeNewton(sid))) as client:
        assert client.post("/api/v1/sessions/missing/viewer").status_code == 404
        synthetic = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        assert client.post(f"/api/v1/sessions/{synthetic['sessionId']}/viewer").status_code == 400
        client.delete(f"/api/v1/sessions/{synthetic['sessionId']}")
        session = client.post(
            "/api/v1/sessions", json={"sceneId": "falling-boxes", "source": "newton"}
        ).json()
        route = f"/api/v1/sessions/{session['sessionId']}"
        expected = {"status": "ready", "url": "http://127.0.0.1:12345/", "backendPid": 123}
        assert client.post(route + "/viewer").json() == expected
        assert client.post(route + "/viewer").json() == expected
        paused = client.post(route + "/commands", json={
            "type": "command.request", "requestId": "pause", "command": "pause",
        })
        assert paused.status_code == 200
        assert client.post(route + "/viewer").json() == expected
        client.delete(route)
        stopped = client.post(route + "/viewer")
        assert stopped.status_code == 409 and stopped.json()["detail"]["message"]
        # Stopped sessions retain their established health representation.
        health = client.get("/api/v1/health").json()
        assert health["sessionCount"] == 2
        assert all(row["state"] == "STOPPED" for row in health["sessions"])

    settings = Settings(remote=True, operator_token="operator", viewer_token="viewer",
                        allowed_origins=("http://allowed.test",), remote_requests_per_minute=4)
    with TestClient(make_app(settings=settings)) as client:
        assert client.get("/api/v1/platform").status_code == 401
        viewer = {"Authorization": "Bearer viewer", "Origin": "http://allowed.test"}
        assert client.get("/api/v1/platform", headers=viewer).status_code == 200
        assert client.post("/api/v1/sessions/missing/viewer", headers=viewer).status_code == 403
        operator = {"Authorization": "Bearer operator", "Origin": "http://allowed.test"}
        denied = client.post("/api/v1/sessions/missing/viewer", headers=operator)
        assert denied.status_code == 403 and denied.json()["detail"]["code"] == "viewer_local_only"
        bad_origin = {**operator, "Origin": "http://bad.test"}
        assert client.get("/api/v1/platform", headers=bad_origin).status_code == 403
        assert client.get("/api/v1/platform", headers=operator).status_code == 200
        assert client.get("/api/v1/platform", headers=operator).status_code == 200
        assert client.get("/api/v1/platform", headers=operator).status_code == 429


async def test_loading_is_visible_without_publishing_an_unready_session_and_create_remains_exclusive():
    started, release = asyncio.Event(), asyncio.Event()
    sources = []

    class DelayedNewton(FakeNewton):
        async def start(self, scene_id, options):
            result = await super().start(scene_id, options)
            started.set()
            await release.wait()
            return result

    def factory(kind, sid):
        source = DelayedNewton(sid)
        sources.append(source)
        return source

    manager = SessionManager(Settings(recordings_enabled=False), factory)
    first = asyncio.create_task(manager.create("falling-boxes", "newton", SessionOptions()))
    await started.wait()
    pending = session_rows(manager)
    assert len(pending) == 1 and pending[0]["state"] == "LOADING"
    assert pending[0]["backend"]["pid"] == 123 and pending[0]["backend"]["alive"] is True
    assert manager.sessions == {}
    second = asyncio.create_task(manager.create("falling-boxes", "newton", SessionOptions()))
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    try:
        session = await first
        assert await second is session and len(sources) == 1
        assert len(session_rows(manager)) == 1 and manager._starting_session is None
    finally:
        await manager.close()


async def test_http_platform_exposes_loading_while_existing_health_and_session_routes_wait(monkeypatch):
    monkeypatch.setattr(monitor_module, "INTERVAL_SECONDS", 0.01)
    started, release = asyncio.Event(), asyncio.Event()

    class DelayedNewton(FakeNewton):
        async def start(self, scene_id, options):
            result = await super().start(scene_id, options)
            started.set()
            await release.wait()
            return result

    app = make_app(
        settings=Settings(recordings_enabled=False), source_factory=lambda kind, sid: DelayedNewton(sid)
    )
    monitor, manager = app.state.platform_monitor, app.state.manager
    await monitor.start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        creation = asyncio.create_task(client.post(
            "/api/v1/sessions", json={"sceneId": "falling-boxes", "source": "newton"}
        ))
        await started.wait()
        try:
            for _ in range(100):
                response = (await client.get("/api/v1/platform")).json()
                if response["sessions"]:
                    break
                await asyncio.sleep(0.002)
            assert response["summary"]["newtonBackends"] == 1
            pending = response["sessions"][0]
            assert pending["state"] == "LOADING"
            assert (await client.get("/api/v1/health")).json()["sessionCount"] == 0
            assert (await client.get(f"/api/v1/sessions/{pending['sessionId']}")).status_code == 404
            release.set()
            assert (await creation).status_code == 201
        finally:
            release.set()
            await creation
            await monitor.close()
            await manager.close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_initial_creation_cleans_staging_and_runner(cancel):
    started, release = asyncio.Event(), asyncio.Event()
    sources = []

    class FailedNewton(FakeNewton):
        async def start(self, scene_id, options):
            await super().start(scene_id, options)
            started.set()
            await release.wait()
            raise RuntimeError("injected start failure")

    def factory(kind, sid):
        source = FailedNewton(sid)
        sources.append(source)
        return source

    manager = SessionManager(Settings(recordings_enabled=False), factory)
    task = asyncio.create_task(manager.create("falling-boxes", "newton", SessionOptions()))
    await started.wait()
    if cancel:
        task.cancel()
    else:
        release.set()
    with pytest.raises(asyncio.CancelledError if cancel else Exception):
        await task
    assert manager.sessions == {} and manager._starting_session is None
    assert sources[0]._state == "STOPPED" and sources[0]._task.done()
    assert session_rows(manager) == []


async def test_native_viewer_serializes_with_reload_and_reports_source_failure():
    started, release = asyncio.Event(), asyncio.Event()
    events = []

    class ViewerNewton(FakeNewton):
        async def open_viewer(self):
            events.append("viewer-start")
            started.set()
            await release.wait()
            events.append("viewer-ready")
            return await super().open_viewer()

        async def close(self):
            events.append("source-close")
            await super().close()

    app = make_app(
        settings=Settings(recordings_enabled=False), source_factory=lambda kind, sid: ViewerNewton(sid)
    )
    manager = app.state.manager
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        session = await manager.create("falling-boxes", "newton", SessionOptions())
        route = f"/api/v1/sessions/{session.session_id}"
        viewer = asyncio.create_task(client.post(route + "/viewer"))
        await started.wait()
        reload = asyncio.create_task(client.post(route + "/reload"))
        await asyncio.sleep(0)
        assert events == ["viewer-start"]
        release.set()
        try:
            assert (await viewer).status_code == 200
            assert (await reload).status_code == 200
            assert events[:3] == ["viewer-start", "viewer-ready", "source-close"]

            async def unavailable():
                raise RuntimeError("owned runner exited")

            session.source.open_viewer = unavailable
            response = await client.post(route + "/viewer")
            assert response.status_code == 503
            assert response.json()["detail"] == {
                "code": "viewer_unavailable", "message": "owned runner exited",
            }
        finally:
            await manager.close()

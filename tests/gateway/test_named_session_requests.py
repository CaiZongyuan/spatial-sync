"""An explicit launch identity has one durable observation/cancellation channel."""

import asyncio
import time

from fastapi.testclient import TestClient
from scene_support import user_version

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource


def eventually(client, identifier, states):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = client.get("/api/v1/session-requests/" + identifier).json()
        if value["status"] in states:
            return value
        time.sleep(0.02)
    raise AssertionError(value)


def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    # A small direct-bound package isolates admission from large template pins.
    user_version(tmp_path / "library", 4)
    return Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings")


def test_idle_named_launch_is_observable_and_exact_replay_does_not_create_twice(tmp_path, monkeypatch):
    app = create_app(settings(tmp_path, monkeypatch))
    body = {"sceneVersion": "lab-demo@4", "source": "synthetic", "seed": 42,
            "requestId": "request-idle-observable"}
    with TestClient(app) as client:
        response = client.post("/api/v1/sessions", json=body)
        assert response.status_code == 202
        assert response.headers["Location"] == "/api/v1/session-requests/" + body["requestId"]
        started = eventually(client, body["requestId"], {"started"})
        repeated = client.post("/api/v1/sessions", json=body)
        assert repeated.status_code == 202 and repeated.json()["sessionId"] == started["sessionId"]
        assert client.post("/api/v1/sessions", json=body | {"seed": 43}).status_code == 409
        assert len(client.get("/api/v1/health").json()["sessions"]) == 1


def test_no_identity_keeps_existing_immediate_launch_contract(tmp_path, monkeypatch):
    with TestClient(create_app(settings(tmp_path, monkeypatch))) as client:
        response = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@4"})
        assert response.status_code == 201 and "sessionId" in response.json()


def test_named_starting_launch_can_be_cancelled_without_creating_a_replacement(tmp_path, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    sources = []

    class WaitingSource(SyntheticSource):
        async def start(self, scene_id, options):
            entered.set()
            await release.wait()
            return await super().start(scene_id, options)

    def factory(kind, identifier):
        source = WaitingSource(identifier)
        sources.append(source)
        return source

    app = create_app(settings(tmp_path, monkeypatch), source_factory=factory)
    with TestClient(app) as client:
        try:
            response = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@4",
                                                             "requestId": "request-cancel-loading"})
            assert response.status_code == 202
            client.portal.call(asyncio.wait_for, entered.wait(), 2)
            pending = client.get("/api/v1/session-requests/request-cancel-loading").json()
            assert pending["status"] == "starting"
            cancelled = client.delete("/api/v1/session-requests/request-cancel-loading").json()
            assert cancelled["status"] == "cancelling"
            client.portal.call(release.set)
            final = eventually(client, "request-cancel-loading", {"cancelled"})
            assert client.get("/api/v1/sessions/" + final["sessionId"]).json()["state"] == "STOPPED"
            assert app.state.manager.get(final["sessionId"]).cleanup_complete
            assert len(sources) == 1
        finally:
            client.portal.call(release.set)

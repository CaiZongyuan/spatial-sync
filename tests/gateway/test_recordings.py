import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app import session_manager
from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.log import read_segment


def test_default_recording_survives_session_close_with_portable_asset_paths(tmp_path):
    root = tmp_path / "logs"
    with TestClient(create_app(Settings(recordings_root=root))) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        rejected = client.post(
            route + "/actions",
            json={
                "requestId": "recorded-rejection",
                "entityKey": "/World/Lab/Centrifuge",
                "action": "start",
                "params": {"rpm": 500, "durationSeconds": 1},
            },
        )
        assert rejected.status_code == 409
        assert client.delete(route).status_code == 204
        listing = client.get("/api/v1/recordings").json()
        assert len(listing) == 1 and listing[0]["status"] == "complete"
        metadata = client.get(f"/recordings/{sid}/recording.json").json()
        assert metadata["formatVersion"] == 2 and metadata["segments"]
        manifest = client.get(f"/recordings/{sid}/manifest.json").json()
        assert "/api/v1/" not in json.dumps(manifest)
        records = read_segment(client.get(f"/recordings/{sid}/seg-000001.bin").content).records
        invocation = [
            json.loads(r.payload)
            for r in records
            if r.kind == 2 and json.loads(r.payload).get("type") == "invocation.update"
        ]
        assert invocation[-1]["requestId"] == "recorded-rejection"
        assert invocation[-1]["status"] == "rejected"


def test_static_recordings_cannot_escape_root_or_follow_symlink(tmp_path):
    root = tmp_path / "logs"
    directory = root / "session-safe"
    directory.mkdir(parents=True)
    outside = tmp_path / "private.json"
    outside.write_text('{"secret":true}')
    with TestClient(create_app(Settings(recordings_root=root))) as client:
        for path in ("../private.json", "%2e%2e%2fprivate.json", "C:/private.json", "x/../../private.json"):
            assert client.get("/recordings/session-safe/" + path).status_code == 404
        try:
            (directory / "leak.json").symlink_to(outside)
        except OSError:
            return  # Windows may not grant symlink creation.
        assert client.get("/recordings/session-safe/leak.json").status_code == 404


def test_startup_recovery_and_retention_only_touch_owned_recording_directories(tmp_path):
    root = tmp_path / "logs"
    for i in range(3):
        directory = root / f"session-{i}"
        directory.mkdir(parents=True)
        (directory / "recording.json").write_text(
            json.dumps(
                {
                    "formatVersion": 1,
                    "sessionId": directory.name,
                    "createdAt": f"2026-01-0{i + 1}",
                    "status": "complete",
                    "segments": [],
                }
            )
        )
    foreign = root / "foreign"
    foreign.mkdir()
    (foreign / "keep.txt").write_text("owned by somebody else")
    with TestClient(create_app(Settings(recordings_root=root, recordings_keep=1))) as client:
        assert [item["sessionId"] for item in client.get("/api/v1/recordings").json()] == ["session-2"]
    assert (foreign / "keep.txt").read_text() == "owned by somebody else"


def test_real_writer_open_failure_does_not_fail_session_or_device_action(tmp_path, monkeypatch):
    root = tmp_path / "logs"
    original_open = Path.open

    def fail_segment_open(path, mode="r", *args, **kwargs):
        if path.name.startswith("seg-") and mode == "wb" and path.is_relative_to(root):
            raise OSError("Injected disk full at segment open")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_segment_open)
    with TestClient(create_app(Settings(recordings_root=root))) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        route = f"/api/v1/sessions/{session['sessionId']}"
        result = client.post(
            route + "/actions",
            json={
                "requestId": "works-with-full-disk",
                "entityKey": "/World/Lab/Centrifuge",
                "action": "closeLid",
                "wait": True,
                "timeoutSeconds": 3,
            },
        )
        assert result.status_code == 200 and result.json()["status"] == "succeeded", result.text
        assert client.get(route).json()["state"] == "RUNNING"
        client.delete(route)
        metadata = client.get("/api/v1/recordings").json()[0]
        assert metadata["status"] == "incomplete" and "disk full" in metadata["error"]


def test_terminal_lost_after_writer_stops_cannot_return_stale_accepted_or_redispatch(tmp_path):
    with TestClient(create_app(Settings(recordings_root=tmp_path / "logs"))) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        body = {"requestId": "lost-terminal", "entityKey": "/World/Lab/Centrifuge", "action": "closeLid"}
        accepted = client.post(route + "/actions", json=body).json()
        assert accepted["status"] in ("accepted", "running")
        owned = client.app.state.manager.get(sid)
        # This real writer drains its admitted accepted update, then stops before
        # the device emits its terminal result; subsequent actions still run.
        owned.recording.stop_incomplete("Injected disk failure after acceptance")
        completed = client.get(route + f"/invocations/{accepted['invocationId']}?wait=true&timeoutSeconds=3")
        assert completed.json()["status"] == "succeeded"
        for i in range(1001):
            result = client.post(
                route + "/actions",
                json={
                    "requestId": f"evict-after-failure-{i}",
                    "entityKey": "/World/Lab/Centrifuge",
                    "action": "start",
                    "params": {"rpm": -1, "durationSeconds": 1},
                },
            )
            assert result.status_code == 422
        for response in (
            client.get(route + f"/invocations/{accepted['invocationId']}"),
            client.post(route + "/actions", json=body),
        ):
            assert response.status_code == 503, response.text
            assert response.json()["detail"]["code"] == "history_unavailable"
            assert response.json()["detail"]["invocationId"] == accepted["invocationId"]
        assert not owned.pipeline.table.active


def test_remote_recording_files_and_list_share_viewer_authorization(tmp_path):
    directory = tmp_path / "session-safe"
    directory.mkdir()
    (directory / "recording.json").write_text(
        json.dumps(
            {
                "formatVersion": 1,
                "sessionId": "session-safe",
                "status": "complete",
                "segments": [],
            }
        )
    )
    settings = Settings(
        recordings_root=tmp_path,
        remote=True,
        operator_token="operator",
        viewer_token="viewer",
        allowed_origins=("http://viewer.test",),
    )
    with TestClient(create_app(settings)) as client:
        for route in ("/api/v1/recordings", "/recordings/session-safe/recording.json"):
            assert client.get(route).status_code == 401
            assert client.get(route, headers={"Authorization": "Bearer viewer"}).status_code == 200
            assert (
                client.get(
                    route, headers={"Authorization": "Bearer viewer", "Origin": "http://other.test"}
                ).status_code
                == 403
            )


def recorded_messages(root, sid):
    metadata = json.loads((root / sid / "recording.json").read_bytes())
    return [
        json.loads(record.payload)
        for segment in metadata["segments"]
        for record in read_segment((root / sid / segment["file"]).read_bytes()).records
        if record.kind == 2
    ]


def test_failed_reload_records_global_loading_error_failed_and_stopped_broadcasts(tmp_path):
    calls = 0

    def factory(kind, sid):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("Injected reload startup failure")
        return SyntheticSource(sid)

    with TestClient(create_app(Settings(recordings_root=tmp_path), source_factory=factory)) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        assert client.post(route + "/reload").status_code == 503
        assert client.get(route).json()["state"] == "FAILED"
        assert client.delete(route).status_code == 204
        messages = recorded_messages(tmp_path, sid)
        assert [message["state"] for message in messages if message["type"] == "session.state"] == [
            "RUNNING",
            "STOPPING",
            "LOADING",
            "FAILED",
            "STOPPING",
            "STOPPED",
        ]
        assert [message["code"] for message in messages if message["type"] == "server.error"] == [
            "source_unavailable"
        ]


def test_normal_reload_records_old_loading_before_independent_new_revision_prefix(tmp_path):
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        replacement = client.post(route + "/reload").json()
        assert client.delete(route).status_code == 204
        messages = recorded_messages(tmp_path, sid)
        loading = [
            message
            for message in messages
            if message["type"] == "session.state" and message["state"] == "LOADING"
        ]
        assert len(loading) == 1 and loading[0]["sceneRevision"] == session["sceneRevision"]
        replaced = next(message for message in messages if message["type"] == "scene.replaced")
        assert replaced["sceneRevision"] == replacement["sceneRevision"] != session["sceneRevision"]
        metadata = json.loads((tmp_path / sid / "recording.json").read_bytes())
        assert len(metadata["segments"]) == len(metadata["revisions"]) == 2
        for segment in metadata["segments"]:
            records = read_segment((tmp_path / sid / segment["file"]).read_bytes()).records
            assert records[0].kind == 1
            assert json.loads(records[1].payload)["sceneRevision"] == segment["sceneRevision"]


def test_reload_capture_intake_waits_for_prefix_then_archives_exact_rejection(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original_freeze = session_manager.freeze_manifest
    calls = 0

    async def slow_second_capture(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
        return await original_freeze(*args)

    monkeypatch.setattr(session_manager, "freeze_manifest", slow_second_capture)
    with (
        TestClient(create_app(Settings(recordings_root=tmp_path))) as client,
        ThreadPoolExecutor(2) as executor,
    ):
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        body = {"requestId": "during-capture", "entityKey": "/World/Lab/Centrifuge", "action": "missing"}
        reload = executor.submit(client.post, route + "/reload")
        assert entered.wait(2)
        action = executor.submit(client.post, route + "/actions", json=body)
        try:
            with pytest.raises(TimeoutError):
                action.result(timeout=0.05)
        finally:
            release.set()
        assert reload.result(timeout=5).status_code == 200
        original = action.result(timeout=5)
        assert original.status_code == 404 and original.json()["error"]["code"] == "unknown_action"
        for i in range(1001):
            result = client.post(route + "/actions", json=body | {"requestId": f"capture-eviction-{i}"})
            assert result.status_code == 404
        stored = client.get(route + "/invocations/" + original.json()["invocationId"])
        assert stored.status_code == 200 and stored.json() == original.json()
        assert client.post(route + "/actions", json=body).json() == original.json()
        assert client.app.state.manager.get(sid).recording.error is None


def test_failed_reload_still_audits_subsequent_source_unavailable_rejection(tmp_path):
    calls = 0

    def factory(kind, sid):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("Injected reload startup failure")
        return SyntheticSource(sid)

    with TestClient(create_app(Settings(recordings_root=tmp_path), source_factory=factory)) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        assert client.post(route + "/reload").status_code == 503
        result = client.post(
            route + "/actions",
            json={
                "requestId": "after-failed-reload",
                "entityKey": "/World/Lab/Centrifuge",
                "action": "closeLid",
            },
        )
        assert result.status_code == 503 and result.json()["error"]["code"] == "source_unavailable"
        client.delete(route)
        updates = [
            message for message in recorded_messages(tmp_path, sid) if message["type"] == "invocation.update"
        ]
        assert updates[-1]["invocationId"] == result.json()["invocationId"]


def test_viewer_denial_after_archived_lookup_rebinds_to_reloaded_pipeline(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    settings = Settings(
        recordings_root=tmp_path,
        remote=True,
        operator_token="operator",
        viewer_token="viewer",
        remote_requests_per_minute=5000,
    )
    operator = {"Authorization": "Bearer operator"}
    viewer = {"Authorization": "Bearer viewer"}
    with TestClient(create_app(settings)) as client, ThreadPoolExecutor(2) as executor:
        session = client.post("/api/v1/sessions", headers=operator, json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        body = {
            "requestId": "archived-operator-request",
            "entityKey": "/World/Lab/Centrifuge",
            "action": "missing",
        }
        original = client.post(route + "/actions", headers=operator, json=body).json()
        for i in range(1001):
            assert (
                client.post(
                    route + "/actions", headers=operator, json=body | {"requestId": f"viewer-race-evict-{i}"}
                ).status_code
                == 404
            )
        owned = client.app.state.manager.get(sid)
        lookup = owned.recording.lookup_invocation

        async def delayed_lookup(identifier):
            if identifier == original["invocationId"]:
                entered.set()
                assert await asyncio.to_thread(release.wait, 5)
            return await lookup(identifier)

        monkeypatch.setattr(owned.recording, "lookup_invocation", delayed_lookup)
        denied = executor.submit(client.post, route + "/actions", headers=viewer, json=body)
        try:
            assert entered.wait(2)
            replacement = client.post(route + "/reload", headers=operator).json()
        finally:
            release.set()
        response = denied.result(timeout=5)
        assert response.status_code == 403 and response.json()["error"]["code"] == "not_permitted"
        client.delete(route, headers=operator)
        audit = next(
            message
            for message in recorded_messages(tmp_path, sid)
            if message.get("invocationId") == response.json()["invocationId"]
        )
        assert audit["sceneRevision"] == replacement["sceneRevision"] != session["sceneRevision"]

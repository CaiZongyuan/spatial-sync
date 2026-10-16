import json
from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
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
        assert metadata["formatVersion"] == 1 and metadata["segments"]
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

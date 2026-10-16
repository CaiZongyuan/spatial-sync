import json

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

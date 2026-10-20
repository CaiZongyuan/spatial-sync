"""Public recording identity survives writing, discovery and recovery across formats."""

import json
from hashlib import sha256

import pytest
import yaml
from fastapi.testclient import TestClient
from scene_support import ROOT, user_version

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.log import read_segment
from packages.info.recording import recording_metadata


def package_digest(directory):
    # Independent byte oracle: sorted package paths, NUL, each file's SHA256.
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    return sha256(b"".join(
        path.relative_to(directory).as_posix().encode() + b"\0" + sha256(path.read_bytes()).digest()
        for path in files
    )).hexdigest()


def test_new_recording_pins_actual_selected_scene_version_and_keeps_binary_v1(tmp_path, monkeypatch):
    user_root = tmp_path / "library"
    package = user_version(user_root, 4)
    expected_hash = package_digest(package)
    assert expected_hash != package_digest(ROOT / "library/scenes/lab-demo/1")
    original_locations = ROOT / "library/scenes/lab-demo/1/locations.yaml"
    assert (package / "locations.yaml").read_bytes() != original_locations.read_bytes()
    monkeypatch.setenv("LW_LIBRARY", str(user_root))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        created = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@4"})
        assert created.status_code == 201, created.text
        summary = created.json()
        route = "/api/v1/sessions/" + summary["sessionId"]
        action = client.post(route + "/actions", json={
            "requestId": "recording-v2-action", "entityKey": "/World/Lab/Centrifuge",
            "action": "closeLid", "wait": True,
        }).json()
        assert action["status"] == "succeeded"
        client.delete(route).raise_for_status()
        directory = "/recordings/" + summary["sessionId"]
        metadata = client.get(directory + "/recording.json").json()
        assert metadata["formatVersion"] == 2
        assert metadata["sceneId"] == "lab-demo" and metadata["sceneVersion"] == "lab-demo@4"
        assert metadata["sceneContentHash"] == summary["sceneContentHash"] == expected_hash
        assert metadata["kind"] == "session"
        listed = client.get("/api/v1/recordings").json()[0]
        assert listed["sceneVersion"] == "lab-demo@4"
        assert listed["sceneContentHash"] == metadata["sceneContentHash"]
        segment = client.get(directory + "/" + metadata["segments"][0]["file"]).content
        assert segment[:6] == b"LWLG\x01\x00"
        assert not read_segment(segment).truncated
        assert metadata["segments"][0]["propertyIndex"] == "seg-000001.index.json"
        assert client.get(directory + "/seg-000001.index.json").status_code == 200
        related = client.get("/api/v1/library/scenes/lab-demo/4").json()["recordings"]
        assert [item["sessionId"] for item in related] == [summary["sessionId"]]
        assert client.get("/api/v1/library/scenes/lab-demo/1").json()["recordings"] == []
    # Attribution remains exact after reconstructing the gateway and library.
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        listed = client.get("/api/v1/recordings").json()[0]
        assert listed["sceneVersion"] == "lab-demo@4" and listed["sceneContentHash"] == expected_hash


def metadata_v2():
    return {
        "formatVersion": 2, "protocolVersion": 2, "sessionId": "session-format",
        "sceneId": "lab-demo", "sceneVersion": "lab-demo@2", "sceneContentHash": "a" * 64,
        "kind": "session", "status": "complete", "segments": [],
    }


def test_rejected_package_reload_cannot_add_a_revision_under_the_recorded_hash(tmp_path, monkeypatch):
    package = user_version(tmp_path / "library", 4)
    pinned_hash = package_digest(package)
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        created = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@4"}).json()
        route = "/api/v1/sessions/" + created["sessionId"]
        unchanged = client.post(route + "/reload")
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["sceneContentHash"] == pinned_hash
        locations_file = package / "locations.yaml"
        locations = yaml.safe_load(locations_file.read_text(encoding="utf-8"))
        locations["locations"]["rack.A1"]["p"] = [0.91, -0.31, 0.83]
        locations_file.write_text(yaml.safe_dump(locations), encoding="utf-8")
        assert package_digest(package) != pinned_hash
        rejected = client.post(route + "/reload")
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "scene_not_launchable"
        assert client.get(route).json()["sceneRevision"] == unchanged.json()["sceneRevision"]
        client.delete(route).raise_for_status()
        directory = "/recordings/" + created["sessionId"] + "/"
        metadata = client.get(directory + "recording.json").json()
        assert metadata["formatVersion"] == 2 and metadata["sceneVersion"] == "lab-demo@4"
        assert metadata["sceneContentHash"] == pinned_hash
        assert len(metadata["revisions"]) == 2
        for revision in metadata["revisions"].values():
            manifest = client.get(directory + revision["manifest"]).json()
            site = next(site for site in manifest["sites"] if site["entityKey"] == "/World/Lab/Rack/A1")
            assert site["localPose"]["p"] == [0.61, -0.31, 0.83]


@pytest.mark.parametrize("field,value", [
    ("sceneVersion", None), ("sceneVersion", "lab-demo"), ("sceneVersion", "lab-demo@0"),
    ("sceneVersion", "other@2"), ("sceneContentHash", None), ("sceneContentHash", "bad"),
    ("kind", "unknown"), ("formatVersion", True), ("segments", [None]), ("segments", {}),
])
def test_invalid_v2_identity_is_rejected_and_not_listed(tmp_path, field, value):
    metadata = metadata_v2() | {field: value}
    with pytest.raises(ValueError):
        recording_metadata(metadata)
    directory = tmp_path / "session-format"
    directory.mkdir()
    (directory / "recording.json").write_text(json.dumps(metadata))
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        assert client.get("/api/v1/recordings").json() == []


@pytest.mark.parametrize("field", ["sceneVersion", "sceneContentHash", "kind"])
def test_v2_never_falls_back_when_required_context_is_missing(field):
    metadata = metadata_v2()
    del metadata[field]
    with pytest.raises(ValueError):
        recording_metadata(metadata)


@pytest.mark.parametrize("format_version", [1, 2])
def test_recovery_and_read_projection_preserve_format_identity(tmp_path, format_version):
    directory = tmp_path / "session-format"
    directory.mkdir()
    metadata = metadata_v2() | {"formatVersion": format_version, "status": "recording"}
    if format_version == 1:
        for field in ("kind", "sceneVersion", "sceneContentHash"):
            del metadata[field]
    # An actual retained binary v1 segment, including independent snapshots.
    fixture = ROOT / "tests/fixtures/recordings/v1"
    (directory / "seg-000001.bin").write_bytes((fixture / "seg-000001.bin").read_bytes())
    (directory / "recording.json").write_text(json.dumps(metadata))
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        listed = client.get("/api/v1/recordings").json()[0]
        assert listed["status"] == "incomplete" and listed["durationMs"] > 10000
        assert listed["sceneVersion"] == f"lab-demo@{format_version}"
        assert listed["kind"] == "session"
        raw = client.get("/recordings/session-format/recording.json").json()
        assert raw["formatVersion"] == format_version
        if format_version == 1:
            assert not {"sceneVersion", "sceneContentHash", "kind"}.intersection(raw)
        else:
            assert raw["sceneContentHash"] == "a" * 64


def test_v1_projection_does_not_rewrite_original_bytes(tmp_path):
    directory = tmp_path / "session-format"
    directory.mkdir()
    original = (b'{ "formatVersion":1, "sceneId":"lab-demo", "sessionId":"session-format", '
                b'"status":"complete", "segments":[] }\n')
    path = directory / "recording.json"
    path.write_bytes(original)
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        listed = client.get("/api/v1/recordings").json()[0]
        assert listed["sceneVersion"] == "lab-demo@1" and listed["sceneContentHash"] is None
        assert client.get("/recordings/session-format/recording.json").content == original
    assert path.read_bytes() == original


def test_live_v2_keeps_explicit_null_identity_and_optional_index_reference(tmp_path):
    directory = tmp_path / "session-format"
    directory.mkdir()
    metadata = metadata_v2() | {
        "kind": "live", "sceneId": None, "sceneVersion": None, "sceneContentHash": None,
        "segments": [{"file": "seg-000001.bin", "propertyIndex": "seg-000001.index.json"}],
    }
    (directory / "recording.json").write_text(json.dumps(metadata))
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        listed = client.get("/api/v1/recordings").json()[0]
        assert listed["kind"] == "live" and listed["sceneVersion"] is None
        assert listed["sceneContentHash"] is None
        assert client.get("/api/v1/library/scenes/lab-demo/1").json()["recordings"] == []
    for field, value in (("sceneVersion", "live@1"), ("sceneContentHash", "a" * 64)):
        with pytest.raises(ValueError):
            recording_metadata(metadata | {field: value})
    metadata["segments"][0]["propertyIndex"] = "../private.json"
    with pytest.raises(ValueError):
        recording_metadata(metadata)

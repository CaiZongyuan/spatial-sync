"""Version selection is proved through real HTTP and the Synthetic device host."""

import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
ROBOT = "/World/Lab/RobotA"


def user_version(root, rev, *, launchable=True):
    destination = root / "scenes/lab-demo" / str(rev)
    shutil.copytree(ROOT / "library/scenes/lab-demo/1", destination)
    locations = yaml.safe_load((destination / "locations.yaml").read_text(encoding="utf-8"))
    locations["locations"]["rack.A1"]["p"] = [0.61, -0.31, 0.83]
    locations["locations"]["rack.A1"]["approach"] = [0.0, 0.4, -0.7, 0.0, 0.0, 0.0, 0.0]
    (destination / "locations.yaml").write_text(yaml.safe_dump(locations), encoding="utf-8")
    (destination / "validation.json").write_text(json.dumps({
        "schema": "lw.validation/1", "subject": f"scene:lab-demo@{rev}", "launchable": launchable,
        "checks": [] if launchable else [{
            "id": "P6", "tier": "physics", "status": "fail", "message": "Fixture: taught location mismatch",
        }],
    }), encoding="utf-8")
    return destination


def test_explicit_builtin_version_preserves_the_legacy_scene_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "user-library"))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        created = client.post(
            "/api/v1/sessions", json={"sceneVersion": "lab-demo@1", "source": "synthetic"}
        )
        assert created.status_code == 201, created.text
        assert created.json()["sceneId"] == "lab-demo"
        assert created.json()["sceneVersion"] == "lab-demo@1"
        assert client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()["sessionId"] == (
            created.json()["sessionId"]
        )


def test_latest_read_and_latest_launchable_use_actual_selected_package(tmp_path, monkeypatch):
    user_root = tmp_path / "library"
    user_version(user_root, 2)
    user_version(user_root, 3, launchable=False)
    monkeypatch.setenv("LW_LIBRARY", str(user_root))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        listed = client.get("/api/v1/library/scenes", params={"q": "lab-demo"}).json()["scenes"]
        assert [scene["sceneVersion"] for scene in listed] == ["lab-demo@1", "lab-demo@2", "lab-demo@3"]
        assert max(listed, key=lambda scene: scene["rev"])["sceneVersion"] == "lab-demo@3"
        assert [scene["sceneVersion"] for scene in client.get(
            "/api/v1/library/scenes", params={"q": "lab-demo", "launchable": True}
        ).json()["scenes"]] == ["lab-demo@1", "lab-demo@2"]
        summary = client.post("/api/v1/sessions", json={"sceneId": "lab-demo", "realtimeFactor": 30}).json()
        assert summary["sceneId"] == "lab-demo" and summary["sceneVersion"] == "lab-demo@2"
        route = "/api/v1/sessions/" + summary["sessionId"]
        manifest = client.get(route + "/scene").json()
        assert next(site for site in manifest["sites"] if site["entityKey"] == "/World/Lab/Rack/A1")[
            "localPose"
        ]["p"] == [0.61, -0.31, 0.83]
        catalog = client.get(route + "/catalog").json()
        taught = catalog["entities"][ROBOT]["config"]["locations"]["locations"]["rack.A1"]
        assert taught["approach"] == [0.0, 0.4, -0.7, 0.0, 0.0, 0.0, 0.0]
        action = client.post(route + "/actions", json={
            "requestId": "selected-version-move", "entityKey": ROBOT,
            "action": "moveTo", "params": {"location": "rack.A1"}, "wait": True,
        }).json()
        assert action["status"] == "succeeded", action
        state = client.get(route + "/state", params={"keys": ROBOT}).json()
        assert state["entities"][ROBOT]["values"]["jointPositions"] == taught["approach"]
        assert client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2"}).json()["sessionId"] == (
            summary["sessionId"]
        )
        conflict = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1"})
        assert conflict.status_code == 409 and conflict.json()["detail"]["code"] == "session_exists"
        wrong_source = client.post(
            "/api/v1/sessions", json={"sceneVersion": "lab-demo@2", "source": "newton"}
        )
        assert wrong_source.status_code == 404
        client.delete(route)
        pinned = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1"}).json()
        assert pinned["sceneVersion"] == "lab-demo@1" and pinned["sessionId"] != summary["sessionId"]
        assert "newton" not in sys.modules


@pytest.mark.parametrize("body, status, code", [
    ({"sceneVersion": "lab-demo@999"}, 404, "unknown_version"),
    ({"sceneVersion": "lab-demo@3"}, 409, "scene_not_launchable"),
    ({}, 422, None),
    ({"sceneId": "lab-demo", "sceneVersion": "lab-demo@2"}, 422, None),
])
def test_invalid_choices_allocate_no_sessions_or_recordings(tmp_path, monkeypatch, body, status, code):
    user_root = tmp_path / "library"
    user_version(user_root, 3, launchable=False)
    monkeypatch.setenv("LW_LIBRARY", str(user_root))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        rejected = client.post("/api/v1/sessions", json=body)
        assert rejected.status_code == status, rejected.text
        if code:
            assert rejected.json()["detail"]["code"] == code
        if code == "scene_not_launchable":
            assert rejected.json()["detail"]["validation"]["checks"][0]["status"] == "fail"
        assert client.get("/api/v1/health").json()["sessions"] == []
        assert client.get("/api/v1/recordings").json() == []


def test_duplicate_builtin_user_id_fails_before_gateway_startup(tmp_path, monkeypatch):
    user_version(tmp_path, 1)
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path))
    with pytest.raises(ValueError, match="Conflicting scene version lab-demo@1"):
        with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))):
            pass


@pytest.mark.parametrize("change", [
    {"sources": {"synthetic": True}},
    {"sources": ["synthetic", "synthetic"]},
    {"stage": "python:../../arbitrary"},
    {"stage": "python:unknown-builder"},
    {"sources": ["newton"]},
])
def test_invalid_package_builder_or_sources_fail_before_gateway_startup(tmp_path, monkeypatch, change):
    package = user_version(tmp_path, 2)
    metadata_file = package / "scene.yaml"
    metadata = yaml.safe_load(metadata_file.read_text(encoding="utf-8"))
    metadata_file.write_text(yaml.safe_dump(metadata | change), encoding="utf-8")
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path))
    with pytest.raises(ValueError, match="sources|builder"):
        with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))):
            pass


def test_missing_validation_stays_missing_and_cannot_launch(tmp_path, monkeypatch):
    package = user_version(tmp_path, 4)
    (package / "validation.json").unlink()
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        detail = client.get("/api/v1/library/scenes/lab-demo/4").json()
        assert detail["validation"] is None and detail["validationStatus"] == "missing"
        assert detail["launchable"] is False
        rejected = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@4"})
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "scene_not_launchable"
        assert client.get("/api/v1/health").json()["sessions"] == []
        assert not (package / "validation.json").exists()


def test_editing_an_indexed_version_cannot_silently_change_its_launch_content(tmp_path, monkeypatch):
    package = user_version(tmp_path, 2)
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        locations = package / "locations.yaml"
        locations.write_text(
            locations.read_text(encoding="utf-8") + "\n# in-place change\n", encoding="utf-8"
        )
        rejected = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2"})
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "scene_not_launchable"
        assert client.get("/api/v1/health").json()["sessions"] == []
        assert client.get("/api/v1/recordings").json() == []


@pytest.mark.parametrize("change", ["location", "missing-validation"])
def test_reload_rejects_changed_package_before_disturbing_current_session(tmp_path, monkeypatch, change):
    package = user_version(tmp_path / "library", 2)
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        created = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2"}).json()
        route = "/api/v1/sessions/" + created["sessionId"]
        unchanged = client.post(route + "/reload")
        assert unchanged.status_code == 200, unchanged.text
        current = unchanged.json()
        assert current["sceneRevision"] != created["sceneRevision"]
        assert current["sceneContentHash"] == created["sceneContentHash"]
        if change == "location":
            file = package / "locations.yaml"
            locations = yaml.safe_load(file.read_text(encoding="utf-8"))
            locations["locations"]["rack.A1"]["p"] = [0.91, -0.31, 0.83]
            file.write_text(yaml.safe_dump(locations), encoding="utf-8")
        else:
            (package / "validation.json").unlink()
        rejected = client.post(route + "/reload")
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["code"] == "scene_not_launchable"
        preserved = client.get(route).json()
        assert preserved["state"] == "RUNNING" and preserved["sceneRevision"] == current["sceneRevision"]
        assert preserved["sceneContentHash"] == created["sceneContentHash"]
        manifest = client.get(route + "/scene").json()
        assert next(site for site in manifest["sites"] if site["entityKey"] == "/World/Lab/Rack/A1")[
            "localPose"
        ]["p"] == [0.61, -0.31, 0.83]
        action = client.post(route + "/actions", json={
            "requestId": "after-rejected-reload", "entityKey": "/World/Lab/Centrifuge",
            "action": "closeLid", "wait": True,
        })
        assert action.status_code == 200 and action.json()["status"] == "succeeded", action.text
        client.delete(route).raise_for_status()
        recording = client.get("/recordings/" + created["sessionId"] + "/recording.json").json()
        assert len(recording["revisions"]) == 2


def test_revision_order_is_numeric_and_read_latest_may_be_unlaunchable(tmp_path, monkeypatch):
    user_version(tmp_path, 2)
    user_version(tmp_path, 10)
    user_version(tmp_path, 11, launchable=False)
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        assert client.get("/api/v1/library/scenes/lab-demo").json()["sceneVersion"] == "lab-demo@11"
        listed = client.get("/api/v1/library/scenes", params={"q": "lab-demo"}).json()["scenes"]
        assert [version["rev"] for version in listed] == [1, 2, 10, 11]
        launched = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        assert launched["sceneVersion"] == "lab-demo@10"


def test_stale_tool_versions_keep_the_recorded_conclusion_without_rewriting_report(tmp_path, monkeypatch):
    package = user_version(tmp_path, 2)
    report_file = package / "validation.json"
    report = json.loads(report_file.read_text(encoding="utf-8"))
    report["toolVersions"] = {"python": "0.0.0"}
    report_file.write_text(json.dumps(report), encoding="utf-8")
    before = report_file.read_bytes()
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path))
    with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))) as client:
        detail = client.get("/api/v1/library/scenes/lab-demo/2").json()
        assert detail["validationStatus"] == "stale" and detail["launchable"] is True
        launched = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2"})
        assert launched.status_code == 201
        assert report_file.read_bytes() == before


@pytest.mark.parametrize("versions", [[], {"python": 313}])
def test_malformed_existing_report_fails_at_startup_not_during_discovery(tmp_path, monkeypatch, versions):
    package = user_version(tmp_path, 2)
    report_file = package / "validation.json"
    report = json.loads(report_file.read_text(encoding="utf-8"))
    report_file.write_text(json.dumps(report | {"toolVersions": versions}), encoding="utf-8")
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path))
    with pytest.raises(ValueError, match="Invalid scene validation report"):
        with TestClient(create_app(Settings(recordings_root=tmp_path / "recordings"))):
            pass

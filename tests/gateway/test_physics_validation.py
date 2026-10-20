"""Injected physical observations verify public publication and launch boundaries.

Real Newton measurement is covered by the optional manual smoke, never ordinary CI.
"""

import sys

import yaml
from fastapi.testclient import TestClient
from test_structure_validation import upload

from apps.api.app.main import create_app
from packages.library.common import BUILTIN_ROOT, LibraryError
from packages.library.physics import skipped
from packages.library.structural import validate as structure
from packages.library.validation import StructuralValidator


def package(name):
    path = BUILTIN_ROOT / "scenes/robot-and-tubes/2"
    values = {file: yaml.safe_load((path / file).read_bytes())
              for file in ("scene.yaml", "devices.yaml", "locations.yaml")}
    values["scene.yaml"]["name"] = name
    values["devices.yaml"]["scene"] = name
    values["locations.yaml"]["locations"]["rack.A1"]["grasp"][0] += .2
    return {file: yaml.safe_dump(value, sort_keys=False).encode() for file, value in values.items()}


def physical_failure():
    return {"physics": {"status": "completed", "device": "cpu", "ranAt": "2026-10-20T00:00:00Z",
                        "toolVersions": {"newton": "1.7.0"}},
            "checks": [{"id": "P6", "tier": "physics", "status": "fail", "message": "TCP error: 38 mm",
                        "entityKey": "/World/Lab/RobotA", "location": "rack.A1",
                        "measurements": {"errorM": .038, "errorMm": 38., "limitM": .01}}]}


def test_failed_physics_is_published_inspectable_and_blocks_exact_version_launch(monkeypatch):
    before = set(sys.modules)
    calls = []

    def worker(self, module, payload, timeout):
        calls.append(module)
        return structure(payload) if module == "structural" else physical_failure()

    monkeypatch.setattr(StructuralValidator, "worker", worker)
    monkeypatch.setattr("packages.library.validation.physics_availability", lambda payload: (None, {}))
    with TestClient(create_app()) as client:
        response = upload(client, package("physics-failure-fixture"))
        assert response.status_code == 200, response.text
        imported = response.json()
        assert imported["sceneVersion"] == "physics-failure-fixture@1"
        report = imported["validation"]
        assert next(row for row in report["checks"] if row["id"] == "S8")["status"] == "pass"
        assert not report["launchable"] and report["physics"]["status"] == "completed"
        detail = client.get("/api/v1/library/scenes/physics-failure-fixture/1").json()
        assert not detail["launchable"] and detail["validation"] == report
        failed = next(row for row in detail["validation"]["checks"] if row["id"] == "P6")
        assert failed["entityKey"] == "/World/Lab/RobotA" and failed["location"] == "rack.A1"
        assert failed["measurements"]["errorMm"] == 38.
        launch = client.post(
            "/api/v1/sessions", json={"sceneVersion": imported["sceneVersion"], "source": "newton"}
        )
        assert launch.status_code == 409 and launch.json()["detail"]["code"] == "scene_not_launchable"
        embedded = client.get(detail["files"]["validation.json"]).content
        monkeypatch.setattr("packages.library.validation.physics_availability",
                            lambda payload: (skipped("Newton is not installed", ["newton"]), {}))
        checked = client.post("/api/v1/library/scenes/physics-failure-fixture/1/validate")
        assert checked.status_code == 200, checked.text
        retained = checked.json()
        assert retained["physics"]["status"] == "skipped" and not retained["launchable"]
        assert retained["physics"]["retainedFrom"]["ranAt"] == report["physics"]["ranAt"]
        assert retained["contentHash"] == imported["contentHash"]
        assert next(row for row in retained["checks"] if row["id"] == "P6")["status"] == "fail"
        assert client.get(detail["files"]["validation.json"]).content == embedded
        assert calls == ["structural", "physics", "structural"]
    assert not {"newton", "warp"} & (set(sys.modules) - before)


def test_worker_timeout_publishes_neither_imported_version_nor_job(monkeypatch):
    def worker(self, module, payload, timeout):
        if module == "structural":
            return structure(payload)
        raise LibraryError("validation_unavailable", "Physics validation timed out", 503)

    monkeypatch.setattr(StructuralValidator, "worker", worker)
    monkeypatch.setattr("packages.library.validation.physics_availability", lambda payload: (None, {}))
    with TestClient(create_app()) as client:
        response = upload(client, package("physics-timeout-fixture"))
        assert response.status_code == 503 and response.json()["detail"]["code"] == "validation_unavailable"
        scenes = client.get("/api/v1/library/scenes").json()["scenes"]
        assert not any(row["sceneId"] == "physics-timeout-fixture" for row in scenes)
        assert client.get("/api/v1/library/imports").json()["imports"] == []

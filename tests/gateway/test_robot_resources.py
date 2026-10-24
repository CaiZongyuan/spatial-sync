"""Real metadata/asset reads without a Source, weight download or fabricated promotion."""

import json
import os
import sys

import pytest
import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import BUILTIN_ROOT


def test_real_fr3_binding_preview_and_registration_only_models_need_no_source(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    monkeypatch.delenv("SPATIAL_LIVE_BINDINGS", raising=False)

    def no_source(*args, **kwargs):
        raise AssertionError("Robot metadata must not create a Source")

    settings = Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings")
    imported = set(sys.modules)
    with TestClient(create_app(settings, source_factory=no_source)) as http:
        response = http.get("/api/v1/embodiments")
        assert response.status_code == 200, response.text
        values = {value["id"]: value for value in response.json()["embodiments"]}
        simulated, real = values["fr3-sim"], values["fr3-real"]
        assert simulated["asset"] == real["asset"] == "robot/fr3@1"
        assert simulated["kind"] == "sim" and real["kind"] == "real"
        assert real["connection"] == "not-connected" and real["bindings"] == []
        assert simulated["currentController"] is None and simulated["controllerHistory"] == []
        assert simulated["bindings"][0] == {
            "scene": "robot-and-tubes@2", "entity": "/World/Lab/RobotA", "source": "newton",
            "sceneContentHash": simulated["bindings"][0]["sceneContentHash"], "program": "robot_arm.newton",
            "robotModel": "fr3-taught-motion", "graspMode": "weld",
        }
        preview = http.get(simulated["visualUrl"])
        assert preview.status_code == 200 and preview.content[:4] == b"glTF"
        assert http.get("/api/v1/embodiments/fr3-sim").json() == simulated
        controller = http.get("/api/v1/controllers/fr3-arm").json()
        assert controller["currentVersion"] is None and controller["current"] is None
        assert controller["versions"] == []
        assert http.get("/api/v1/controllers/fr3-arm/versions").json() == {"versions": []}
        models = http.get("/api/v1/robot-models").json()["models"]
        vlas = [model for model in models if model["family"] == "vla"]
        assert len(vlas) >= 3
        assert all(not model["runnable"] and model["embodiments"] == [] for model in vlas)
        assert all(model["sources"] and model["license"] and model["parameters"] > 0 for model in vlas)
        taught = http.get("/api/v1/robot-models/fr3-taught-motion").json()
        assert taught["runnable"] and taught["capability"]["program"] == "robot_arm.newton"
        assert taught["quality"] == {"status": "not-evaluated", "evaluationId": None}
        assert not {"newton", "torch"} & (set(sys.modules) - imported)
        assert http.get("/api/v1/health").json()["sessionCount"] == 0
        assert http.get("/api/v1/jobs").json()["jobs"] == []
        for route in ("/embodiments/missing", "/controllers/missing", "/robot-models/missing"):
            assert http.get("/api/v1" + route).status_code == 404
        assert http.post("/api/v1/robot-models", json=taught).status_code == 405
        assert "private/" not in json.dumps(models)


@pytest.mark.parametrize("change", ["vla-runnable", "unavailable-program", "real-binding", "wrong-entity"])
def test_invalid_capability_or_binding_fails_explicitly(tmp_path, monkeypatch, change):
    root = tmp_path / "library"
    monkeypatch.setenv("LW_LIBRARY", str(root))
    if change in ("vla-runnable", "unavailable-program"):
        relative = ("robot-models/openvla-7b.yaml" if change == "vla-runnable"
                    else "robot-models/fr3-taught-motion.yaml")
    else:
        relative = "embodiments/fr3-real.yaml" if change == "real-binding" else "embodiments/fr3-sim.yaml"
    value = yaml.safe_load((BUILTIN_ROOT / relative).read_text(encoding="utf-8"))
    if change == "vla-runnable":
        value["runnable"] = True
    elif change == "unavailable-program":
        value["capability"]["program"] = "robot_arm.missing"
    elif change == "real-binding":
        value["bindings"] = [{"scene": "robot-and-tubes@2", "entity": "/World/Lab/RobotA",
                              "source": "newton"}]
    else:
        value["bindings"][0]["entity"] = "/World/Lab/NotThisRobot"
    path = root / relative
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    with TestClient(create_app(Settings(state_root=tmp_path / "state"))) as http:
        response = http.get("/api/v1/embodiments")
        assert response.status_code == 409
        assert http.get("/api/v1/health").json()["sessionCount"] == 0


def test_actual_metadata_history_requires_an_explicit_pointer_and_matching_composition(tmp_path, monkeypatch):
    # Controlled metadata exercises the read contract; it is not a promoted production version.
    root = tmp_path / "library"
    monkeypatch.setenv("LW_LIBRARY", str(root))
    directory = root / "controllers/fr3-arm"
    directory.mkdir(parents=True)
    identity = {"id": "fr3-arm", "title": "Controlled metadata", "embodiment": "fr3-sim",
                "currentVersion": None}
    version = {"controller": "fr3-arm", "version": 1, "embodiment": "fr3-sim",
               "robotModel": "fr3-taught-motion", "policy": None, "program": "robot_arm.newton",
               "action": {"space": "joint_position_targets", "hz": None}, "observation": None,
               "task": None, "scene": "robot-and-tubes@2", "evaluationId": None}
    (directory / "controller.yaml").write_text(yaml.safe_dump(identity), encoding="utf-8")
    (directory / "1.yaml").write_text(yaml.safe_dump(version), encoding="utf-8")
    with TestClient(create_app(Settings(state_root=tmp_path / "state"))) as http:
        retained = http.get("/api/v1/controllers/fr3-arm").json()
        assert retained["current"] is None and len(retained["versions"]) == 1
        identity["currentVersion"] = 1
        (directory / "controller.yaml").write_text(yaml.safe_dump(identity), encoding="utf-8")
        current = http.get("/api/v1/controllers/fr3-arm").json()["current"]
        assert current["version"] == 1 and current["evaluationId"] is None
        version["action"]["space"] = "changed-after-observation"
        (directory / "1.yaml").write_text(yaml.safe_dump(version), encoding="utf-8")
        changed = http.get("/api/v1/controllers/fr3-arm")
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "controller_version_changed"
        version["embodiment"] = "fr3-real"
        (directory / "1.yaml").write_text(yaml.safe_dump(version), encoding="utf-8")
        assert http.get("/api/v1/controllers/fr3-arm").status_code == 409


def test_linked_metadata_cannot_disclose_an_external_file(tmp_path, monkeypatch):
    root = tmp_path / "library"
    monkeypatch.setenv("LW_LIBRARY", str(root))
    outside = tmp_path / "private.yaml"
    outside.write_text("PRIVATE_RESOURCE_SENTINEL", encoding="utf-8")
    directory = root / "robot-models"
    directory.mkdir(parents=True)
    os.link(outside, directory / "linked.yaml")
    with TestClient(create_app(Settings(state_root=tmp_path / "state"))) as http:
        response = http.get("/api/v1/robot-models")
        assert response.status_code == 409
        assert "PRIVATE_RESOURCE_SENTINEL" not in response.text and str(tmp_path) not in response.text

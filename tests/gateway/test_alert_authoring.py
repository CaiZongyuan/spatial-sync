"""Scene upload/structural validation and runtime consume the same authoring rules."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from test_alerts import await_alerts, control
from test_structure_validation import upload

from apps.api.app.main import create_app

KEY = "/World/Lab/Incubator"


def declared_scene(fault):
    root = Path(__file__).resolve().parents[2]
    package = root / "library/scenes/lab-demo/2"
    files = {name: (package / name).read_bytes() for name in ("scene.yaml", "devices.yaml", "locations.yaml")}
    metadata = yaml.safe_load(files["scene.yaml"])
    metadata["name"], metadata["title"] = "alert-authoring", "Alert authoring fixture"
    bindings = yaml.safe_load(files["devices.yaml"])
    bindings["scene"] = "alert-authoring"
    incubator = next(binding for binding in bindings["entities"] if binding.get("key") == KEY)
    incubator.setdefault("config", {}).update(
        {"initialTemperatureC": 37, "setpointC": 37, "timeConstantSeconds": 1000000}
    )
    definition = yaml.safe_load((root / "devices/types/incubator.v1.yaml").read_text(encoding="utf-8"))
    deviation = definition["alerts"]["tempDeviation"] | {"forSeconds": 0.25}
    incubator["alerts"] = {
        "tempDeviation": deviation,
        "targetAbove38": {
            "when": {"property": "setpointC", "gt": 38},
            "forSeconds": 0,
            "severity": "info",
            "message": "Target exceeds 38 C",
        },
    }
    if fault == "reference":
        deviation["when"] = {"property": "temperatureC", "deviatesFrom": "missing", "by": 1}
    elif fault == "threshold":
        deviation["when"] = {"property": "temperatureC", "deviatesFrom": "setpointC", "by": True}
    elif fault == "duration":
        deviation["forSeconds"] = -1
    files["scene.yaml"] = yaml.safe_dump(metadata).encode()
    files["devices.yaml"] = yaml.safe_dump(bindings).encode()
    return files


@pytest.mark.parametrize("fault", [None, "reference", "threshold", "duration"])
def test_uploaded_binding_overrides_and_supplements_are_validated_before_public_launch(fault):
    with TestClient(create_app()) as client:
        imported = upload(client, declared_scene(fault))
        if fault == "duration":
            assert imported.status_code == 422, imported.text
            assert imported.json()["detail"]["code"] == "import_invalid"
            return
        assert imported.status_code == 200, imported.text
        job = imported.json()
        report = job["validation"]
        s5 = next(check for check in report["checks"] if check["id"] == "S5")
        if fault is not None:
            assert s5["status"] == "fail", report
            assert "Invalid binding" in s5["message"], s5
            launch = client.post("/api/v1/sessions", json={"sceneVersion": job["sceneVersion"]})
            assert launch.status_code == 409 and launch.json()["detail"]["code"] == "scene_not_launchable"
            assert client.get("/api/v1/health").json()["sessions"] == []
            return
        assert s5["status"] == "pass" and report["launchable"] is True, report
        authored = client.get("/api/v1/library/scenes/alert-authoring/1").json()
        binding = next(binding for binding in authored["entities"] if binding.get("key") == KEY)
        assert binding["alerts"]["tempDeviation"]["forSeconds"] == 0.25
        created = client.post("/api/v1/sessions", json={"sceneVersion": job["sceneVersion"], "controlHz": 20})
        assert created.status_code == 201, created.text
        sid = created.json()["sessionId"]
        route = "/api/v1/sessions/" + sid
        try:
            control(client, route, "pause")
            control(client, route, "reset")
            accepted = client.post(
                route + "/actions",
                json={
                    "requestId": "setpoint",
                    "entityKey": KEY,
                    "action": "setSetpoint",
                    "params": {"temperatureC": 40},
                },
            )
            assert accepted.status_code == 202, accepted.text
            control(client, route, "step", count=72)
            episodes = await_alerts(client, sid, 2, active=True)
            assert {episode["rule"] for episode in episodes} == {"tempDeviation", "targetAbove38"}
            deviation = next(episode for episode in episodes if episode["rule"] == "tempDeviation")
            assert deviation["since"] == pytest.approx(0.05) and deviation["simTime"] == pytest.approx(0.3)
            public = client.get(route + "/catalog").json()
            assert "alerts" not in public["entities"][KEY] and "alerts" not in public["types"]["incubator/v1"]
        finally:
            client.delete(route)

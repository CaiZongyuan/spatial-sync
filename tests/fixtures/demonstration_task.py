"""A downstream Task and actual Synthetic episode, without training or GPU."""

import uuid

import yaml

from tests.fixtures.training_task import task_directory


def demonstration_task(path, name="demonstration-probe"):
    package = task_directory(path, name)
    file = package / "task.yaml"
    metadata = yaml.safe_load(file.read_text(encoding="utf-8"))
    metadata["scene"] = metadata["evaluation"]["scene"] = "lab-demo@3"
    file.write_text(yaml.safe_dump(metadata), encoding="utf-8")
    (package / "probe.py").write_text('def register(): return "installed-source-marker"\n', encoding="utf-8")
    return package


def successful_recording(http):
    created = http.post("/api/v1/sessions", json={
        "sceneVersion": "lab-demo@3", "source": "synthetic", "realtimeFactor": 30,
    })
    created.raise_for_status()
    identifier = created.json()["sessionId"]
    route = "/api/v1/sessions/" + identifier
    try:
        action = http.post(route + "/actions", json={
            "requestId": "demo-fixture-" + uuid.uuid4().hex,
            "entityKey": "/World/Lab/RobotA", "action": "pickPlace",
            "params": {"from": "rack.A1", "to": "centrifuge.slot1"},
            "wait": True, "timeoutSeconds": 20,
        })
        assert action.is_success, action.text
        assert action.json()["status"] == "succeeded", action.text
    finally:
        http.delete(route).raise_for_status()
    return identifier

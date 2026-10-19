"""Versioned lab devices use real programs through public Synthetic HTTP."""

import json
import math
import shutil
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.schema import Command

INCUBATOR = "/World/Lab/Incubator"
CABINET = "/World/Lab/BiosafetyCabinet"
SENSOR = "/World/Lab/EnvironmentSensor"
KEYS = [INCUBATOR, CABINET, SENSOR]


class PausedSynthetic(SyntheticSource):
    async def start(self, scene_id, options):
        result = await super().start(scene_id, options)
        await self.execute(Command(requestId="fixture-pause", command="pause"))
        return result


def factory(kind, session_id):
    assert kind == "synthetic"
    source = PausedSynthetic(
        session_id, publication_clock=lambda: source.host.sim_time if hasattr(source, "host") else 0
    )
    return source


def sample(client, seed=42, version="lab-demo@2"):
    response = client.post("/api/v1/sessions", json={"sceneVersion": version, "seed": seed})
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["seed"] == seed
    route = "/api/v1/sessions/" + summary["sessionId"]
    initial = client.get(route + "/state", params={"keys": ",".join(KEYS)}).json()["entities"]
    result = client.post(
        route + "/commands", json={"requestId": "sample-step", "command": "step", "args": {"count": 1000}}
    )
    assert result.json()["ack"]["status"] == "applied", result.text
    values = client.get(route + "/state", params={"keys": ",".join(KEYS)}).json()["entities"]
    return route, initial, values


def test_seeded_device_readings_replay_reset_and_version_identity(tmp_path):
    with TestClient(create_app(source_factory=factory)) as client:
        route, initial, first = sample(client)
        manifest = client.get(route + "/scene").json()
        assert set(KEYS).issubset({node["entityKey"] for node in manifest["nodes"]})
        catalog = client.get(route + "/catalog").json()
        assert catalog["types"]["incubator/v1"]["actions"]["setSetpoint"]["risk"] == "high"
        assert catalog["types"]["biosafety-cabinet/v1"]["actions"]["setUv"]["risk"] == "high"
        assert catalog["types"]["env-sensor/v1"]["actions"] == {}
        assert first[INCUBATOR]["values"]["temperatureC"] > initial[INCUBATOR]["values"]["temperatureC"]
        assert first[CABINET]["values"]["inflowMs"] > initial[CABINET]["values"]["inflowMs"]
        assert first[SENSOR]["values"]["temperatureC"] > initial[SENSOR]["values"]["temperatureC"]
        sampled = first[INCUBATOR]["meta"]["temperatureC"]["simTime"]
        ideal = 37 - 15 * math.exp(-sampled / 60)
        assert abs(first[INCUBATOR]["values"]["temperatureC"] - ideal) <= 0.0300001
        assert all(not meta.get("stale") for entity in first.values() for meta in entity["meta"].values())
        expected = {key: entity["values"] for key, entity in first.items()}
        client.post(route + "/commands", json={"requestId": "reset", "command": "reset"})
        client.post(
            route + "/commands", json={"requestId": "repeat-step", "command": "step", "args": {"count": 1000}}
        )
        repeated = client.get(route + "/state", params={"keys": ",".join(KEYS)}).json()["entities"]
        assert {key: entity["values"] for key, entity in repeated.items()} == expected
        client.delete(route)
        route, _, second = sample(client)
        assert {key: entity["values"] for key, entity in second.items()} == expected
        client.delete(route)
        route, _, different = sample(client, seed=43)
        assert different[SENSOR]["values"] != expected[SENSOR]
        client.delete(route)
        session_id = route.rsplit("/", 1)[1]
        metadata = client.get(f"/recordings/{session_id}/recording.json").json()
        assert metadata["seed"] == 43 and metadata["protocolMinorVersion"] == 1
        # Retain the independent HTTP read for the frozen-reader compatibility probe.
        (tmp_path / "frozen-reader-expected.json").write_text(json.dumps({
            "recording": str(tmp_path / "recordings" / session_id),
            "entities": {key: entity["values"] for key, entity in different.items()},
        }), encoding="utf-8")
        legacy = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1"}).json()
        legacy_route = "/api/v1/sessions/" + legacy["sessionId"]
        old = client.get(legacy_route + "/scene").json()
        assert set(KEYS).isdisjoint({node["entityKey"] for node in old["nodes"]})
        assert len(client.get(legacy_route + "/catalog").json()["entities"]) == 22


def test_open_door_relaxes_temperature_and_co2_toward_ambient(tmp_path, monkeypatch):
    package = tmp_path / "library/scenes/lab-demo/3"
    shutil.copytree(Path(__file__).resolve().parents[2] / "library/scenes/lab-demo/2", package)
    bindings_file = package / "devices.yaml"
    bindings = yaml.safe_load(bindings_file.read_text(encoding="utf-8"))
    next(binding for binding in bindings["entities"] if binding.get("key") == INCUBATOR)["config"] = {
        "initialTemperatureC": 37,
        "initialCo2Percent": 5,
        "doorOpen": True,
    }
    bindings_file.write_text(yaml.safe_dump(bindings), encoding="utf-8")
    report_file = package / "validation.json"
    report = json.loads(report_file.read_bytes()) | {"subject": "scene:lab-demo@3"}
    report_file.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    with TestClient(create_app(source_factory=factory)) as client:
        _, initial, final = sample(client, version="lab-demo@3")
        assert final[INCUBATOR]["values"]["temperatureC"] < initial[INCUBATOR]["values"]["temperatureC"] - 1
        assert final[INCUBATOR]["values"]["co2Percent"] < initial[INCUBATOR]["values"]["co2Percent"] - 0.5


def test_high_risk_device_controls_wait_for_approval_and_plan_refs_are_not_accepted():
    with TestClient(create_app(source_factory=factory)) as client:
        created = client.post(
            "/api/v1/sessions", json={"sceneVersion": "lab-demo@2", "approval": {"high": "require"}}
        ).json()
        route = "/api/v1/sessions/" + created["sessionId"]
        for key, action, params in [
            (INCUBATOR, "setSetpoint", {"temperatureC": 38}),
            (CABINET, "setUv", {"on": True}),
        ]:
            response = client.post(
                route + "/actions",
                json={"requestId": action, "entityKey": key, "action": action, "params": params},
            )
            assert response.status_code == 202 and response.json()["status"] == "pending_approval", (
                response.text
            )
        states = client.get(route + "/state", params={"keys": ",".join(KEYS)}).json()["entities"]
        assert states[INCUBATOR]["values"]["setpointC"] == 37
        assert states[CABINET]["values"]["uvOn"] is False
        unsupported = client.post(
            route + "/actions",
            json={
                "requestId": "future-plan",
                "entityKey": CABINET,
                "action": "setBlower",
                "params": {"on": False},
                "planId": "plan-1",
                "stepId": 1,
            },
        )
        assert unsupported.status_code == 422

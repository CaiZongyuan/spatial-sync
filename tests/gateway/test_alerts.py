"""Declared alerts use the real Synthetic HTTP/WS/recording interfaces."""

import json
import shutil
import time
from itertools import count
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.sources import synthetic_source
from packages.info.catalog import load_catalog
from packages.info.log import read_segment
from packages.info.program import ProgramConfig

KEY = "/World/Test/Thermostat"


class Thermostat:
    id, type, tick_priority = "test.thermostat", "test-thermostat/v1", 100
    Config = ProgramConfig

    def init(self, ctx):
        ctx.set(temperatureC=37, setpointC=37)

    def tick(self, ctx, dt):
        pass

    def start(self, inv, ctx):
        ctx.set(temperatureC=inv.params["temperatureC"])
        inv.succeed()


@pytest.fixture
def alert_catalog(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    devices = tmp_path / "devices"
    shutil.copytree(root / "devices", devices)
    rule = {
        "when": {"all": [{"property": "temperatureC", "deviatesFrom": "setpointC", "by": 1.0}]},
        "forSeconds": 60,
        "severity": "critical",
        "message": "Temperature differs from target",
        "hint": "Check the door",
    }
    definition = {
        "type": Thermostat.type,
        "title": "Controlled thermostat",
        "properties": {
            "temperatureC": {"schema": {"type": "number"}},
            "setpointC": {"schema": {"type": "number"}},
        },
        "actions": {"inject": {"params": {"temperatureC": {"type": "number"}}, "required": ["temperatureC"]}},
        "alerts": {"temperatureDeviation": rule},
    }
    (devices / "types/test-thermostat.v1.yaml").write_text(yaml.safe_dump(definition), encoding="utf-8")
    shutil.copyfile(root / "library/scenes/lab-demo/1/locations.yaml", tmp_path / "locations.yaml")
    scene = yaml.safe_load((root / "library/scenes/lab-demo/1/devices.yaml").read_text(encoding="utf-8"))
    scene["entities"].append(
        {
            "key": KEY,
            "type": Thermostat.type,
            "program": Thermostat.id,
            "unplaced": True,
            "alerts": {"temperatureDeviation": rule | {"forSeconds": 0.25}},
        }
    )
    scene_file = tmp_path / "devices.yaml"
    scene_file.write_text(yaml.safe_dump(scene), encoding="utf-8")
    monkeypatch.setattr(synthetic_source, "REGISTRY", synthetic_source.REGISTRY | {Thermostat.id: Thermostat})
    monkeypatch.setattr(
        synthetic_source,
        "load_catalog",
        lambda manifest, scene_id, registry, **options: load_catalog(
            manifest, scene_id, registry, root=devices, scene_file=scene_file
        ),
    )


def test_sustained_deviation_acknowledgement_and_recovery_are_reliable_and_recorded(alert_catalog):
    with TestClient(create_app()) as client:
        created = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1", "controlHz": 20})
        assert created.status_code == 201, created.text
        sid = created.json()["sessionId"]
        route, requests = "/api/v1/sessions/" + sid, count()

        def command(name, **args):
            response = client.post(
                route + "/commands",
                json={
                    "requestId": f"control-{next(requests)}",
                    "command": name,
                    "args": args,
                },
            )
            assert response.json()["ack"]["status"] == "applied", response.text

        def alerts(**params):
            response = client.get("/api/v1/alerts", params={"sessionId": sid, **params})
            assert response.status_code == 200, response.text
            return response.json()["alerts"]

        def inject(value):
            response = client.post(
                route + "/actions",
                json={
                    "requestId": f"inject-{next(requests)}",
                    "entityKey": KEY,
                    "action": "inject",
                    "params": {"temperatureC": value},
                },
            )
            assert response.status_code == 202, response.text

        def next_alert(ws, state):
            for _ in range(100):
                message = receive_message(ws)
                if message["type"] == "entity.alert":
                    assert message["state"] == state, message
                    return message
            raise AssertionError("Missing entity.alert " + state)

        command("pause")
        command("reset")
        assert alerts() == []
        with client.websocket_connect(route + "/ws") as ws:
            ready = receive_message(ws)
            ws.send_json(
                {"type": "client.subscribe", "protocolVersion": 2, "sceneRevision": ready["sceneRevision"]}
            )
            inject(39)
            command("step", count=12)  # First control tick at .05 starts the condition.
            command("step", count=59)  # 71/240 seconds: duration has not reached .25.
            assert alerts(active=True) == []
            time.sleep(0.28)  # Paused wall time cannot satisfy a simulation duration.
            assert alerts(active=True) == []
            command("step", count=1)  # At .30: .25 seconds of sustained deviation.
            raised = next_alert(ws, "raised")
            assert raised["since"] == pytest.approx(0.05)
            assert raised["simTime"] == pytest.approx(0.30)
            assert raised["value"] == 39
            alert_id = raised["alertId"]
            ack = client.post(
                "/api/v1/alerts/" + alert_id + "/ack",
                json={"note": "Checking thermostat"},
                headers={"X-Actor": "agent:lab-check"},
            )
            assert ack.status_code == 200, ack.text
            acknowledged = next_alert(ws, "acknowledged")
            assert acknowledged["actor"] == {"kind": "agent", "name": "lab-check"}
            assert "note" not in acknowledged
            assert ack.json()["acknowledgement"]["note"] == "Checking thermostat"
            retry = client.post(
                "/api/v1/alerts/" + alert_id + "/ack",
                json={"note": "retry"},
                headers={"X-Actor": "user:another"},
            )
            assert retry.status_code == 200 and retry.json() == ack.json()
            inject(37)
            command("step", count=12)
            cleared = next_alert(ws, "cleared")
            assert cleared["alertId"] == alert_id and cleared["value"] == 37
            assert alerts(active=True) == []
            assert alerts()[0]["state"] == "cleared"
        client.delete(route).raise_for_status()
        metadata = client.get(f"/recordings/{sid}/recording.json").json()
        messages = [
            json.loads(record.payload)
            for segment in metadata["segments"]
            for record in read_segment(client.get(f"/recordings/{sid}/" + segment["file"]).content).records
            if record.kind == 2
        ]
        actual = [message for message in messages if message["type"] == "entity.alert"]
        assert [message["state"] for message in actual] == ["raised", "acknowledged", "cleared"]
        assert {message["alertId"] for message in actual} == {alert_id}
        assert actual[1] == acknowledged and actual[2] == cleared
        assert not any(message["type"] == "information.clock" for message in messages)
        reading_changes = [
            change for message in messages if message["type"] == "entity.observations"
            for change in message["changes"] if change["key"] == KEY
        ]
        assert len(reading_changes) == 2  # Stable values did not supply the duration clock.
        audits = [
            message
            for message in messages
            if message["type"] == "entity.event" and message["event"] == "alert.acknowledged"
        ]
        assert len(audits) == 1
        assert audits[0]["data"] == {
            "alertId": alert_id,
            "actor": {"kind": "agent", "name": "lab-check"},
            "note": "Checking thermostat",
        }

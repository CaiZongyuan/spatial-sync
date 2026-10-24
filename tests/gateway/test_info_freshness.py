"""Freshness is a host contract, exercised through the real Synthetic HTTP/WS seam."""

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
from packages.info.program import ProgramConfig

KEY = "/World/Test/SparseSensor"


class SparseSensor:
    id, type, tick_priority = "test.sparse", "test-sensor/v1", 100
    Config = ProgramConfig

    def init(self, ctx):
        ctx.set(reading=37)

    def tick(self, ctx, dt):
        pass

    def start(self, inv, ctx):
        if inv.action == "refresh":
            ctx.set(reading=37)
        inv.succeed()


@pytest.mark.parametrize("startup_stale", [False, True])
def test_same_value_refresh_expiry_once_pause_and_stale_action_rejection(
    tmp_path, monkeypatch, startup_stale
):
    root = Path(__file__).resolve().parents[2]
    devices = tmp_path / "devices"
    shutil.copytree(root / "devices", devices)
    (devices / "types/test-sensor.v1.yaml").write_text(
        yaml.safe_dump(
            {
                "type": SparseSensor.type,
                "title": "Sparse reading fixture",
                "properties": {
                    "reading": {
                        "schema": {"type": "number"},
                        "staleAfterSeconds": 0.2,
                        "deadband": 100,
                        "maxRateHz": 0.01,
                    }
                },
                "actions": {"refresh": {}, "inspect": {"preconditions": [{"property": "reading", "gte": 0}]}},
            }
        ),
        encoding="utf-8",
    )
    scene_file = tmp_path / "devices.yaml"
    shutil.copyfile(root / "library/scenes/lab-demo/1/locations.yaml", tmp_path / "locations.yaml")
    scene = yaml.safe_load((root / "library/scenes/lab-demo/1/devices.yaml").read_text(encoding="utf-8"))
    scene["entities"].append(
        {"key": KEY, "type": SparseSensor.type, "program": SparseSensor.id, "unplaced": True}
    )
    scene_file.write_text(yaml.safe_dump(scene), encoding="utf-8")
    monkeypatch.setattr(
        synthetic_source, "REGISTRY", synthetic_source.REGISTRY | {SparseSensor.id: SparseSensor}
    )
    monkeypatch.setattr(
        synthetic_source,
        "load_catalog",
        lambda manifest, scene_id, registry, **options: load_catalog(
            manifest, scene_id, registry, root=devices, scene_file=scene_file
        ),
    )
    with TestClient(create_app()) as client:
        created = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1", "seed": 43})
        assert created.status_code == 201, created.text
        route = "/api/v1/sessions/" + created.json()["sessionId"]
        requests = count()

        def command(name, **args):
            result = client.post(
                route + "/commands",
                json={"requestId": f"control-{next(requests)}", "command": name, "args": args},
            )
            assert result.json()["ack"]["status"] == "applied", result.text
            return result.json()["state"]

        def state(stale):
            deadline = time.monotonic() + 1
            while True:
                value = client.get(route + "/state", params={"keys": KEY}).json()["entities"][KEY]
                if value["meta"]["reading"].get("stale", False) is stale:
                    return value
                assert time.monotonic() < deadline, value
                time.sleep(0.005)

        if startup_stale:
            assert state(True)["meta"]["reading"]["simTime"] == 0
        command("pause")
        reset_state = command("reset")
        assert reset_state["epoch"] == created.json()["epoch"] + 1
        assert state(False)["values"]["reading"] == 37
        command("step", count=40)
        refreshed = client.post(
            route + "/actions", json={"requestId": "refresh", "entityKey": KEY, "action": "refresh"}
        )
        assert refreshed.json()["status"] == "accepted", refreshed.text
        command("step", count=4)
        command("step", count=40)
        assert state(False)["values"]["reading"] == 37
        command("step", count=16)
        expired = state(True)
        assert expired["values"]["reading"] == 37
        observed_at = expired["meta"]["reading"]["simTime"]
        assert 0.16 < observed_at < 0.2  # Last actual same-value write, not the expiry clock.
        denied = client.post(
            route + "/actions", json={"requestId": "stale-inspect", "entityKey": KEY, "action": "inspect"}
        )
        assert denied.status_code == 409 and denied.json()["error"]["code"] == "precondition_failed"
        assert denied.json()["error"]["details"]["reason"] == "stale_reading"
        assert denied.json()["error"]["message"] == "读数已过期"
        with client.websocket_connect(route + "/ws") as ws:
            ready = receive_message(ws)
            ws.send_json(
                {"type": "client.subscribe", "protocolVersion": 2, "sceneRevision": ready["sceneRevision"]}
            )
            for _ in range(10):
                message = receive_message(ws)
                if message["type"] == "entity.snapshot":
                    sensor = next(entity for entity in message["entities"] if entity["key"] == KEY)
                    assert sensor["values"]["reading"] == 37
                    assert sensor["meta"]["reading"]["stale"] is True
                    break
            else:
                raise AssertionError("WebSocket did not expose the authoritative stale snapshot")
        command("step", count=80)
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        def stale_transitions(epoch):
            return [
                change
                for event in events
                if event["message"]["type"] == "entity.observations"
                and event["message"]["sceneRevision"] == reset_state["sceneRevision"]
                and event["message"]["epoch"] == epoch
                for change in event["message"]["changes"]
                if change["key"] == KEY and change.get("meta", {}).get("reading", {}).get("stale")
            ]

        if startup_stale:
            assert len(stale_transitions(created.json()["epoch"])) == 1
        assert len(stale_transitions(reset_state["epoch"])) == 1
        time.sleep(0.21)
        assert state(True)["rev"] == expired["rev"]  # Paused wall time never advances freshness.
        client.post(route + "/actions", json={"requestId": "recover", "entityKey": KEY, "action": "refresh"})
        command("step", count=4)
        assert state(False)["values"]["reading"] == 37
        assert client.get("/api/v1/health").json()["protocolMinorVersion"] == 1
        client.delete(route)
        metadata = json.loads(next((tmp_path / "recordings").glob("*/recording.json")).read_bytes())
        assert metadata["formatVersion"] == 2 and metadata["protocolVersion"] == 2
        assert metadata["protocolMinorVersion"] == 1
        assert metadata["seed"] == 43

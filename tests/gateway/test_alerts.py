"""Declared alerts use the real Synthetic HTTP/WS/recording interfaces."""

import asyncio
import json
import shutil
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from ws_messages import receive_message

from apps.api.app.main import create_app
from apps.api.app.sources import synthetic_source
from packages.info.catalog import load_catalog
from packages.info.log import read_segment
from packages.info.program import ProgramConfig
from packages.protocol.internal import InformationClock
from packages.protocol.schema import EntityObservations

KEY = "/World/Test/Thermostat"


class Thermostat:
    id, type, tick_priority = "test.thermostat", "test-thermostat/v1", 100

    class Config(ProgramConfig):
        recoverDuringTick: bool = False

    def init(self, ctx):
        ctx.set(temperatureC=37, setpointC=37)

    def tick(self, ctx, dt):
        if ctx.config.recoverDuringTick:
            ctx.set(temperatureC=37)

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
    return devices / "types/test-thermostat.v1.yaml", scene_file


def control(client, route, name, **args):
    response = client.post(
        route + "/commands",
        json={
            "requestId": uuid4().hex,
            "command": name,
            "args": args,
        },
    )
    assert response.json()["ack"]["status"] == "applied", response.text


def inject_reading(client, route, value):
    response = client.post(
        route + "/actions",
        json={
            "requestId": uuid4().hex,
            "entityKey": KEY,
            "action": "inject",
            "params": {"temperatureC": value},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()["invocationId"]


def listed(client, sid, **params):
    response = client.get("/api/v1/alerts", params={"sessionId": sid, **params})
    assert response.status_code == 200, response.text
    return response.json()["alerts"]


def paused_session(client):
    response = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1", "controlHz": 20})
    assert response.status_code == 201, response.text
    sid = response.json()["sessionId"]
    route = "/api/v1/sessions/" + sid
    control(client, route, "pause")
    control(client, route, "reset")
    return sid, route


def await_alerts(client, sid, expected, **params):
    deadline = time.monotonic() + 2
    while True:
        actual = listed(client, sid, **params)
        if len(actual) == expected:
            return actual
        assert time.monotonic() < deadline, actual
        time.sleep(0.005)


def test_same_tick_transient_is_aggregated_before_any_zero_duration_alert(alert_catalog):
    _, scene_file = alert_catalog
    scene = yaml.safe_load(scene_file.read_text(encoding="utf-8"))
    binding = scene["entities"][-1]
    binding["config"] = {"recoverDuringTick": True}
    binding["alerts"]["temperatureDeviation"]["forSeconds"] = 0
    scene_file.write_text(yaml.safe_dump(scene), encoding="utf-8")
    with TestClient(create_app()) as client:
        sid, route = paused_session(client)
        invocation = inject_reading(client, route, 39)
        control(client, route, "step", count=12)
        completed = client.get(
            route + "/invocations/" + invocation, params={"wait": True, "timeoutSeconds": 1}
        )
        assert completed.json()["status"] == "succeeded", completed.text
        assert listed(client, sid) == []
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        observed = [
            change["values"].get("temperatureC")
            for event in events
            if event["message"]["type"] == "entity.observations"
            for change in event["message"]["changes"]
            if change["key"] == KEY
        ]
        assert 39 not in observed
        assert not any(event["message"]["type"] == "entity.alert" for event in events)
        assert (
            client.get(route + "/state", params={"keys": KEY}).json()["entities"][KEY]["values"][
                "temperatureC"
            ]
            == 37
        )
        client.delete(route)


def test_reset_reload_and_stop_close_context_without_false_cleared_events(alert_catalog):
    with TestClient(create_app()) as client:
        sid, route = paused_session(client)
        inject_reading(client, route, 39)
        control(client, route, "step", count=72)
        [first] = await_alerts(client, sid, 1, active=True)
        control(client, route, "reset")
        assert listed(client, sid, active=True) == []
        [archived] = listed(client, sid)
        assert archived["state"] == "raised" and archived["closedContext"]["reason"] == "session_reset"
        assert client.post("/api/v1/alerts/" + first["alertId"] + "/ack", json={}).status_code == 409
        inject_reading(client, route, 39)
        control(client, route, "step", count=72)
        [second] = await_alerts(client, sid, 1, active=True)
        assert second["alertId"] != first["alertId"] and second["epoch"] == first["epoch"] + 1
        reloaded = client.post(route + "/reload")
        assert reloaded.status_code == 200, reloaded.text
        assert listed(client, sid, active=True) == []
        assert listed(client, sid)[0]["closedContext"]["reason"] == "scene_reloaded"
        control(client, route, "pause")
        control(client, route, "reset")
        inject_reading(client, route, 39)
        control(client, route, "step", count=72)
        [third] = await_alerts(client, sid, 1, active=True)
        assert third["sceneRevision"] != second["sceneRevision"]
        client.delete(route).raise_for_status()
        assert listed(client, sid, active=True) == []
        [latest, *older] = listed(client, sid)
        assert latest["state"] == "raised" and latest["closedContext"]["reason"] == "session_stopped"
        assert client.post("/api/v1/alerts/" + third["alertId"] + "/ack", json={}).status_code == 409
        assert client.post("/api/v1/alerts/missing/ack", json={}).status_code == 404
        assert all(episode["state"] == "raised" for episode in older)
        events = client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0}).json()["events"]
        assert not any(
            event["message"]["type"] == "entity.alert" and event["message"]["state"] == "cleared"
            for event in events
        )


def test_sustained_deviation_acknowledgement_and_recovery_are_reliable_and_recorded(alert_catalog):
    with TestClient(create_app()) as client:
        sid, route = paused_session(client)
        wire_catalog = client.get(route + "/catalog").json()
        assert all("alerts" not in definition for definition in wire_catalog["types"].values())
        assert all("alerts" not in binding for binding in wire_catalog["entities"].values())
        entity = client.get(route + "/entity", params={"key": KEY}).json()
        assert "alerts" not in entity["definition"] and "alerts" not in entity["binding"]

        def next_alert(ws, state):
            for _ in range(100):
                message = receive_message(ws)
                if message["type"] == "entity.alert":
                    assert message["state"] == state, message
                    return message
            raise AssertionError("Missing entity.alert " + state)

        assert listed(client, sid) == []
        with client.websocket_connect(route + "/ws") as ws:
            ready = receive_message(ws)
            ws.send_json(
                {"type": "client.subscribe", "protocolVersion": 2, "sceneRevision": ready["sceneRevision"]}
            )
            inject_reading(client, route, 39)
            control(client, route, "step", count=12)  # First control tick at .05 starts the condition.
            control(client, route, "step", count=59)  # 71/240 seconds: duration has not reached .25.
            assert listed(client, sid, active=True) == []
            time.sleep(0.28)  # Paused wall time cannot satisfy a simulation duration.
            assert listed(client, sid, active=True) == []
            control(client, route, "step", count=1)  # At .30: .25 seconds of sustained deviation.
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
            inject_reading(client, route, 37)
            control(client, route, "step", count=12)
            cleared = next_alert(ws, "cleared")
            assert cleared["alertId"] == alert_id and cleared["value"] == 37
            assert listed(client, sid, active=True) == []
            assert listed(client, sid)[0]["state"] == "cleared"
        client.delete(route).raise_for_status()
        metadata = client.get(f"/recordings/{sid}/recording.json").json()
        assert client.get(f"/recordings/{sid}/catalog.json").json() == wire_catalog
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
            change
            for message in messages
            if message["type"] == "entity.observations"
            for change in message["changes"]
            if change["key"] == KEY
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


@pytest.mark.parametrize("expires_after", [0.15, 0.4])
def test_stale_rule_triggers_while_unknown_deviation_does_not_raise_or_falsely_clear(
    alert_catalog, expires_after
):
    type_file, _ = alert_catalog
    definition = yaml.safe_load(type_file.read_text(encoding="utf-8"))
    for property in definition["properties"].values():
        property["staleAfterSeconds"] = expires_after
    definition["alerts"]["staleTarget"] = {
        "when": {"property": "setpointC", "stale": True},
        "forSeconds": 0.1,
        "severity": "warning",
        "message": "Target reading is stale",
    }
    definition["actions"]["inspect"] = {
        "preconditions": [{"property": "temperatureC", "deviatesFrom": "setpointC", "by": 1}],
    }
    type_file.write_text(yaml.safe_dump(definition), encoding="utf-8")
    with TestClient(create_app()) as client:
        sid, route = paused_session(client)
        inject_reading(client, route, 39)
        control(client, route, "step", count=72)
        if expires_after == 0.4:
            [deviation] = await_alerts(client, sid, 1, active=True)
            assert deviation["rule"] == "temperatureDeviation"
        control(client, route, "step", count=60)
        episodes = await_alerts(client, sid, 2 if expires_after == 0.4 else 1, active=True)
        assert {episode["rule"] for episode in episodes} == (
            {"staleTarget", "temperatureDeviation"} if expires_after == 0.4 else {"staleTarget"}
        )
        assert all(episode["state"] == "raised" for episode in episodes)
        denied = client.post(
            route + "/actions",
            json={
                "requestId": "inspect",
                "entityKey": KEY,
                "action": "inspect",
            },
        )
        assert denied.status_code == 409 and denied.json()["error"]["code"] == "precondition_failed"
        assert denied.json()["error"]["details"]["reason"] == "stale_reading"
        assert denied.json()["error"]["message"] == "读数已过期"
        client.delete(route)


class DeferredInformationSource(synthetic_source.SyntheticSource):
    """Delay the public reliable source channel while its spatial channel continues."""

    def __init__(self, session_id):
        super().__init__(session_id)
        self.hold_information = False
        self.release_information = asyncio.Event()

    async def events(self):
        async for event in super().events():
            if self.hold_information and isinstance(event, (EntityObservations, InformationClock)):
                await self.release_information.wait()
            yield event


@pytest.mark.parametrize("reset_before_delivery", [False, True])
async def test_spatial_channel_cannot_advance_a_withheld_information_clock_or_cross_epoch(
    alert_catalog, reset_before_delivery
):
    sources = []

    def factory(kind, session_id):
        source = DeferredInformationSource(session_id)
        sources.append(source)
        return source

    app = create_app(source_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://gateway") as client:
            response = await client.post(
                "/api/v1/sessions", json={"sceneVersion": "lab-demo@1", "controlHz": 20}
            )
            assert response.status_code == 201, response.text
            sid = response.json()["sessionId"]
            route = "/api/v1/sessions/" + sid

            async def command(name, **args):
                response = await client.post(
                    route + "/commands",
                    json={
                        "requestId": uuid4().hex,
                        "command": name,
                        "args": args,
                    },
                )
                assert response.json()["ack"]["status"] == "applied", response.text

            async def inject():
                response = await client.post(
                    route + "/actions",
                    json={
                        "requestId": uuid4().hex,
                        "entityKey": KEY,
                        "action": "inject",
                        "params": {"temperatureC": 39},
                    },
                )
                assert response.status_code == 202, response.text

            await command("pause")
            await command("reset")
            sources[0].hold_information = True
            try:
                await inject()
                await command("step", count=240)
                spatial = await client.get(route + "/snapshot")
                assert spatial.json()["simTime"] == 1
                withheld = await client.get("/api/v1/alerts", params={"sessionId": sid})
                assert withheld.json()["alerts"] == []
                if reset_before_delivery:
                    await command("reset")
                sources[0].release_information.set()
                sources[0].hold_information = False
                if reset_before_delivery:
                    await inject()
                    await command("step", count=72)
                deadline = time.monotonic() + 2
                while True:
                    response = await client.get("/api/v1/alerts", params={"sessionId": sid})
                    if response.json()["alerts"]:
                        break
                    assert time.monotonic() < deadline, response.text
                    await asyncio.sleep(0.005)
                [raised] = response.json()["alerts"]
                assert raised["simTime"] == pytest.approx(0.3) and raised["since"] == pytest.approx(0.05)
                assert raised["epoch"] == (2 if reset_before_delivery else 1)
            finally:
                sources[0].release_information.set()
                await client.delete(route)

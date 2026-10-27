"""Declared loading faults raise, block HTTP start and clear on recovery."""

from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.sources import synthetic_source
from packages.programs.tube.virtual import TubeProgram

CENTRIFUGE = "/World/Lab/Centrifuge"
TUBE = "/World/Lab/Tubes/Tube01"


def test_loading_fault_is_visible_and_blocks_the_shared_action_pipeline(monkeypatch):
    bad = {"loading": False}

    class UnstableTube(TubeProgram):
        def tick(self, ctx, dt):
            super().tick(ctx, dt)
            if ctx.key == TUBE:
                ctx.set(location=f"{CENTRIFUGE}/Rotor/Slot1" if bad["loading"] else
                        ctx.config.initialLocations[TUBE], upright=not bad["loading"])

    monkeypatch.setattr(synthetic_source, "REGISTRY", synthetic_source.REGISTRY | {
        TubeProgram.id: UnstableTube})
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
        assert response.status_code == 201
        sid = response.json()["sessionId"]
        route = f"/api/v1/sessions/{sid}"

        def control(command, **args):
            result = client.post(route+"/commands", json={"requestId": uuid4().hex,
                                                         "command": command, "args": args})
            assert result.json()["ack"]["status"] == "applied", result.text

        def action(name, **params):
            return client.post(route+"/actions", json={"requestId": uuid4().hex,
                               "entityKey": CENTRIFUGE, "action": name, "params": params})

        def alerts():
            return client.get("/api/v1/alerts", params={"sessionId": sid, "active": True}).json()["alerts"]

        def state():
            response = client.get(route+"/state", params={"keys": CENTRIFUGE})
            assert response.status_code == 200, response.text
            return response.json()["entities"][CENTRIFUGE]["values"]

        control("pause")
        assert action("closeLid").status_code == 202
        control("step", count=300)
        assert not [a for a in alerts() if a["rule"] == "unsafeLoad"]
        bad["loading"] = True
        control("step", count=480)
        assert state()["loadSafe"] is False
        assert [a for a in alerts() if a["rule"] == "unsafeLoad" and a["active"]]
        result = action("start", rpm=3000, durationSeconds=10)
        assert result.status_code == 409 and result.json()["error"]["code"] == "unsafe_load"
        bad["loading"] = False
        control("step", count=120)
        assert not [a for a in alerts() if a["rule"] == "unsafeLoad" and a["active"]]
        assert state()["loadSafe"] is True
        assert action("start", rpm=500, durationSeconds=1).status_code == 202

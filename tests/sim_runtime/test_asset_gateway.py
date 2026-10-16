from hashlib import sha256
from io import BytesIO

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytest.importorskip("pxr.Usd")
import trimesh

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

pytestmark = pytest.mark.newton


def receive(ws, kind):
    for _ in range(100):
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"No {kind}")


def test_registered_usd_scene_serves_native_mesh_over_real_runner_ipc_and_http():
    with TestClient(create_app(Settings(heartbeat_interval_seconds=0.03))) as client:
        started = client.post("/api/v1/sessions", json={"sceneId": "usd-lab", "source": "newton"})
        assert started.status_code == 201, started.text
        sid = started.json()["sessionId"]
        route = f"/api/v1/sessions/{sid}"
        manifest = client.get(route + "/scene").json()
        body = next(node for node in manifest["nodes"] if node["entityKey"] == "/World/MovingPart")
        assert len(body["visuals"]) == 1 and len(body["fallbackVisuals"]) == 2
        assert {visual["kind"] for visual in body["fallbackVisuals"]} == {"mesh", "primitive"}
        assert client.get(body["visuals"][0]["url"]).status_code == 404
        native = next(visual for visual in body["fallbackVisuals"] if visual["kind"] == "mesh")
        assert native["kind"] == "mesh" and native["assetUpAxis"] == "Z"
        mesh = client.get(native["url"])
        assert mesh.status_code == 200, mesh.text
        assert mesh.headers["content-type"] == "model/gltf-binary"
        assert "immutable" in mesh.headers["cache-control"]
        assert mesh.headers["etag"] == '"' + native["meshId"] + '"'
        assert "sha256-" + sha256(mesh.content).hexdigest() == native["meshId"]
        assert trimesh.load(BytesIO(mesh.content), file_type="glb", force="scene").geometry
        assert client.get(native["url"], headers={"If-None-Match": mesh.headers["etag"]}).status_code == 304
        assert client.get(route + "/meshes/unregistered").status_code == 404
        cover = client.get("/api/v1/assets/equipment-cover.glb")
        assert cover.status_code == 200 and cover.content[:4] == b"glTF"
        assert client.get(route + "/stats").json()["readbackMode"] == "cpu-bulk"
        with client.websocket_connect(route + "/ws") as ws:
            receive(ws, "server.ready")
            ws.send_json(
                {
                    "type": "client.subscribe",
                    "protocolVersion": 1,
                    "sceneRevision": manifest["sceneRevision"],
                    "scope": "all",
                    "rateHz": 30,
                }
            )
            receive(ws, "stream.snapshot")
            ws.send_json(
                {"type": "command.request", "requestId": "pause-usd", "command": "pause", "args": {}}
            )
            assert receive(ws, "command.ack")["status"] == "applied"
            receive(ws, "session.state")
            before = client.get(route + "/pose", params={"key": body["entityKey"]}).json()
            ws.send_json(
                {"type": "command.request", "requestId": "step-usd", "command": "step", "args": {"count": 24}}
            )
            assert receive(ws, "command.ack")["status"] == "applied"
            after = client.get(route + "/pose", params={"key": body["entityKey"]}).json()
            assert after["simTime"] == pytest.approx(before["simTime"] + 0.1)
            assert after["p"][2] < before["p"][2]
        assert client.delete(route).status_code == 204

"""Configured Live layouts retain real exact library versions without starting physics."""

import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from template_support import upload
from test_live_devices import FakeBrokerTransport

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import BUILTIN_ROOT


def test_live_layout_is_resolved_and_blocks_exact_scene_deletion_until_configuration_is_removed(tmp_path):
    root = BUILTIN_ROOT / "scenes/lab-demo/3"
    files = {name: (root / name).read_bytes() for name in ("scene.yaml", "devices.yaml", "locations.yaml")}
    metadata = yaml.safe_load(files["scene.yaml"])
    metadata["name"] = "live-layout-fixture"
    declarations = yaml.safe_load(files["devices.yaml"])
    declarations["scene"] = metadata["name"]
    files["scene.yaml"] = yaml.safe_dump(metadata).encode()
    files["devices.yaml"] = yaml.safe_dump(declarations).encode()
    settings = Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings")
    source_calls = []

    def no_source(*args):
        source_calls.append(args)
        raise AssertionError("Live layout metadata must never start a spatial Source")

    with TestClient(create_app(settings, source_factory=no_source)) as client:
        imported = upload(client, files)
        assert imported.status_code == 200, imported.text
        assert imported.json()["sceneVersion"] == "live-layout-fixture@1"
        scene = client.get("/api/v1/library/scenes/live-layout-fixture/1").json()
    bindings = yaml.safe_load((Path(__file__).parents[2] / "devices/live/lab.yaml").read_text("utf-8"))
    bindings.update(live="layout-lab", layout="live-layout-fixture@1")
    bindings["entities"][0]["placeAt"] = "/World/Lab/Incubator"
    config = tmp_path / "live.yaml"
    config.write_text(yaml.safe_dump(bindings), encoding="utf-8")
    app = create_app(Settings(state_root=settings.state_root, recordings_root=settings.recordings_root,
                              live_bindings=config), source_factory=no_source,
                     live_transport_factory=lambda broker, topics:
                     FakeBrokerTransport(broker, topics, time.monotonic))
    with TestClient(app) as client:
        health = client.get("/api/v1/health").json()
        assert health["sessions"] == [] and health["live"]["state"] == "RUNNING"
        live = client.get("/api/v1/live").json()
        assert live["layout"] == "live-layout-fixture@1"
        assert live["layoutContentHash"] == scene["contentHash"] and live["layoutError"] is None
        assert app.state.manager.live.manifest is None
        blocked = client.delete("/api/v1/library/scenes/live-layout-fixture/1")
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "version_in_use"
        assert any(reference["kind"] == "live" and reference["id"] == "layout-lab"
                   for reference in blocked.json()["detail"]["references"])
    with TestClient(create_app(settings, source_factory=no_source)) as client:
        released = client.delete("/api/v1/library/scenes/live-layout-fixture/1")
        assert released.status_code == 202, released.text
        assert released.json()["code"] == "pending_approval"
    assert source_calls == []

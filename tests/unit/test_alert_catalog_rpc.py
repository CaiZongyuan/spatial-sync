"""Real Newton adapter spawn/Pipe RPC with a CPU-free external Runner counterpart."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import yaml

from apps.api.app.sources.newton_source import NewtonSource
from packages.info.alerts import AlertEngine
from packages.library.scenes import SceneLibrary
from packages.protocol.internal import InformationClock
from packages.protocol.schema import (
    EntitySnapshot,
    EntityValues,
    SceneManifest,
    SessionOptions,
    TransformFrame,
)
from packages.sim_runtime import runner
from packages.sim_runtime.info_catalog import load_info_catalog
from packages.transport import FrameCodec

KEY = "/World/Lab/Centrifuge"


def catalog_rpc_runner(transport, session_id, scene_id, options, scene_package, *, rpc_fence=None):
    """Speak the real internal protocol without loading Newton or starting a solver."""
    try:
        assert rpc_fence is not None  # The real Source still supplies its private admission fence.
        manifest = SceneManifest.model_validate_json((scene_package.path / "rpc-manifest.json").read_bytes())
        manifest.sessionId = session_id
        catalog = load_info_catalog(manifest, scene_id, scene_package=scene_package)
        rules = json.loads(json.dumps(catalog.alert_rules()))
        mode = (scene_package.path / "rpc-mode.txt").read_text(encoding="utf-8")
        if mode == "tampered":
            rules[KEY]["running"]["forSeconds"] = 99
        snapshot = EntitySnapshot(
            sceneRevision=manifest.sceneRevision,
            epoch=0,
            simTime=0,
            sampleTimeMs=1000,
            entities=[EntityValues(key=KEY, rev=0, values={"rpm": 1000})],
        )
        frame = TransformFrame(
            type="stream.snapshot",
            sessionId=session_id,
            sceneRevision=manifest.sceneRevision,
            epoch=0,
            seq=1,
            simTime=0,
            sampleTimeMs=1000,
            poses=[(node.streamId, 0, 0, 0, 0, 0, 0, 1) for node in manifest.nodes if node.kind == "body"],
        )
        packet = {
            "kind": "ready",
            "manifest": manifest.model_dump(mode="json"),
            "catalog": catalog.model_dump(mode="json"),
            "alertRules": rules,
            "entitySnapshot": snapshot.model_dump(mode="json"),
            "snapshot": frame.model_dump(mode="json"),
        }
        if mode == "missing":
            del packet["alertRules"]
        transport.control.send(packet)
        if mode == "matching":
            future_frame = frame.model_copy(update={"type": "transform.frame", "seq": 2, "simTime": 2})
            transport.frames.send_bytes(
                FrameCodec(session_id, manifest.sceneRevision, 1).encode(future_frame)
            )
            clock = InformationClock(
                sceneRevision=manifest.sceneRevision, epoch=0, simTime=0.25, sampleTimeMs=1250
            )
            transport.control.send({"kind": "info_event", "event": clock.model_dump(mode="json")})
        while transport.control.poll(5):
            request = transport.control.recv()
            if request["kind"] == "close":
                transport.control.send({"kind": "closed", "token": request["token"]})
                return
    finally:
        transport.close()


@pytest.mark.parametrize("mode", ["matching", "tampered", "missing"])
async def test_internal_ready_preserves_private_rules_and_rejects_drift_without_public_extensions(
    tmp_path, monkeypatch, mode
):
    root = Path(__file__).resolve().parents[2]
    package = tmp_path / "scenes/robot-and-tubes/3"
    shutil.copytree(root / "library/scenes/robot-and-tubes/1", package)
    scene = yaml.safe_load((package / "devices.yaml").read_text(encoding="utf-8"))
    binding = next(value for value in scene["entities"] if value.get("key") == KEY)
    binding["alerts"] = {
        "running": {
            "when": {"property": "rpm", "gt": 0},
            "forSeconds": 0.25,
            "severity": "warning",
            "message": "Rotor is running",
        }
    }
    (package / "devices.yaml").write_text(yaml.safe_dump(scene), encoding="utf-8")
    (package / "validation.json").write_text(
        json.dumps(
            {
                "schema": "lw.validation/1",
                "subject": "scene:robot-and-tubes@3",
                "launchable": True,
                "checks": [
                    {
                        "id": "rpc-fixture",
                        "tier": "structural",
                        "status": "warn",
                        "message": "Internal RPC fixture; no physical validation is claimed.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    keys = ["/World/Lab/RobotA", KEY, *[f"/World/Lab/Tubes/Tube{index:02d}" for index in range(1, 21)]]
    locations = yaml.safe_load((package / "locations.yaml").read_text(encoding="utf-8"))
    manifest = SceneManifest(
        sessionId="rpc-fixture",
        sceneRevision="rpc-alert-revision",
        revisionNumericId=1,
        sceneName="RPC fixture",
        source={"kind": "newton", "physicsDt": 1 / 240, "basePublishHz": 30},
        nodes=[
            {"kind": "body", "entityKey": key, "streamId": index, "name": key.rsplit("/", 1)[1]}
            for index, key in enumerate(keys)
        ],
        sites=[
            {"entityKey": site, "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}}
            for site in sorted({value["site"] for value in locations["locations"].values()})
        ],
    )
    (package / "rpc-manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    (package / "rpc-mode.txt").write_text(mode, encoding="utf-8")
    selected = SceneLibrary(user_root=tmp_path).resolve("robot-and-tubes@3", launch=True)
    assert selected.id == "robot-and-tubes@3" and selected.path == package
    monkeypatch.setattr(runner, "run_runner", catalog_rpc_runner)
    source = NewtonSource("rpc-alert-" + mode)
    source.configure_scene(selected)
    try:
        if mode != "matching":
            with pytest.raises(RuntimeError, match="alert rules differ"):
                await asyncio.wait_for(source.start("robot-and-tubes", SessionOptions(device="cpu")), 5)
        else:
            _, catalog = await asyncio.wait_for(
                source.start("robot-and-tubes", SessionOptions(device="cpu")), 5
            )
            assert catalog.alert_rules()[KEY]["running"]["forSeconds"] == 0.25
            assert "alerts" not in catalog.entities[KEY]
            assert all("alerts" not in definition for definition in catalog.types.values())
            snapshot = source.entity_snapshot()
            engine = AlertEngine(catalog)
            values = {entity.key: entity.values for entity in snapshot.entities}
            assert engine.advance(0, 0, 1000, values, {}) == []
            deadline = asyncio.get_running_loop().time() + 2
            while source.latest_frame().simTime != 2:
                assert asyncio.get_running_loop().time() < deadline
                await asyncio.sleep(0.005)
            assert engine.documents() == []
            clock = await asyncio.wait_for(anext(source.events()), 2)
            assert isinstance(clock, InformationClock) and clock.simTime == 0.25
            [raised] = engine.advance(clock.epoch, clock.simTime, clock.sampleTimeMs, values, {})
            assert raised.since == 0 and raised.simTime == 0.25 and raised.rule == "running"
    finally:
        await source.close()
        assert source.stats()["runnerAlive"] is False
        assert source.stats()["readerThreadsAlive"] == []

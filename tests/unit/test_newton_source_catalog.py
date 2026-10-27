"""Ready validation uses the selected package on both sides of the local pipe."""

import asyncio
import json
import shutil
from pathlib import Path

import pytest
import yaml

from apps.api.app.sources.newton_source import NewtonSource
from packages.library.scenes import SceneLibrary
from packages.protocol.schema import EntitySnapshot, SceneManifest, SessionOptions, TransformFrame
from packages.sim_runtime.info_catalog import load_info_catalog

ROOT = Path(__file__).resolve().parents[2]
ROBOT = "/World/Lab/RobotA"


@pytest.mark.parametrize("tampered", [False, True])
async def test_ready_rebuilds_selected_package_catalog_and_still_rejects_disagreement(tmp_path, tampered):
    package = tmp_path / "scenes/robot-and-tubes/3"
    shutil.copytree(ROOT / "library/scenes/robot-and-tubes/1", package)
    bindings = yaml.safe_load((package / "devices.yaml").read_text(encoding="utf-8"))
    robot = next(binding for binding in bindings["entities"] if binding.get("key") == ROBOT)
    robot["config"]["speedScale"] = 0.35
    robot["config"]["locations"] = "selected-locations.yaml"
    locations = yaml.safe_load((package / "locations.yaml").read_text(encoding="utf-8"))
    target = [0.15, -0.4, 0.0, -2.0, 0.0, 1.6, 0.7]
    locations["locations"]["selected.offset"] = {"site": "/World/Lab/Rack/A1", "zone": "probe",
                                                "approach": target, "grasp": target}
    (package / "devices.yaml").write_text(yaml.safe_dump(bindings), encoding="utf-8")
    (package / "selected-locations.yaml").write_text(yaml.safe_dump(locations), encoding="utf-8")
    (package / "validation.json").write_text(json.dumps({
        "schema": "lw.validation/1", "subject": "scene:robot-and-tubes@3", "launchable": True,
        "checks": [{"id": "fixture", "tier": "structural", "status": "warn",
                    "message": "Test fixture; no full structural or physics validation is claimed."}],
    }), encoding="utf-8")
    selected = SceneLibrary(user_root=tmp_path).resolve("robot-and-tubes@3", launch=True)
    assert selected.id == "robot-and-tubes@3" and selected.path == package
    keys = [ROBOT, "/World/Lab/Centrifuge", *[f"/World/Lab/Tubes/Tube{i:02d}" for i in range(1, 21)]]
    manifest = SceneManifest(
        sessionId="selected-newton", sceneRevision="selected-revision", revisionNumericId=1,
        sceneName="Selected package", source={"kind": "newton", "physicsDt": 1 / 240,
                                            "basePublishHz": 30, "realtimeFactor": 1},
        nodes=[{"kind": "body", "entityKey": key, "streamId": i, "name": key.rsplit("/", 1)[1]}
               for i, key in enumerate(keys)],
        sites=[{"entityKey": key, "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]}}
               for key in sorted({item["site"] for item in locations["locations"].values()})],
    )
    catalog = load_info_catalog(manifest, "robot-and-tubes", scene_package=selected)
    assert catalog.entities[ROBOT]["config"]["speedScale"] == 0.35
    selected_locations = catalog.entities[ROBOT]["config"]["locations"]["locations"]
    assert selected_locations["selected.offset"]["approach"] == target
    packet_catalog = catalog.model_dump(mode="json")
    if tampered:
        packet_catalog["entities"][ROBOT]["config"]["speedScale"] = 0.8
    source = NewtonSource(manifest.sessionId)
    source.scene_id, source.options = "robot-and-tubes", SessionOptions(device="cpu")
    source.configure_scene(selected)
    source._loop = asyncio.get_running_loop()
    source._ready = source._loop.create_future()
    try:
        source._on_control({
            "kind": "ready", "manifest": manifest.model_dump(mode="json"), "catalog": packet_catalog,
            "alertRules": catalog.alert_rules(),
            "entitySnapshot": EntitySnapshot(sceneRevision=manifest.sceneRevision, epoch=0,
                                              sampleTimeMs=1000).model_dump(mode="json"),
            "snapshot": TransformFrame(type="stream.snapshot", sessionId=manifest.sessionId,
                                       sceneRevision=manifest.sceneRevision, epoch=0, seq=1, simTime=0,
                                       sampleTimeMs=1000,
                                       poses=[(i, 0, 0, 0, 0, 0, 0, 1) for i in range(len(keys))]
                                       ).model_dump(mode="json"),
        })
        if tampered:
            with pytest.raises(RuntimeError, match="Catalog definitions differ"):
                await source._ready
        else:
            _, ready_catalog = await source._ready
            assert ready_catalog.entities[ROBOT]["config"]["speedScale"] == 0.35
            assert ready_catalog.entities[ROBOT]["config"]["locations"]["locations"][
                "selected.offset"
            ]["approach"] == target
            assert not source._failed
    finally:
        await source.close()

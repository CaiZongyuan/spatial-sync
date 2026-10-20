"""New template-authored versions preserve the previous public device behavior."""

import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.library.common import BUILTIN_ROOT
from packages.library.scenes import SceneLibrary
from packages.protocol.schema import Command, SceneManifest
from packages.sim_runtime.info_catalog import load_info_catalog

CENTRIFUGE, ROBOT = "/World/Lab/Centrifuge", "/World/Lab/RobotA"


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


def test_synthetic_migration_preserves_catalog_seeded_state_actions_reset_and_recording_identity():
    results = []
    with TestClient(create_app(source_factory=factory)) as client:
        for version in ("lab-demo@2", "lab-demo@3"):
            response = client.post(
                "/api/v1/sessions",
                json={
                    "sceneVersion": version,
                    "seed": 43,
                },
            )
            assert response.status_code == 201, response.text
            session_id = response.json()["sessionId"]
            route = "/api/v1/sessions/" + session_id
            source = client.app.state.manager.get(session_id).source

            def values():
                return {
                    key: entity["values"]
                    for key, entity in client.get(route + "/state").json()["entities"].items()
                }

            def step(request_id, count):
                result = client.post(
                    route + "/commands",
                    json={
                        "requestId": request_id,
                        "command": "step",
                        "args": {"count": count},
                    },
                )
                assert result.json()["ack"]["status"] == "applied", result.text

            initial = values()
            step("signal-sample", 100)
            seeded = values()
            client.post(route + "/commands", json={"requestId": "reset", "command": "reset"})
            step("signal-repeat", 100)
            assert values() == seeded
            actions = []
            for key, action, params, steps in [
                (ROBOT, "moveTo", {"location": "rack.A1"}, 800),
                (CENTRIFUGE, "closeLid", {}, 240),
                (CENTRIFUGE, "start", {"rpm": 600, "durationSeconds": 1}, 600),
            ]:
                started = client.post(
                    route + "/actions",
                    json={
                        "requestId": action,
                        "entityKey": key,
                        "action": action,
                        "params": params,
                    },
                )
                assert started.status_code == 202, started.text
                step("finish-" + action, steps)
                invocation = client.get(route + "/invocations/" + started.json()["invocationId"]).json()
                assert invocation["status"] == "succeeded", invocation
                actions.append((action, invocation["status"], values()))
            catalog = client.get(route + "/catalog").json()
            catalog.pop("sceneRevision")
            results.append(
                {
                    "catalog": catalog,
                    "initial": initial,
                    "seeded": seeded,
                    "actions": actions,
                    "alerts": source.catalog.alert_rules(),
                    "interlock": source.catalog.conditions(CENTRIFUGE, "closeLid"),
                    "manifest": {
                        name: client.get(route + "/scene").json()[name]
                        for name in ("nodes", "sites", "joints")
                    },
                }
            )
            assert client.delete(route).status_code == 204
            recorded = client.get(f"/recordings/{session_id}/recording.json").json()
            assert recorded["formatVersion"] == 2 and recorded["sceneVersion"] == version
            assert recorded["sceneContentHash"] == response.json()["sceneContentHash"]
        assert results[0] == results[1]
        assert client.get("/api/v1/library/scenes/lab-demo").json()["sceneVersion"] == "lab-demo@3"
        latest = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
        assert latest.status_code == 201 and latest.json()["sceneVersion"] == "lab-demo@3"


def test_native_migration_preserves_catalog_private_alerts_and_explicit_legacy_resource_keys(tmp_path):
    library = SceneLibrary(user_root=tmp_path / "user", state_root=tmp_path / "state")
    capture = BUILTIN_ROOT.parent / "assets/scene-previews/robot-and-tubes/1"
    manifest = SceneManifest.model_validate_json(next(capture.glob("*/newton/manifest.json")).read_bytes())
    old = library.resolve("robot-and-tubes@1", launch=True, source="newton")
    new = library.resolve("robot-and-tubes@2", launch=True, source="newton")
    before = load_info_catalog(manifest, old.name, scene_package=old)
    after = load_info_catalog(manifest, new.name, scene_package=new)
    assert before.model_dump(mode="json") == after.model_dump(mode="json")
    assert before.alert_rules() == after.alert_rules()
    assert before.conditions(CENTRIFUGE, "closeLid") == after.conditions(CENTRIFUGE, "closeLid")
    assert after.entities[ROBOT]["config"]["handBody"] == "/World/Lab/RobotA/fr3/fr3_hand"
    report = json.loads((new.path / "validation.json").read_bytes())
    effective = {
        binding["key"]: {k: v for k, v in binding.items() if k != "alerts"}
        for binding in report["effectiveBindings"]["newton"]
    }
    assert after.entities == effective
    assert len({key for key in effective if key.startswith("/World/Lab/Tubes/")}) == 20
    assert library.resolve("robot-and-tubes", launch=True).id == new.id

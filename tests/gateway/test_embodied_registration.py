import yaml
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import BUILTIN_ROOT


def test_robot_registration_preserves_builtin_and_promotion_boundaries():
    settings = Settings(remote=True, operator_token="test-operator", viewer_token="test-viewer")
    operator = {"Authorization": "Bearer test-operator"}
    viewer = {"Authorization": "Bearer test-viewer"}
    header = {"id": "my-controller", "title": "My Controller", "embodiment": "my-robot"}
    binding = {"scene": "robot-and-tubes@2", "entity": "/World/Lab/RobotA", "source": "newton"}
    embodiment = {"id": "my-robot", "title": "My Robot", "asset": "robot/fr3@1", "kind": "sim",
                  "controller": header["id"], "bindings": [binding]}
    model = yaml.safe_load((BUILTIN_ROOT / "robot-models/fr3-taught-motion.yaml").read_text())
    with TestClient(create_app(settings)) as http:
        for path, body in [("controllers", header), ("embodiments", embodiment), ("robot-models", model)]:
            response = http.post(f"/api/v1/{path}", json=body, headers=viewer)
            assert response.status_code == 403, response.text
        assert http.post("/api/v1/robot-models", json=model, headers=operator).status_code == 409
        assert http.post("/api/v1/embodiments", json=embodiment | {"id": "fr3-sim"},
                         headers=operator).status_code == 409
        assert http.post("/api/v1/controllers", json=header | {"id": "fr3-arm"},
                         headers=operator).status_code == 409
        assert http.post("/api/v1/embodiments", json=embodiment | {"bindings": [
            binding | {"entity": "/World/Lab/NotThisRobot"}]}, headers=operator).status_code == 409
        created = http.post("/api/v1/embodiments", json=embodiment, headers=operator)
        assert created.status_code == 201, created.text
        for fields in ({"currentVersion": 1}, {"version": 1}, {"versions": [{}]}):
            assert http.post("/api/v1/controllers", json=header | fields,
                             headers=operator).status_code == 422
        assert http.get("/api/v1/controllers/my-controller", headers=viewer).status_code == 404
        created = http.post("/api/v1/controllers", json=header, headers=operator)
        assert created.status_code == 201, created.text
        current = http.get("/api/v1/controllers/my-controller", headers=viewer).json()
        assert current["currentVersion"] is None and current["versions"] == []
        assert http.post("/api/v1/controllers/my-controller/versions", json={"version": 1},
                         headers=operator).status_code == 405
        assert http.post("/api/v1/embodiments", json=embodiment | {"title": "Changed"},
                         headers=operator).status_code == 409
        assert http.get("/api/v1/embodiments/my-robot", headers=viewer).json()["title"] == "My Robot"
        model |= {"id": "my-model", "embodiments": ["my-robot"]}
        created = http.post("/api/v1/robot-models", json=model, headers=operator)
        assert created.status_code == 201, created.text
        assert http.get("/api/v1/robot-models/my-model", headers=viewer).json() == created.json()


def test_entity_type_registration_is_versioned_and_immutable():
    settings = Settings(remote=True, operator_token="test-operator", viewer_token="test-viewer")
    operator = {"Authorization": "Bearer test-operator"}
    viewer = {"Authorization": "Bearer test-viewer"}
    route = "/api/v1/library/entity-types"
    declaration = {"type": "u-lab-bot/v1", "title": "Lab Robot", "actions": {
        "wave": {"longRunning": True, "preconditions": [{"property": "fallen", "equals": False}]}
    }}
    with TestClient(create_app(settings)) as http:
        assert http.post(route, json=declaration, headers=viewer).status_code == 403
        for name in ("biped/v1", "u-lab--bot/v1", "u-lab-bot/v01", "u-lab-bot/v0", "u-Bot/v1"):
            assert http.post(route, json=declaration | {"type": name}, headers=operator).status_code == 422
        assert http.post(route, json=declaration | {"properties": {
            "fallen": {"schema": {"type": "number"}}
        }}, headers=operator).status_code == 422
        created = http.post(route, json=declaration, headers=operator)
        assert created.status_code == 201, created.text
        published = created.json()
        assert len(published["contentHash"]) == 64
        assert published["properties"]["fallen"]["schema"] == {"type": "boolean"}
        assert published["actions"]["stop"]["concurrent"] is True
        assert http.get(route + "/u-lab-bot/v1", headers=viewer).json() == published
        assert http.post(route, json=declaration, headers=operator).status_code == 200
        assert http.post(route, json=declaration | {"title": "Changed"}, headers=operator).status_code == 409
        newer = http.post(route, json=declaration | {"type": "u-lab-bot/v2"}, headers=operator)
        assert newer.status_code == 201 and newer.json()["contentHash"] != published["contentHash"]
        ids = {item["type"] for item in http.get(route, headers=viewer).json()["types"]}
        assert {"biped/v1", "u-lab-bot/v1", "u-lab-bot/v2"} <= ids


def test_scene_import_requires_the_published_type_hash_and_injects_it():
    from io import BytesIO
    from zipfile import ZipFile

    from packages.info.evaluation_identity import freeze_identity, verify_identity
    from packages.training.inputs import pack_package

    declaration = {"type": "u-lab-bot/v1", "title": "Lab Bot"}
    root = "/api/v1/library"
    with TestClient(create_app(Settings())) as http:
        published = http.post(root + "/entity-types", json=declaration).json()
        stage = {
            "scene.yaml": {"name": "hash-probe", "title": "Hash test", "sources": ["synthetic"],
                           "stage": "python:lab-demo"},
            "devices.yaml": {"scene": "hash-probe", "entities": [
                {"key": "/World/Lab/RobotA", "type": declaration["type"], "program": "unavailable"}
            ]},
            "locations.yaml": {},
        }
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from types import SimpleNamespace

        import pytest

        def publish(binding):
            stage["devices.yaml"]["entities"][0] = binding
            with TemporaryDirectory(dir=http.app.state.settings.state_root) as temporary:
                directory = Path(temporary)
                for name, doc in stage.items():
                    (directory / name).write_text(yaml.safe_dump(doc), encoding="utf-8")
                return http.post(root + "/imports", content=pack_package(directory),
                                 headers={"Content-Type": "application/x-tar"})

        binding = stage["devices.yaml"]["entities"][0]
        bad = publish(binding | {"typeContentHash": "0" * 64})
        assert bad.status_code == 409, bad.text
        created = publish(binding)
        assert created.status_code == 200, created.text
        scene = http.app.state.manager.library.resolve(created.json()["sceneVersion"])
        assert scene.bindings["entities"][0]["typeContentHash"] == published["contentHash"]
        assert publish(binding).json()["sceneVersion"] == scene.id

        with TemporaryDirectory(dir=http.app.state.settings.state_root) as temporary:
            directory = Path(temporary)
            suite = SimpleNamespace(id="suite/probe@1", content_hash="a" * 64, path=scene.path,
                                    metadata={"source": "synthetic"}, grading={})
            candidate = {"kind": "fixture", "contentHash": "b" * 64}
            first = freeze_identity("first", suite, scene, None, directory, candidate=candidate)
            assert first["environmentInputs"]["inputs"]["entityTypes"] == [
                {"id": declaration["type"], "contentHash": published["contentHash"]}
            ]
            with ZipFile(BytesIO((directory / first["recovery"]["file"]).read_bytes())) as recovery:
                assert recovery.read("entity-types/u-lab-bot/v1.yaml")
            other = http.post(root + "/entity-types", json=declaration | {"type": "u-unrelated/v1"})
            other.raise_for_status()
            second = freeze_identity("second", suite, scene, None, directory, candidate=candidate)
            assert first["staticIdentity"] == second["staticIdentity"]
            user_root = http.app.state.library_assets.roots["user"]
            verify_identity(first, directory=directory, user_root=user_root)
            type_path = http.app.state.library_assets.roots["user"] / "device-types/u-lab-bot/v1.yaml"
            type_path.write_bytes(type_path.read_bytes() + b"\n")
            with pytest.raises(ValueError, match="EntityType"):
                verify_identity(first, directory=directory, user_root=user_root)

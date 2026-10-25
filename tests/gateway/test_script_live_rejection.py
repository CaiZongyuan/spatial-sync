"""Actual Live Gateway dispatch rejects valid Script material before any execution or transport."""

import math
import time

import httpx
import pytest
import yaml

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.live.bindings import load_bindings
from packages.policies.subjects import fixed_bindings
from packages.programs.robot_arm.script import ScriptProgram
from packages.training.inputs import pack_package
from tests.fixtures.script_program import SOURCE, program_directory
from tests.gateway.test_live_devices import EXAMPLE, FakeBrokerTransport


async def test_live_gateway_rejects_canonical_script_before_candidate_or_source_execution(tmp_path):
    sources, transports = [], []

    def forbidden(*args):
        sources.append(args)
        raise AssertionError("Live rejection test must create no spatial Source")

    def transport(broker, topics):
        value = FakeBrokerTransport(broker, topics, time.monotonic)
        transports.append(value)
        return value

    assert not hasattr(math, "_lw_uploaded_program_marker")
    assert not hasattr(math, "_lw_uploaded_constructor_marker")
    app = create_app(Settings(live_bindings=EXAMPLE), source_factory=forbidden,
                     live_transport_factory=transport)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://live") as http:
            health = (await http.get("/api/v1/health")).json()
            assert health["live"] == {"enabled": True, "state": "RUNNING"}
            assert (await http.get("/api/v1/live/entities")).status_code == 200
            assert len(transports) == 1 and not sources
            directory, _ = program_directory(tmp_path)
            (directory / "controller.py").write_bytes(
                SOURCE + b"\n    def __init__(self):\n        math._lw_uploaded_constructor_marker = True\n"
            )
            response = await http.post("/api/v1/programs", content=pack_package(directory),
                                       headers={"Content-Type": "application/x-tar"})
            assert response.status_code == 201, response.text
            program = response.json()
            scene = app.state.manager.library.resolve("robot-and-tubes@2", launch=True, source="newton")
            original = fixed_bindings(scene, "newton")["/World/Lab/RobotA"]
            config = {key: value for key, value in original["config"].items()
                      if key in ScriptProgram.Config.model_fields}
            config.update(programArtifact={key: program[key] for key in ("id", "contentHash")},
                          parameters=program["metadata"]["config"])
            canonical = ScriptProgram.Config.model_validate(config).model_dump(mode="json")
            document = {"live": "script-denial", "broker": {"url": "mqtt://127.0.0.1:1883"},
                        "entities": [{"key": "/Real/RobotA", "type": "robot-arm/v1",
                                      "program": "robot_arm.script", "config": canonical}]}
            path = tmp_path / "live-script.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")
            parsed = load_bindings(path)
            assert parsed.layout is None and parsed.entities[0].type == ScriptProgram.type
            assert parsed.entities[0].config == canonical
            with pytest.raises(ValueError, match="matching registered MQTT program/type"):
                create_app(Settings(live_bindings=path), source_factory=forbidden,
                           live_transport_factory=transport)
            assert len(transports) == 1 and not sources
            assert not hasattr(math, "_lw_uploaded_program_marker")
            assert not hasattr(math, "_lw_uploaded_constructor_marker")
    assert transports[0].closed and app.state.manager.live.state == "STOPPED"

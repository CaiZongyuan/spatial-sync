"""Actual public import, CPU validator, Runner, stepping and recording clock."""

from copy import deepcopy

import pytest
import yaml
from fastapi.testclient import TestClient

pytest.importorskip("newton")
pytest.importorskip("pxr.Usd")

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.log import read_segment
from packages.training.inputs import pack_package

pytestmark = pytest.mark.newton


def clock_package(directory, name, *, usd_dt=None, metadata_dt=None):
    directory.mkdir()
    stage = Usd.Stage.CreateNew(str(directory / "stage.usda"))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.Xform.Define(stage, "/World/Lab")
    floor = UsdGeom.Cube.Define(stage, "/World/Lab/Floor")
    floor.CreateSizeAttr(1.0)
    floor.AddTranslateOp().Set(Gf.Vec3d(0, 0, -.05))
    floor.AddScaleOp().Set(Gf.Vec3f(1, 1, .1))
    UsdPhysics.CollisionAPI.Apply(floor.GetPrim())
    box = UsdGeom.Cube.Define(stage, "/World/Lab/Box")
    box.CreateSizeAttr(.1)
    box.AddTranslateOp().Set(Gf.Vec3d(0, 0, .05))
    UsdPhysics.CollisionAPI.Apply(box.GetPrim())
    UsdPhysics.RigidBodyAPI.Apply(box.GetPrim())
    UsdPhysics.MassAPI.Apply(box.GetPrim()).CreateMassAttr(1.)
    physics = UsdPhysics.Scene.Define(stage, "/World/Lab/Physics")
    physics.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    physics.CreateGravityMagnitudeAttr(9.81)
    if usd_dt is not None:
        for key, kind, value in (
            ("timestep", Sdf.ValueTypeNames.Double, usd_dt),
            ("iterations", Sdf.ValueTypeNames.Int, 60),
            ("ls_iterations", Sdf.ValueTypeNames.Int, 50),
            ("noslip_iterations", Sdf.ValueTypeNames.Int, 4),
            ("integrator", Sdf.ValueTypeNames.Token, "implicitfast"),
            ("cone", Sdf.ValueTypeNames.Token, "elliptic"),
        ):
            physics.GetPrim().CreateAttribute("mjc:option:" + key, kind, custom=True).Set(value)
    stage.GetRootLayer().Save()
    simulation = {"solver": "mujoco", "warmupSteps": 1}
    if metadata_dt is not None:
        simulation["physicsDt"] = metadata_dt
    for filename, document in {
        "scene.yaml": {"name": name, "title": name, "stage": "stage.usda",
                       "sources": ["newton"], "simulation": simulation},
        "devices.yaml": {"scene": name, "entities": []}, "locations.yaml": {"locations": {}},
    }.items():
        (directory / filename).write_text(yaml.safe_dump(document))
    return directory


def upload(client, directory):
    result = client.post("/api/v1/library/imports", content=pack_package(directory),
                         headers={"Content-Type": "application/x-tar"})
    assert result.status_code == 200, result.text
    return result.json()


def control(client, route, name, **args):
    response = client.post(route + "/commands", json={
        "requestId": "clock-" + name, "command": name, "args": args})
    assert response.status_code == 200, response.text
    assert response.json()["ack"]["status"] == "applied", response.json()


def test_public_clock_paths_keep_legacy_timing_and_bind_new_validation_runner_and_recording(tmp_path):
    app = create_app(Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings",
                              scene_previews_root=tmp_path / "previews"))
    with TestClient(app) as client:
        for label, usd_dt, metadata_dt, expected in (
            ("legacy", None, None, 1 / 240),
            ("declared", .0005, .0005, .0005),
            ("usd-only", .0005, None, .0005),
        ):
            source = clock_package(tmp_path / label, "clock-" + label,
                                   usd_dt=usd_dt, metadata_dt=metadata_dt)
            imported = upload(client, source)
            report = imported["validation"]
            assert report["launchable"], report
            assert report["physics"]["physicsDt"] == pytest.approx(expected)
            assert report["physics"]["simulationSeconds"] == pytest.approx(2.)
            if usd_dt is not None:
                assert report["physics"]["solverOptions"] == {
                    "iterations": 60, "ls_iterations": 50, "noslip_iterations": 4,
                    "integrator": 3, "cone": 1}
            duplicate = upload(client, source)
            assert duplicate["sceneVersion"] == imported["sceneVersion"]
            created = client.post("/api/v1/sessions", json={"sceneVersion": imported["sceneVersion"]})
            assert created.status_code == 201, created.text
            sid = created.json()["sessionId"]
            route = "/api/v1/sessions/" + sid
            manifest = client.get(route + "/scene").json()
            assert manifest["source"]["physicsDt"] == pytest.approx(expected)
            assert app.state.manager.get(sid).options.physicsDt == pytest.approx(expected)
            control(client, route, "pause")
            before = client.get(route + "/pose", params={"key": "/World/Lab/Box"}).json()
            control(client, route, "step", count=10)
            after = client.get(route + "/pose", params={"key": "/World/Lab/Box"}).json()
            assert after["simTime"] - before["simTime"] == pytest.approx(10 * expected)
            assert client.delete(route).status_code == 204
            recording = "/recordings/" + sid
            assert client.get(recording + "/recording.json").json()["formatVersion"] == 2
            recorded = client.get(recording + "/manifest.json").json()
            assert recorded["source"]["physicsDt"] == pytest.approx(expected)
            assert not read_segment(client.get(recording + "/seg-000001.bin").content).truncated
            # A controlled pre-clock report is not proof of this authored clock.
            if usd_dt is not None:
                version = app.state.manager.library.resolve(imported["sceneVersion"])
                stale = deepcopy(version.report())
                stale["physics"].pop("physicsDt")
                version.reports.save(stale, version.content_hash)
                rejected = client.post("/api/v1/sessions", json={"sceneVersion": version.id})
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["detail"]["code"] == "scene_clock_unqualified"
                assert not any(item.holds_resources for item in app.state.manager.platform_sessions())


def test_public_import_refuses_conflicting_clock_before_a_newton_session(tmp_path):
    source = clock_package(tmp_path / "conflict", "clock-conflict", usd_dt=.0005, metadata_dt=.001)
    with TestClient(create_app(Settings(scene_previews_root=tmp_path / "previews"))) as client:
        imported = upload(client, source)
        assert not imported["validation"]["launchable"]
        assert any(row["id"] == "S2" and row["status"] == "fail"
                   and "physicsDt differs" in row["message"] for row in imported["validation"]["checks"])
        rejected = client.post("/api/v1/sessions", json={"sceneVersion": imported["sceneVersion"]})
        assert rejected.status_code == 409
        assert client.get("/api/v1/health").json()["sessions"] == []

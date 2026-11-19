"""External robot archives use the public immutable Asset transaction."""

import hashlib
import json
import math

import pytest
from fastapi.testclient import TestClient
from test_asset_imports import upload

from apps.api.app.main import create_app
from packages.library.physics import skipped

URDF = b"""<robot name="probe">
  <link name="base"><collision><geometry><box size=".2 .4 .6"/></geometry></collision></link>
  <link name="tip"><collision><geometry><box size=".1 .1 .1"/></geometry></collision></link>
  <joint name="motor-a" type="revolute">
    <parent link="base"/><child link="tip"/><origin xyz=".2 0 0"/>
    <axis xyz="-1 0 0"/><limit lower="-.4" upper=".7" effort="20" velocity="1"/>
  </joint>
</robot>"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(tmp_path / "state"))
    # Core CI exercises the optional-SDK absence path. Native CPU evidence uses
    # the same HTTP entry with the installed Newton and MuJoCo distributions.
    monkeypatch.setattr("packages.library.validation.physics_availability",
                        lambda _: (skipped("Fixture has no Newton wheels", ["newton"]), {}))
    with TestClient(create_app()) as test_client:
        yield test_client


def files(entry="probe.urdf", model=URDF):
    return {
        "robot.json": json.dumps({
            "schema": "lw.robot-import/1",
            "id": "robot/calibration-probe", "title": "Calibration Probe", "entry": entry,
            "license": "LICENSE", "licenseId": "CC0-1.0",
            "calibration": {"density": 250., "joints": {"motor-a": {"zeroOffset": .25}}},
        }).encode(),
        entry: model,
        "LICENSE": b"Fixture created for testing; CC0-1.0\n",
    }


def test_robot_archive_publishes_pinned_calibration(client):
    response = upload(client, files())
    assert response.status_code == 200, response.text
    identity = response.json()
    assert identity["assetVersion"] == "robot/calibration-probe@1"
    assert upload(client, files(), reverse=True).json()["deduplicated"]
    detail = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    assert detail["metadata"]["physics"]["massSource"] == "estimated"
    assert not detail["validation"]["launchable"]
    assert detail["validation"]["physics"]["status"] == "skipped"
    mapping = client.get(detail["files"]["joint-calibration.json"]).json()
    assert mapping["schema"] == "lw.joint-calibration/1"
    assert mapping["joints"] == [
        {"jointKey": "/Asset/joints/motor_a", "sourceName": "motor-a",
         "sign": -1, "zeroOffset": .25, "unit": "rad"},
    ]
    joint = mapping["joints"][0]
    assert joint["sign"] * (.40 - joint["zeroOffset"]) == pytest.approx(-.15)
    visual = client.get(detail["files"]["visual.glb"])
    assert visual.content.startswith(b"glTF")
    assert client.get(detail["files"]["LICENSE"]).content == files()["LICENSE"]
    provenance = client.get(detail["files"]["provenance.json"]).json()
    assert provenance["files"]["source/probe.urdf"]["sha256"] == hashlib.sha256(URDF).hexdigest()

    path = "/api/v1/library/assets/robot/calibration-probe/1/calibrate"
    recipe = {"density": 250., "scale": 2., "upAxis": "Y", "friction": .37,
              "joints": {"motor-a": {"zeroOffset": .25, "defaultPosition": .4, "kp": 15., "kv": 2.}},
              "sites": {"tcp": {"body": "tip", "positionM": [.1, 0, 0]}}}
    response = client.post(path, json=recipe)
    assert response.status_code == 200, response.text
    assert response.json()["assetVersion"] == "robot/calibration-probe@2"
    assert client.post(path, json=recipe).json()["deduplicated"]
    new = client.get("/api/v1/library/assets/robot/calibration-probe/2").json()
    new_provenance = client.get(new["files"]["provenance.json"]).json()
    assert new_provenance["calibratedFrom"] == {
        "id": identity["assetVersion"], "contentHash": identity["contentHash"],
    }
    original = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    assert original["contentHash"] == identity["contentHash"]
    baked = client.get(new["files"]["asset.usda"]).text
    assert 'uniform token upAxis = "Z"' not in baked  # stage metadata, not a prim property
    assert 'upAxis = "Z"' in baked and "metersPerUnit = 1" in baked
    assert "float physics:mass = 96" in baked
    assert 'quatf physics:principalAxes = (1, 0, 0, 0)' in baked
    assert 'def Xform "tcp"' in baked
    assert "float physics:dynamicFriction = 0.37" in baked
    assert str(round(math.degrees(.4), 3))[:5] in baked


def test_robot_archive_dependency_and_topology_gaps_are_local(client):
    cases = [
        ("probe.urdf", URDF.replace(b'<axis xyz=', b'<mimic joint="external"/><axis xyz='),
         "Unsupported URDF joint"),
        ("probe.urdf", URDF.replace(b"<robot", b"<!DOCTYPE robot><robot"), "DTD and entity"),
        ("probe.urdf", URDF.replace(b"<robot", b"<xacro:robot"), "Expand xacro"),
        ("probe.urdf", URDF.replace(b'<box size=".2 .4 .6"/>', b'<mesh filename="package://host/robot.stl"/>'),
         "Unknown archive package"),
        ("probe.xml", b'<mujoco><include file="../outside.xml"/></mujoco>', "escapes"),
        ("probe.xml", b'<mujoco><include file="probe.xml"/></mujoco>', "Recursive"),
        ("probe.xml", b'<mujoco><asset><mesh file="missing.stl"/></asset></mujoco>',
         "Missing or unsupported"),
        ("probe.xml", b'<mujoco><extension><plugin plugin="outside"/></extension></mujoco>',
         "Unsupported MJCF"),
        ("probe.usda", b'#usda 1.0\n( subLayers = [@../escaped.usda@] )\n', "escapes its package"),
    ]
    for entry, model, message in cases:
        response = upload(client, files(entry, model))
        assert response.status_code == 422, response.text
        problem = response.json()["detail"]["problems"][0]
        assert message in problem["message"], problem
        assert problem["path"]


def test_included_mjcf_and_usd_round_trip_at_public_asset_boundary(client, tmp_path):
    mujoco = pytest.importorskip("mujoco")
    from apps.node.usd_mjcf import derive_usd_mjcf

    source = files(
        "probe.xml", b'<mujoco><compiler angle="radian"/><include file="assembly/body.xml"/></mujoco>',
    )
    source["assembly/body.xml"] = b"""
<mujocoinclude><worldbody><body name="base" pos="0 0 .3">
  <geom type="box" size=".1 .2 .3" mass="3"/>
  <body name="tip" pos=".2 0 0">
    <joint name="motor-a" axis="-1 0 0" range="-.4 .7"/>
    <geom type="box" size=".05 .05 .05" mass=".3" pos="0 0 .1"/>
  </body>
</body></worldbody></mujocoinclude>
"""
    first = upload(client, source)
    assert first.status_code == 200, first.text
    item = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    baked = client.get(item["files"]["asset.usda"]).content
    path = tmp_path / "generated.usda"
    path.write_bytes(baked)
    result = derive_usd_mjcf(path)
    joint = result.joints[0]
    assert (joint.lower, joint.upper) == pytest.approx((-.45, .65))
    derived = result.to_mjmodel()
    data = mujoco.MjData(derived)
    data.qpos[derived.joint(joint.name).qposadr[0]] = .4
    mujoco.mj_forward(derived, data)
    # q_source=-.15 rad about -X is +.15 rad about X. The child geom's
    # authored +.1 m offset along Z rotates relative to the fixed hinge.
    geom = next(name for prim, name in result.collider_names_by_path.items() if "/tip/" in prim)
    assert data.geom_xpos[derived.geom(geom).id].tolist() == pytest.approx(
        [.2, -.1 * math.sin(.15), .3 + .1 * math.cos(.15)]
    )
    manifest = {"id": "robot/usd-probe", "title": "USD Probe", "entry": "probe.usda",
                "license": "LICENSE", "licenseId": "CC0-1.0"}
    usd_source = {
        "robot.json": json.dumps(manifest).encode(), "probe.usda": baked, "LICENSE": source["LICENSE"],
    }
    reply = upload(client, usd_source)
    assert reply.status_code == 200, reply.text
    second = client.get("/api/v1/library/assets/robot/usd-probe/1").json()
    mapping = client.get(second["files"]["joint-calibration.json"]).json()
    assert mapping["joints"][0]["sourceName"] == "/Asset/joints/motor_a"

"""External robot archives use the public immutable Asset transaction."""

import gzip
import hashlib
import io
import json
import math

import pytest
import trimesh
from fastapi.testclient import TestClient
from test_asset_imports import archive, upload

from apps.api.app.main import create_app
from packages.library.physics import skipped

URDF = b"""<robot name="probe">
  <link name="base">
    <collision><geometry><box size=".2 .4 .6"/></geometry></collision>
    <visual><geometry><box size=".2 .4 .6"/></geometry>
      <material name="paint"><color rgba=".2 .4 .6 1"/></material>
    </visual>
  </link>
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
    assert visual.status_code == 200
    scene = trimesh.load_scene(io.BytesIO(visual.content), file_type="glb", process=False)
    colors = {tuple(color) for mesh in scene.geometry.values() for color in mesh.visual.vertex_colors}
    assert colors == {(51, 102, 153, 255), (166, 166, 166, 255)}
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


def test_closed_stl_collision_imports_with_volume_and_rejects_bad_components(client):
    model = URDF.replace(b'<box size=".2 .4 .6"/>', b'<mesh filename="frame.stl"/>', 1)
    source = files(model=model)
    cube = trimesh.creation.box(extents=(.2, .4, .6))
    source["frame.stl"] = cube.export(file_type="stl")
    response = upload(client, source)
    assert response.status_code == 200, response.text
    detail = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    assert detail["metadata"]["physics"]["massKg"] == pytest.approx(12.25)
    assert "float physics:mass = 12" in client.get(detail["files"]["asset.usda"]).text

    inner = trimesh.creation.box(extents=(.05, .05, .05))
    inner.apply_translation([1., 0., 0.])
    inner.invert()
    inverted_component = trimesh.util.concatenate([cube, inner])
    for mesh in (cube.submesh([range(len(cube.faces) - 1)], append=True), inverted_component):
        response = upload(client, source | {"frame.stl": mesh.export(file_type="stl")})
        assert response.status_code == 422, response.text
        problem = response.json()["detail"]["problems"][0]
        assert "closed, positive-volume components" in problem["message"]
        assert problem["path"] == "/Asset/links/base"


def test_gzip_robot_upload_deduplicates_against_bare_tar(client, monkeypatch):
    raw = archive(files())
    accepted = client.post("/api/v1/library/imports", content=gzip.compress(raw),
                           headers={"Content-Type": "application/gzip"})
    assert accepted.status_code == 200, accepted.text
    bare = upload(client, files()).json()
    assert bare["deduplicated"] and bare["contentHash"] == accepted.json()["contentHash"]
    # Verify the compressed stream is checked to EOF, including its trailer.
    broken = gzip.compress(raw)[:-4]
    response = client.post("/api/v1/library/imports", content=broken,
                           headers={"Content-Type": "application/x-tar"})
    assert response.status_code == 422, response.text
    monkeypatch.setattr("packages.library.archive.MAX_PACKAGE_BYTES", 512)
    monkeypatch.setattr("packages.library.archive.MAX_FILES", 16)
    over_budget = gzip.compress(raw + b"\0" * 64000)
    response = client.post("/api/v1/library/imports", content=over_budget,
                           headers={"Content-Type": "application/gzip"})
    assert response.status_code == 422
    assert "Expanded tar stream exceeds" in response.text
    response = client.post("/api/v1/library/imports", content=gzip.compress(raw),
                           headers={"Content-Type": "application/gzip"})
    assert response.status_code == 422
    assert "Expanded package exceeds" in response.text
    monkeypatch.setattr("packages.library.archive.MAX_PACKAGE_BYTES", 8 * 1024)
    monkeypatch.setattr("packages.library.archive.MAX_FILES", 2)
    response = client.post("/api/v1/library/imports", content=gzip.compress(raw),
                           headers={"Content-Type": "application/gzip"})
    assert response.status_code == 422
    assert "Package exceeds 2 entries" in response.text


def test_mjcf_joint_actuator_force_limit_preserves_applied_torque(client, tmp_path):
    import mujoco
    from pxr import Usd, UsdPhysics

    from apps.node.usd_mjcf import derive_usd_mjcf

    model = b"""<mujoco><compiler angle="degree"/><worldbody>
      <body name="base" pos="0 0 .5"><geom type="box" size=".15 .12 .10" mass="2"/>
        <body name="tip" pos=".2 0 0">
          <joint name="motor-a" axis="0 0 -1" range="-30 70" ref="10" actuatorfrcrange="-3 3"/>
          <geom type="sphere" size=".05" mass=".25"/>
        </body>
      </body></worldbody><actuator><position joint="motor-a" kp="10" kv="1"/></actuator></mujoco>"""
    source = mujoco.MjModel.from_xml_string(model.decode())
    source_data = mujoco.MjData(source)
    source_data.ctrl[0] = .9
    mujoco.mj_forward(source, source_data)
    assert source_data.qfrc_actuator[0] == pytest.approx(3.)

    reply = upload(client, files("probe.xml", model))
    assert reply.status_code == 200, reply.text
    detail = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    path = tmp_path / "baked.usda"
    path.write_bytes(client.get(detail["files"]["asset.usda"]).content)
    stage = Usd.Stage.Open(str(path))
    drive = UsdPhysics.DriveAPI(stage.GetPrimAtPath("/Asset/joints/motor_a"), "angular")
    assert drive.GetMaxForceAttr().Get() == pytest.approx(3.)
    result = derive_usd_mjcf(stage)
    derived = result.to_mjmodel()
    data = mujoco.MjData(derived)
    data.qpos[0] = result.joints[0].initial_position
    data.ctrl[0] = -.9 + .25
    mujoco.mj_forward(derived, data)
    assert data.qfrc_actuator[0] == pytest.approx(-3.)

    for changed, expected in (
        (model.replace(b'="-3 3"', b'="-3 4"'), "symmetric"),
        (model.replace(b'ref="10"', b'ref="10" damping="1"'), "passive damping"),
    ):
        reply = upload(client, files("probe.xml", changed))
        assert reply.status_code == 422, reply.text
        problem = reply.json()["detail"]["problems"][0]
        assert problem["path"] == "motor-a"
        assert expected in problem["message"]


def test_included_mjcf_and_usd_round_trip_at_public_asset_boundary(client, tmp_path):
    import mujoco

    from apps.node.usd_mjcf import derive_usd_mjcf

    source = files("probe.xml", b'<mujoco><include file="setup/settings.xml"/>'
                   b'<include file="assembly/body.xml"/></mujoco>')
    source["setup/settings.xml"] = (
        b'<mujocoinclude><compiler angle="radian" meshdir="meshes"/></mujocoinclude>'
    )
    source["meshes/shape.stl"] = trimesh.creation.box(extents=(.1, .1, .1)).export(file_type="stl")
    source["assembly/body.xml"] = b"""
<mujocoinclude><asset><mesh file="shape.stl"/></asset><worldbody><body name="base" pos="0 0 .3">
  <geom type="box" size=".1 .2 .3" mass="3"/>
  <body name="tip" pos=".2 0 0">
    <joint name="motor-a" axis="-1 0 0" range="-.4 .7"/>
    <geom type="mesh" mesh="shape" mass=".3" pos="0 0 .1"/>
  </body>
</body></worldbody></mujocoinclude>
"""
    for name, value in source.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    native = mujoco.MjModel.from_xml_path(str(tmp_path / "probe.xml"))
    assert native.nmesh == 1
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
    no_mesh = {name: value for name, value in source.items() if name != "meshes/shape.stl"}
    reply = upload(client, no_mesh)
    assert reply.status_code == 422
    assert "meshes/shape.stl" in reply.text

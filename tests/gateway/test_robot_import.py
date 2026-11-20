"""External robot archives use the public immutable Asset transaction."""

import gzip
import hashlib
import io
import json
import math
import zipfile

import pytest
import trimesh
from fastapi.testclient import TestClient
from test_asset_imports import archive, package_files, upload

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.physics import skipped
from tests.gateway.test_training_jobs import TOKEN

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
    with TestClient(create_app(Settings(node_token=TOKEN))) as test_client:
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
    route = "/api/v1/library/imports?robotSource=true"
    wrong_package = client.post(
        route, content=archive(package_files()), headers={"Content-Type": "application/x-tar"},
    )
    assert wrong_package.status_code == 422
    assert wrong_package.json()["detail"]["problems"][0]["path"] == "robot.json"
    assert client.get("/api/v1/library/assets/labware/test-tube/1").status_code == 404
    accepted = client.post(route, content=gzip.compress(raw),
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


def test_urdf_passive_damping_survives_calibrated_drive_and_effort_limit(client, tmp_path):
    import mujoco

    from apps.node.usd_mjcf import derive_usd_mjcf

    raw = URDF.replace(b'effort="20" velocity="1"/>', b'effort="3" velocity="5"/><dynamics damping="4"/>')
    source = mujoco.MjModel.from_xml_string(raw.decode())
    data = mujoco.MjData(source)
    data.qvel[0] = 2.
    mujoco.mj_forward(source, data)
    assert data.qfrc_passive[0] == pytest.approx(-8.)
    assert upload(client, files(model=raw)).status_code == 200

    path = "/api/v1/library/assets/robot/calibration-probe"
    reply = client.post(path + "/1/calibrate", json={
        "fixedBase": True, "joints": {"motor-a": {"kp": 10., "kv": 0.}},
    })
    assert reply.status_code == 200, reply.text
    item = client.get(path + "/2").json()
    stage = tmp_path / "baked.usda"
    stage.write_bytes(client.get(item["files"]["asset.usda"]).content)
    model = derive_usd_mjcf(stage).to_mjmodel()
    state = mujoco.MjData(model)
    state.qvel[0] = -2.
    state.ctrl[0] = -.4
    mujoco.mj_forward(model, state)
    assert state.qfrc_passive[0] == pytest.approx(8.)
    assert state.qfrc_actuator[0] == pytest.approx(-3.)


def test_mjcf_position_control_range_survives_negative_axis_and_zero_offset(client, tmp_path):
    import mujoco

    from apps.node.usd_mjcf import derive_usd_mjcf

    raw = b"""<mujoco><compiler angle="radian"/><worldbody><body name="base">
    <geom type="box" size=".1 .1 .1" mass="2"/><body name="tip" pos=".4 0 0">
    <joint name="motor-a" axis="-1 0 0" range="-1 1"/><geom size=".05" mass=".25"/>
    </body></body></worldbody><actuator>
    <position name="drive" joint="motor-a" kp="10" ctrlrange="-.1 .2" forcerange="-15 15"/>
    </actuator></mujoco>"""
    original = mujoco.MjModel.from_xml_string(raw.decode())
    source_data = mujoco.MjData(original)
    source_data.ctrl[0] = .8
    mujoco.mj_forward(original, source_data)
    assert source_data.qfrc_actuator[0] == pytest.approx(2.)

    reply = upload(client, files("probe.xml", raw))
    assert reply.status_code == 200, reply.text
    detail = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    stage = tmp_path / "baked.usda"
    stage.write_bytes(client.get(detail["files"]["asset.usda"]).content)
    model = derive_usd_mjcf(stage).to_mjmodel()
    assert model.actuator_ctrllimited.tolist() == [True]
    assert model.actuator_ctrlrange[0] == pytest.approx([.05, .35])
    data = mujoco.MjData(model)
    data.qpos[0] = .25
    data.ctrl[0] = -.8 + .25
    mujoco.mj_forward(model, data)
    assert data.qfrc_actuator[0] == pytest.approx(-2.)
    assert client.get(detail["files"]["visual.glb"]).content.startswith(b"glTF")


def test_fixed_mjcf_keeps_world_welded_parent_contact_eligibility(tmp_path, monkeypatch):
    pytest.importorskip("newton")
    import mujoco

    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))
    monkeypatch.setenv("SPATIAL_STATE_ROOT", str(tmp_path / "state"))
    raw = b"""<mujoco><compiler angle="radian"/><worldbody><body name="base" pos="0 0 .3">
    <geom name="base-box" type="box" size=".15 .12 .1" mass="2"/><body name="tip" pos=".31 0 0">
    <joint name="motor-a" range="-1 1"/><geom name="tip-ball" size=".05" mass=".25"/>
    </body></body></worldbody></mujoco>"""
    source = mujoco.MjModel.from_xml_string(raw.decode())
    assert source.body_weldid[source.body("base").id] == 0
    assert source.body_weldid[source.body("tip").id] != 0
    # Move only the collider in a native probe to witness that the separated
    # source pair is eligible even with the default parent filter on.
    source.geom("tip-ball").pos[0] = -.14
    mujoco.mj_setConst(source, mujoco.MjData(source))
    state = mujoco.MjData(source)
    mujoco.mj_forward(source, state)
    assert state.ncon == 1

    with TestClient(create_app()) as test_client:
        result = upload(test_client, files("probe.xml", raw))
    assert result.status_code == 200, result.text
    report = result.json()["validation"]
    checks = {row["id"]: row for row in report["checks"]}
    assert report["launchable"], checks
    assert checks["P7"]["status"] == "pass"
    assert checks["P7"]["measurements"]["collidableGeometryPairCount"] == 1
    assert checks["P8"]["status"] == "pass"


def test_mjcf_inferred_inertia_uses_recipe_density_and_keeps_authored_mass(client):
    model = b"""<mujoco><default><default class="authored"><geom density="1000"/></default></default>
    <worldbody><body name="base"><geom type="box" size=".15 .12 .1"/>
      <body name="tip" pos=".4 0 0"><joint name="motor-a" range="-1 1"/>
        <geom size=".05" class="authored"/>
      </body>
      <body name="authored_link" pos="0 .4 0"><joint name="motor-b" range="-1 1"/>
        <inertial mass=".5" pos="0 0 0" diaginertia=".005 .005 .005"/>
        <geom size=".05"/>
      </body>
    </body></worldbody></mujoco>"""
    reply = upload(client, files("probe.xml", model))
    assert reply.status_code == 200, reply.text
    item = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    expected = 250 * 8 * .15 * .12 * .1 + 1000 * 4 * math.pi * .05**3 / 3 + .5
    assert item["metadata"]["physics"] == {
        "collision": "authored", "massKg": pytest.approx(expected), "massSource": "estimated",
    }
    provenance = client.get(item["files"]["provenance.json"]).json()
    assert set(provenance["estimatedMassPrims"]) == {"/Asset/links/base", "/Asset/links/tip"}
    assert client.get(item["files"]["source/probe.xml"]).content == model


@pytest.mark.parametrize("mass_mode", ["implicit", "zero", "collider"])
def test_usd_import_preserves_si_joint_dynamics_and_partial_inertia(client, tmp_path, mass_mode):
    import mujoco
    import numpy as np
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    from apps.node.usd_mjcf import derive_usd_mjcf

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, .01)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdPhysics.SetStageKilogramsPerUnit(stage, .5)
    root = UsdGeom.Xform.Define(stage, "/Robot").GetPrim()
    stage.SetDefaultPrim(root)
    UsdPhysics.ArticulationRootAPI.Apply(root)
    for name, x in (("base", 0), ("arm", 20), ("tip", 40)):
        link = UsdGeom.Xform.Define(stage, f"/Robot/{name}")
        link.AddTranslateOp().Set(Gf.Vec3d(x, 0, 20))
        UsdPhysics.RigidBodyAPI.Apply(link.GetPrim())
        collider = UsdGeom.Cube.Define(stage, f"/Robot/{name}/box")
        collider.CreateSizeAttr(8)
        UsdPhysics.CollisionAPI.Apply(collider.GetPrim())
        mass = UsdPhysics.MassAPI.Apply(link.GetPrim())
        mass.CreateDiagonalInertiaAttr(Gf.Vec3f(8, 9, 10))
        if name != "tip":
            mass.CreateCenterOfMassAttr(Gf.Vec3f(1, 2, 3))
        if name == "arm":
            if mass_mode == "zero":
                mass.CreateMassAttr(0.)
            mass.CreateDensityAttr(.001)
            mass.CreatePrincipalAxesAttr(Gf.Quatf(math.sqrt(.5), Gf.Vec3f(0, 0, math.sqrt(.5))))
            if mass_mode == "collider":
                UsdPhysics.MassAPI.Apply(collider.GetPrim()).CreateMassAttr(.9)
        else:
            mass.CreateMassAttr(1.)
        if name == "tip":
            collider.AddTranslateOp().Set(Gf.Vec3d(4, 0, 0))
            small_box = UsdGeom.Cube.Define(stage, "/Robot/tip/small_box")
            small_box.CreateSizeAttr(4)
            small_box.AddTranslateOp().Set(Gf.Vec3d(13, 0, 0))
            UsdPhysics.CollisionAPI.Apply(small_box.GetPrim())
            UsdPhysics.MassAPI.Apply(small_box.GetPrim()).CreateMassAttr(0.)
    fixed = UsdPhysics.FixedJoint.Define(stage, "/Robot/anchor")
    fixed.CreateBody1Rel().SetTargets(["/Robot/base"])
    fixed.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 20))
    for kind, channel, name, parent, child, dynamics in (
        (UsdPhysics.RevoluteJoint, "angular", "hinge", "base", "arm", (2., .3, 6000., 8000., .4, 1000.)),
        (UsdPhysics.PrismaticJoint, "linear", "slide", "arm", "tip", (4., .8, 600., 8., 1.2, 40.)),
    ):
        joint = kind.Define(stage, "/Robot/" + name)
        joint.CreateBody0Rel().SetTargets(["/Robot/" + parent])
        joint.CreateBody1Rel().SetTargets(["/Robot/" + child])
        joint.CreateLocalPos0Attr(Gf.Vec3f(20, 0, 0))
        kp, kv, force, armature, damping, friction = dynamics
        drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), channel)
        drive.CreateTypeAttr("force")
        drive.CreateStiffnessAttr(kp)
        drive.CreateDampingAttr(kv)
        drive.CreateMaxForceAttr(force)
        for field, value in (("armature", armature), ("damping", damping), ("friction", friction)):
            joint.GetPrim().CreateAttribute("newton:" + field, Sdf.ValueTypeNames.Float).Set(value)

    source = stage.GetRootLayer().ExportToString().encode()
    package = files("probe.usda", source)
    manifest = json.loads(package["robot.json"])
    manifest["calibration"] = {"density": 250.}
    package["robot.json"] = json.dumps(manifest).encode()
    reply = upload(client, package)
    if mass_mode == "collider":
        assert reply.status_code == 422, reply.text
        problem, = reply.json()["detail"]["problems"]
        assert problem["path"] == "/Robot/arm/box"
        assert "collider mass" in problem["message"]
        assert client.get("/api/v1/library/assets/robot/calibration-probe/1").status_code == 404
        return
    assert reply.status_code == 200, reply.text
    item = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    assert client.get(item["files"]["source/probe.usda"]).content == source
    provenance = client.get(item["files"]["provenance.json"]).json()
    assert provenance["estimatedMassPrims"] == ["/Asset/links/arm"]
    path = tmp_path / "readback.usda"
    path.write_bytes(client.get(item["files"]["asset.usda"]).content)
    derived = derive_usd_mjcf(path)
    model = derived.to_mjmodel()
    tip = model.body(derived.body_names_by_path["/Asset/links/tip"]).id
    assert model.body_mass[tip] == pytest.approx(.5)
    # The larger cube is eight times the volume of the small cube.
    assert model.body_ipos[tip] == pytest.approx([.05, 0, 0])
    assert model.body_inertia[tip] == pytest.approx([.0004, .00045, .0005])
    arm = model.body(derived.body_names_by_path["/Asset/links/arm"]).id
    assert model.body_mass[arm] == pytest.approx(.256)
    assert model.body_ipos[arm] == pytest.approx([.01, .02, .03])
    basis = np.empty(9)
    mujoco.mju_quat2Mat(basis, model.body_iquat[arm])
    basis = basis.reshape((3, 3))
    np.testing.assert_allclose(basis @ np.diag(model.body_inertia[arm]) @ basis.T,
                               np.diag([.00045, .0004, .0005]), atol=1e-9)

    data = mujoco.MjData(model)
    for joint, expected, command in zip(derived.joints, (
        (1e-4 * 180 / math.pi, 1.5e-5 * 180 / math.pi, .3, .4, 2e-5 * 180 / math.pi, .05),
        (2., .4, 3., 4., .6, .2),
    ), (80., .65), strict=True):
        native = model.joint(joint.name)
        dof = native.dofadr[0]
        actuator, = np.flatnonzero(model.actuator_trnid[:, 0] == native.id)
        assert (model.actuator_gainprm[actuator, 0], -model.actuator_biasprm[actuator, 2],
                model.actuator_forcerange[actuator, 1], model.dof_armature[dof],
                model.dof_damping[dof], model.dof_frictionloss[dof]) == pytest.approx(expected)
        data.qpos[native.qposadr[0]], data.qvel[dof], data.ctrl[actuator] = .1, .5, command
    mujoco.mj_forward(model, data)
    assert data.qfrc_actuator.tolist() == pytest.approx([.3, .9])
    assert data.qfrc_passive.tolist() == pytest.approx([-1e-5 * 180 / math.pi, -.3])


def two_link_usd_with_box_colliders(offset):
    from pxr import Gf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, .01)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdPhysics.SetStageKilogramsPerUnit(stage, .5)
    root = UsdGeom.Xform.Define(stage, "/Robot").GetPrim()
    stage.SetDefaultPrim(root)
    UsdPhysics.ArticulationRootAPI.Apply(root)
    for name, x in (("base", 0), ("arm", 30)):
        link = UsdGeom.Xform.Define(stage, f"/Robot/{name}")
        link.AddTranslateOp().Set(Gf.Vec3d(x, 0, 10))
        UsdPhysics.RigidBodyAPI.Apply(link.GetPrim())
        shape = UsdGeom.Cube.Define(stage, f"/Robot/{name}/box")
        shape.CreateSizeAttr(8)
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    heavy = UsdGeom.Cube.Define(stage, "/Robot/arm/heavy")
    heavy.CreateSizeAttr(8)
    heavy.AddTranslateOp().Set(Gf.Vec3d(*offset))
    UsdPhysics.CollisionAPI.Apply(heavy.GetPrim())
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/Robot/hinge")
    joint.CreateBody0Rel().SetTargets(["/Robot/base"])
    joint.CreateBody1Rel().SetTargets(["/Robot/arm"])
    joint.CreateLocalPos0Attr(Gf.Vec3f(30, 0, 0))
    return stage, stage.GetPrimAtPath("/Robot/arm"), heavy.GetPrim()


@pytest.mark.parametrize(("density_source", "expected_mass", "expected_center"), [
    ("collider", 1.28, .08),
    ("material", 1.536, 1 / 15),
    ("shadowed", .512, .05),
    ("equal", .512, .05),
    ("centerOfMass", None, None),
    ("diagonalInertia", None, None),
    ("principalAxes", None, None),
    ("sentinels", 1.28, .08),
    ("body-fields", 1.8, .02),
])
def test_usd_robot_import_preserves_effective_collider_density(
    client, tmp_path, density_source, expected_mass, expected_center,
):
    from pxr import Gf, UsdPhysics, UsdShade

    from apps.node.usd_mjcf import derive_usd_mjcf

    stage, arm, heavy = two_link_usd_with_box_colliders((10, 0, 0))
    if density_source != "material":
        UsdPhysics.MassAPI.Apply(arm).CreateDensityAttr(.001)
    if density_source in ("material", "shadowed"):
        material = UsdShade.Material.Define(stage, "/Robot/Material")
        UsdPhysics.MaterialAPI.Apply(material.GetPrim()).CreateDensityAttr(.004)
        UsdShade.MaterialBindingAPI.Apply(heavy).Bind(material, materialPurpose="physics")
    collider_mass = UsdPhysics.MassAPI.Apply(heavy)
    collider_mass.CreateDensityAttr({"equal": .001, "material": 0., "shadowed": 0.}.get(density_source, .004))
    if density_source in ("centerOfMass", "body-fields"):
        collider_mass.CreateCenterOfMassAttr(Gf.Vec3f(1, 0, 0))
    if density_source in ("diagonalInertia", "body-fields"):
        collider_mass.CreateDiagonalInertiaAttr(Gf.Vec3f(2, 2.5, 3))
    if density_source in ("principalAxes", "body-fields"):
        collider_mass.CreatePrincipalAxesAttr(Gf.Quatf(.70710678, Gf.Vec3f(0, 0, .70710678)))
    if density_source == "sentinels":
        collider_mass.CreateMassAttr(0.)
        collider_mass.CreateCenterOfMassAttr(Gf.Vec3f(-math.inf))
        collider_mass.CreateDiagonalInertiaAttr(Gf.Vec3f(0))
        collider_mass.CreatePrincipalAxesAttr(Gf.Quatf(0))
    if density_source == "body-fields":
        collider_mass.CreateMassAttr(.75)
        body_mass = UsdPhysics.MassAPI(arm)
        body_mass.CreateMassAttr(3.6)
        body_mass.CreateCenterOfMassAttr(Gf.Vec3f(2, 0, 0))
        body_mass.CreateDiagonalInertiaAttr(Gf.Vec3f(8, 9, 10))

    package = files("density.usda", stage.GetRootLayer().ExportToString().encode())
    manifest = json.loads(package["robot.json"])
    manifest["calibration"] = {"density": 1000.}
    package["robot.json"] = json.dumps(manifest).encode()
    reply = upload(client, package)
    if expected_mass is None:
        assert reply.status_code == 422, reply.text
        problem, = reply.json()["detail"]["problems"]
        assert problem["path"] == "/Robot/arm/heavy"
        assert density_source in problem["message"]
        assert client.get("/api/v1/library/assets/robot/calibration-probe/1").status_code == 404
        return
    assert reply.status_code == 200, reply.text
    item = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    path = tmp_path / "readback.usda"
    path.write_bytes(client.get(item["files"]["asset.usda"]).content)
    assert client.get(item["files"]["source/density.usda"]).content == package["density.usda"]
    derived = derive_usd_mjcf(path)
    model = derived.to_mjmodel()
    body = model.body(derived.body_names_by_path["/Asset/links/arm"]).id
    assert model.body_mass[body] == pytest.approx(expected_mass)
    assert model.body_ipos[body] == pytest.approx([expected_center, 0., 0.])
    if density_source == "body-fields":
        assert model.body_inertia[body] == pytest.approx([.0004, .00045, .0005])
    else:
        cube_inertia = expected_mass * .08**2 / 6
        transverse = cube_inertia + expected_mass * expected_center * (.10 - expected_center)
        assert model.body_inertia[body] == pytest.approx([cube_inertia, transverse, transverse])


@pytest.mark.parametrize(("case", "axes", "diagonal", "offset"), [
    ("axes-only", (.70710678, 0., 0., .70710678), None, (10, 0, 0)),
    ("authored-identity", (1., 0., 0., 0.), None, (3, 10, 0)),
    ("diagonal-only", None, (8, 9, 10), (3, 10, 0)),
    ("both", (.70710678, 0., 0., .70710678), (8, 9, 10), (3, 10, 0)),
    ("sentinels", (0., 0., 0., 0.), (0, 0, 0), (3, 10, 0)),
    ("derived", None, None, (3, 10, 0)),
    ("repeated-moments", (.70710678, 0., .70710678, 0.), None, (0, 0, 0)),
])
def test_usd_robot_import_preserves_partial_principal_axes(client, tmp_path, case, axes, diagonal, offset):
    import numpy as np
    from pxr import Gf, Usd, UsdPhysics

    from apps.node.usd_mjcf import derive_usd_mjcf, mujoco_link_inertia

    stage, arm, heavy = two_link_usd_with_box_colliders(offset)
    mass_api = UsdPhysics.MassAPI.Apply(arm)
    mass_api.CreateDensityAttr(.001)
    UsdPhysics.MassAPI.Apply(heavy).CreateDensityAttr(.004)
    if axes is not None:
        mass_api.CreatePrincipalAxesAttr(Gf.Quatf(*axes))
    if diagonal is not None:
        mass_api.CreateDiagonalInertiaAttr(Gf.Vec3f(*diagonal))

    def cube(prim):
        properties = UsdPhysics.RigidBodyAPI.MassInformation()
        properties.volume = 8**3
        properties.inertia = Gf.Matrix3f(8**5 / 6)
        properties.centerOfMass = Gf.Vec3f(0.)
        positions = {"box": (0, 0, 0), "heavy": offset}
        properties.localPos = Gf.Vec3f(*positions[prim.GetName()])
        properties.localRot = Gf.Quatf(1.)
        return properties

    def basis(quaternion):
        q = Gf.Quatd(quaternion.GetReal(), Gf.Vec3d(quaternion.GetImaginary())).GetNormalized()
        return np.asarray(Gf.Matrix3d().SetRotate(q)).T

    source_mass, source_diagonal, source_com, source_axes = (
        UsdPhysics.RigidBodyAPI(arm).ComputeMassProperties(cube)
    )
    if case == "diagonal-only":
        # The explicit moments use the default body frame when rotation is omitted.
        source_axes = Gf.Quatf(1.)
    source_basis = basis(source_axes)
    source_tensor = (source_basis * np.asarray(source_diagonal) * .5 * .01**2) @ source_basis.T

    source = stage.GetRootLayer().ExportToString().encode()
    package = files("axes.usda", source)
    manifest = json.loads(package["robot.json"])
    manifest["calibration"] = {"density": 1000.}
    package["robot.json"] = json.dumps(manifest).encode()
    result = upload(client, package)
    assert result.status_code == 200, result.text
    item = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    assert client.get(item["files"]["source/axes.usda"]).content == source
    path = tmp_path / "readback.usda"
    path.write_bytes(client.get(item["files"]["asset.usda"]).content)
    prim_path = "/Asset/links/arm"
    derived = derive_usd_mjcf(path)
    model = derived.to_mjmodel()
    body = model.body(derived.body_names_by_path[prim_path]).id
    assert model.body_mass[body] == pytest.approx(source_mass * .5)
    assert model.body_ipos[body] == pytest.approx(np.asarray(source_com) * .01, abs=1e-8)
    np.testing.assert_allclose(mujoco_link_inertia(model, body), source_tensor, rtol=2e-6, atol=1e-8)
    if axes is not None and any(axes) and diagonal is None:
        baked = Usd.Stage.Open(str(path))
        published_axes = UsdPhysics.MassAPI(baked.GetPrimAtPath(prim_path)).GetPrincipalAxesAttr().Get()
        np.testing.assert_allclose(basis(published_axes), source_basis, atol=1e-7)


@pytest.mark.parametrize(("condim", "priority", "override", "parameters", "field"), [
    (3, 0, False, 'margin="0" gap="0" solref=".02 1" solimp=".9 .95 .001 .5 2" solmix="1"', None),
    (1, 0, False, "", "condim=1"),
    (4, 0, False, "", "condim=4"),
    (6, 0, False, "", "condim=6"),
    (3, 1, False, "", "priority=1"),
    (3, 0, True, "", "override"),
    (3, 0, False, 'margin=".05"', "margin"),
    (3, 0, False, 'gap=".01"', "gap"),
    (3, 0, False, 'solref=".03 .75"', "solref"),
    (3, 0, False, 'solimp=".6 .8 .002 .5 2"', "solimp"),
    (3, 0, False, 'solmix=".1"', "solmix"),
])
def test_mjcf_robot_import_requires_supported_contact_friction(
    client, tmp_path, condim, priority, override, parameters, field,
):
    import mujoco

    from apps.node.usd_mjcf import derive_usd_mjcf

    flag = "enable" if override else "disable"
    source = f"""<mujoco>
      <option o_friction=".21 .22 .3 .2 .2"><flag override="{flag}"/></option>
      <default><geom condim="{condim}" priority="{priority}" friction=".8 .3 .2" {parameters}/></default>
      <worldbody><body name="base"><freejoint/>
        <geom name="base-box" type="box" size=".1 .1 .1" mass="2"/>
        <geom name="visual" size=".05" condim="6" priority="2" contype="0" conaffinity="0" mass="0"
              margin=".05" gap=".02"/>
        <body name="tip" pos=".3 0 0"><joint name="motor-a"/>
          <geom size=".05" condim="3" priority="0" mass=".25"/>
        </body>
      </body></worldbody>
    </mujoco>""".encode()
    native = mujoco.MjModel.from_xml_string(source.decode())
    box = native.geom("base-box").id
    assert native.geom_condim[box] == condim
    assert native.geom_priority[box] == priority
    assert native.geom_friction[box] == pytest.approx([.8, .3, .2])
    reply = upload(client, files("probe.xml", source))
    url = "/api/v1/library/assets/robot/calibration-probe/1"
    if field:
        assert reply.status_code == 422, reply.text
        problem, = reply.json()["detail"]["problems"]
        assert problem["path"] == ("option/flag:override" if override else "base/base-box")
        assert field in problem["message"]
        assert client.get(url).status_code == 404
        return
    assert reply.status_code == 200, reply.text
    files_readback = client.get(url).json()["files"]
    assert client.get(files_readback["source/probe.xml"]).content == source
    path = tmp_path / "readback.usda"
    path.write_bytes(client.get(files_readback["asset.usda"]).content)
    derived = derive_usd_mjcf(path)
    model = derived.to_mjmodel()
    box, = [name for prim, name in derived.collider_names_by_path.items() if "/base/" in prim]
    assert model.geom(box).condim[0] == 3
    assert model.geom(box).friction[0] == pytest.approx(.8)


@pytest.mark.parametrize(("geometry", "volume", "moment_factors"), [
    ('<box size=".08 .10 .12"/>', .08 * .10 * .12, (.002033333333, .001733333333, .001366666667)),
    ('<sphere radius=".04"/>', .000256 * math.pi / 3, (.00064, .00064, .00064)),
    ('<cylinder radius=".04" length=".12"/>', .000192 * math.pi, (.0016, .0016, .0008)),
    ('<capsule radius=".04" length=".12"/>', (.000192 + .000256 / 3) * math.pi,
     (.002966153846, .002966153846, .000750769231)),
])
def test_robot_import_estimates_analytic_inertia(client, tmp_path, geometry, volume, moment_factors):
    import numpy as np

    from apps.node.usd_mjcf import derive_usd_mjcf, mujoco_link_inertia

    original = b'<link name="tip"><collision><geometry><box size=".1 .1 .1"/></geometry></collision></link>'
    replacement = (
        f'<link name="tip"><collision><origin xyz=".03 .02 .01" rpy="0 {math.pi / 4} 0"/>'
        f'<geometry>{geometry}</geometry></collision></link>'
    ).encode()
    archive = files(model=URDF.replace(original, replacement))
    manifest = json.loads(archive["robot.json"])
    manifest["calibration"] = {"density": 2000., "scale": 1.5}
    archive["robot.json"] = json.dumps(manifest).encode()
    reply = upload(client, archive)
    assert reply.status_code == 200, reply.text
    item = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    path = tmp_path / "readback.usda"
    path.write_bytes(client.get(item["files"]["asset.usda"]).content)
    derived = derive_usd_mjcf(path)
    model = derived.to_mjmodel()
    index = model.body(derived.body_names_by_path["/Asset/links/tip"]).id
    mass = volume * 2000 * 1.5**3
    assert model.body_mass[index] == pytest.approx(mass)
    assert model.body_ipos[index] == pytest.approx([.045, .03, .015])
    ix, iy, iz = moment_factors
    expected = np.array([[(ix + iz) / 2, 0., (iz - ix) / 2],
                         [0., iy, 0.], [(iz - ix) / 2, 0., (ix + iz) / 2]])
    np.testing.assert_allclose(mujoco_link_inertia(model, index), mass * 1.5**2 * expected, atol=1e-8)


def test_policy_export_checks_scene_coordinates_of_explicit_calibration(client, tmp_path):
    import yaml

    from tests.fixtures.policy import observation_spec
    from tests.fixtures.training_task import task_document
    from tests.gateway.test_policies import uploaded_job

    source = b"""<mujoco><compiler angle="radian"/>
    <worldbody><body name="base"><geom type="box" size=".1 .1 .1" mass="2"/>
      <body name="arm" pos=".3 0 0"><joint name="motor-a" axis="-1 0 0" range="-.7 .9"/>
        <geom size=".05" mass=".25"/>
        <body name="tip" pos=".3 0 0"><joint name="finger" type="slide" range="-.1 .2"/>
          <geom size=".05" mass=".25"/><site name="tool"/>
        </body>
      </body></body></worldbody>
    <actuator><position joint="motor-a" kp="10" ctrlrange="-.1 .2"/></actuator></mujoco>"""
    reply = upload(client, files("probe.xml", source))
    assert reply.status_code == 200, reply.text
    entity = "/World/Lab/RobotA"
    key = entity + "/joints/motor_a"
    for name, override, expected in (
        ("equivalent-frame", "quatf physics:localRot1 = (-1, 0, 0, 0)", "verified"),
        ("changed-axis", 'uniform token physics:axis = "Y"', "unavailable"),
    ):
        task = task_document(name)
        task["scene"] = task["evaluation"]["scene"] = name + "@1"
        task["success"] = {"entity": entity, "property": "gripperWidth", "gte": 0.}
        spec = observation_spec()
        spec["action"]["jointKeys"] = spec["observations"][0]["keys"] = [key]
        scene = {
            "scene.yaml": yaml.safe_dump({"name": name, "title": name, "stage": "stage.usda",
                                         "sources": ["newton"], "simulation": {"solver": "mujoco"}}).encode(),
            "devices.yaml": yaml.safe_dump({"scene": name, "entities": [{
                "key": entity, "type": "robot-arm/v1", "program": "robot_arm.newton",
                "config": {"locations": "locations.yaml", "jointKeys": [key], "tubes": [],
                           "fingerKeys": [entity + "/joints/finger"], "handBody": entity + "/links/tip",
                           "tcpSite": entity + "/links/tip/tool"},
            }]}).encode(),
            "locations.yaml": b"locations: {}\n",
            "stage.usda": f'''#usda 1.0
(defaultPrim = "World"
 metersPerUnit = 1
 upAxis = "Z")
def Xform "World" {{
    def Xform "Lab" {{
        def Xform "RobotA" (
            prepend references = @library:assets/robot/calibration-probe/1/asset.usda@</Asset>
        ) {{
            over "joints" {{
                over "motor_a" {{
                    {override}
                }}
            }}
        }}
    }}
}}
'''.encode(),
        }
        reply = upload(client, scene)
        assert reply.status_code == 200, reply.text
        evaluation = {"scene": name + "@1", "episodes": 100, "threshold": 85}
        job = uploaded_job(client, tmp_path / name, spec=spec, task_document=task,
                           plan_overrides={"evaluation": evaluation})
        checkpoint = job["jobId"] + "/checkpoint-origin.json"
        policy = client.post("/api/v1/policies", json={"checkpoint": checkpoint})
        assert policy.status_code == 201, policy.text
        exported = client.get(f"/api/v1/policies/{name}/1/export")
        assert exported.status_code == 200, exported.text
        with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
            manifest = json.loads(package.read("manifest.json"))
            mapping = manifest["sourceCalibration"]
            assert mapping["status"] == expected
            assert manifest["actuators"][0]["controlRange"] == pytest.approx([.05, .35])
            if expected == "verified":
                assert mapping["joints"] == [{"jointKey": "/Asset/joints/motor_a", "sourceName": "motor-a",
                                              "sign": -1, "zeroOffset": .25, "unit": "rad"}]
            else:
                assert mapping["joints"] == [] and "coordinates differ" in mapping["reason"]
                assert "source-calibration.json" not in package.namelist()


@pytest.mark.parametrize(("setting", "path", "expected"), [
    ('joint:stiffness="5" springref=".3"', "motor-a", "joint springs"),
    ('flag:damper="disable"', "motor-a", "disables authored joint damping"),
    ('flag:actuation="disable"', "motor-a", "disables the joint actuator"),
    ('flag:constraint="disable"', "option/flag:constraint", "canonical USD subset"),
    ('flag:contact="disable"', "option/flag:contact", "canonical USD subset"),
    ('root:<joint type="free" name="float" damping="2"/>', "float", "Floating root"),
])
def test_mjcf_unsupported_dynamics_fail_before_publication(client, setting, path, expected):
    import mujoco

    kind, value = setting.split(":", 1)
    option = f"<option><flag {value}/></option>" if kind == "flag" else ""
    joint = value if kind == "joint" else ""
    root = value if kind == "root" else ""
    model = f"""<mujoco><compiler angle="radian"/>{option}
    <worldbody><body name="base">{root}<geom type="box" size=".1 .1 .1" mass="2"/>
      <body name="tip" pos=".3 0 0"><joint name="motor-a" damping="4" range="-1 1" {joint}/>
        <geom size=".05" mass=".25"/>
      </body></body></worldbody>
    <actuator><position joint="motor-a" kp="10"/></actuator></mujoco>"""
    assert mujoco.MjModel.from_xml_string(model).nu == 1
    reply = upload(client, files("probe.xml", model.encode()))
    assert reply.status_code == 422, reply.text
    problem = reply.json()["detail"]["problems"][0]
    assert problem["path"] == path
    assert expected in problem["message"]
    assert client.get("/api/v1/library/assets/robot/calibration-probe/1").status_code == 404


@pytest.mark.parametrize(("joint_limit", "damping"), [(True, 0), (True, 4), (False, 4)])
def test_mjcf_actuator_force_limit_preserves_applied_torque(client, tmp_path, joint_limit, damping):
    import mujoco
    from pxr import Usd, UsdPhysics

    from apps.node.usd_mjcf import derive_usd_mjcf

    joint_force = 'actuatorfrcrange="-3 3"' if joint_limit else ""
    actuator_force = 'forcerange="-5 5"' if joint_limit else 'forcerange="-3 3"'
    model = f"""<mujoco><compiler angle="degree"/><worldbody>
      <body name="base" pos="0 0 .5"><geom type="box" size=".15 .12 .10" mass="2"/>
        <body name="tip" pos=".2 0 0">
          <joint name="motor-a" axis="0 0 -1" range="-30 70" ref="10" damping="{damping}" {joint_force}/>
          <geom type="sphere" size=".05" mass=".25"/>
        </body>
      </body></worldbody>
      <actuator><position name="drive-a" joint="motor-a" kp="10" kv="1" {actuator_force}/></actuator>
    </mujoco>""".encode()
    source = mujoco.MjModel.from_xml_string(model.decode())
    source_data = mujoco.MjData(source)
    source_data.qvel[0] = .5
    source_data.ctrl[0] = .9
    mujoco.mj_forward(source, source_data)
    assert source_data.qfrc_actuator[0] == pytest.approx(3.)
    assert source_data.qfrc_passive[0] == pytest.approx(-damping * .5)

    reply = upload(client, files("probe.xml", model))
    assert reply.status_code == 200, reply.text
    detail = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    path = tmp_path / "baked.usda"
    path.write_bytes(client.get(detail["files"]["asset.usda"]).content)
    stage = Usd.Stage.Open(str(path))
    drive = UsdPhysics.DriveAPI(stage.GetPrimAtPath("/Asset/joints/motor_a"), "angular")
    assert drive.GetMaxForceAttr().Get() == pytest.approx(3.)
    assert drive.GetDampingAttr().Get() == pytest.approx(math.pi / 180.)
    result = derive_usd_mjcf(stage)
    derived = result.to_mjmodel()
    data = mujoco.MjData(derived)
    data.qpos[0] = result.joints[0].initial_position
    data.qvel[0] = -source_data.qvel[0]
    data.ctrl[0] = -.9 + .25
    mujoco.mj_forward(derived, data)
    assert data.qfrc_actuator[0] == pytest.approx(-3.)
    assert data.qfrc_passive[0] == pytest.approx(damping * .5)
    assert data.qfrc_actuator[0] + data.qfrc_passive[0] == pytest.approx(
        -source_data.qfrc_actuator[0] - source_data.qfrc_passive[0]
    )

    reply = upload(client, files("probe.xml", model.replace(b'="-3 3"', b'="-3 4"')))
    assert reply.status_code == 422, reply.text
    problem = reply.json()["detail"]["problems"][0]
    assert problem["path"] == ("motor-a" if joint_limit else "drive-a")
    assert "symmetric" in problem["message"]
    if damping:
        reply = upload(client, files("probe.xml", model.replace(b'damping="4"', b'damping="4 1"')))
        assert reply.status_code == 422, reply.text
        assert reply.json()["detail"]["problems"] == [
            {"path": "motor-a", "message": "Joint requires finite nonnegative linear passive damping"},
        ]


@pytest.mark.parametrize(("prefix", "meshdir"), [("", "meshes"), ("model/", "../meshes")])
def test_included_mjcf_and_usd_round_trip_at_public_asset_boundary(client, tmp_path, prefix, meshdir):
    import mujoco

    from apps.node.usd_mjcf import derive_usd_mjcf

    entry = prefix + "probe.xml"
    source = files(entry, b'<mujoco><include file="setup/settings.xml"/>'
                   b'<include file="assembly/body.xml"/></mujoco>')
    settings = prefix + "setup/settings.xml"
    source[settings] = (
        f'<mujocoinclude><compiler angle="radian" meshdir="{meshdir}"/></mujocoinclude>'
    ).encode()
    source["meshes/shape.stl"] = trimesh.creation.box(extents=(.1, .1, .1)).export(file_type="stl")
    source[prefix + "assembly/body.xml"] = b"""
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
    native = mujoco.MjModel.from_xml_path(str(tmp_path / entry))
    assert native.nmesh == 1
    first = client.post("/api/v1/library/imports", content=gzip.compress(archive(source)),
                        headers={"Content-Type": "application/gzip"})
    assert first.status_code == 200, first.text
    item = client.get("/api/v1/library/assets/robot/calibration-probe/1").json()
    assert client.get(item["files"]["source/" + settings]).content == source[settings]
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
    escaped = source | {settings: source[settings].replace(meshdir.encode(), b"../../meshes")}
    reply = upload(client, escaped)
    assert reply.status_code == 422
    assert reply.json()["detail"]["problems"] == [
        {"path": settings, "message": "Invalid compiler directory: Resource escapes the archive"},
    ]

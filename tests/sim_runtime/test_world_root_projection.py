"""A motor coordinate must move its actual body before streaming or rendering."""

import json
import math

import numpy as np
import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")
mujoco = pytest.importorskip("mujoco")
from newton.solvers import SolverMuJoCo
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from packages.sim_runtime.loaders import import_usd
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation

pytestmark = pytest.mark.newton


@pytest.mark.parametrize("kind,target", [("slide", .02), ("hinge", .05)])
@pytest.mark.parametrize("authored", [True, False])
def test_world_root_coordinate_and_body_projection(tmp_path, kind, target, authored, instanceable=False):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Lab")
    scope = "/Template" if instanceable else "/World/Lab"
    UsdGeom.Xform.Define(stage, scope)
    body = UsdGeom.Xform.Define(stage, scope + "/Part")
    body.AddTranslateOp().Set(Gf.Vec3d(0, 0, 1))
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(1.)
    shape = UsdGeom.Cube.Define(stage, scope + "/Part/Collider")
    shape.CreateSizeAttr(.05)
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    joint_type = UsdPhysics.PrismaticJoint if kind == "slide" else UsdPhysics.RevoluteJoint
    joint = joint_type.Define(stage, scope + "/Joint")
    joint.CreateBody1Rel().SetTargets([body.GetPath()])
    joint.CreateLocalPos0Attr(Gf.Vec3f(0, 0, 1))
    joint.CreateLocalPos1Attr(Gf.Vec3f(0))
    joint.CreateAxisAttr("Y" if kind == "slide" else "Z")
    joint.CreateLowerLimitAttr(0.)
    joint.CreateUpperLimitAttr(.2 if kind == "slide" else 30.)
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "linear" if kind == "slide" else "angular")
    drive.CreateStiffnessAttr(200.)
    drive.CreateDampingAttr(20.)
    drive.CreateMaxForceAttr(100.)
    drive.CreateTargetPositionAttr(0.)
    joint.GetPrim().CreateAttribute("newton:" + ("linear" if kind == "slide" else "angular") + ":position",
                                    Sdf.ValueTypeNames.Float, custom=True).Set(0.)
    if authored:
        UsdPhysics.ArticulationRootAPI.Apply(body.GetPrim())
    if instanceable:
        instance = UsdGeom.Xform.Define(stage, "/World/Lab/Instance").GetPrim()
        instance.GetReferences().AddInternalReference(scope)
        instance.SetInstanceable(True)

    def builder():
        result = newton.ModelBuilder(up_axis=newton.Axis.Z)
        SolverMuJoCo.register_custom_attributes(result)
        import_usd(result, stage, root_path="/World/Lab")
        return result

    with NewtonSimulation("world-root-projection", device="cpu", config=SceneConfig(
        builder, solver="mujoco", physics_dt=.0005
    )) as sim:
        sim.control.joint_target_q.numpy()[0] = target
        sim.step(2000)
        scalar = float(sim.state.joint_q.numpy()[0])
        assert scalar == pytest.approx(target, abs=.001)
        # This independent buffer observes the native current qpos. It never
        # edits the live solver, Newton state, or a motor target.
        native, actual = sim.solver.mj_model, sim.solver.mj_data
        observed = mujoco.MjData(native)
        observed.qpos[:] = actual.qpos
        mujoco.mj_kinematics(native, observed)
        mapping = sim.solver.mjc_body_to_newton.numpy().reshape(-1)
        matches = np.flatnonzero(mapping == 0)
        assert len(matches) == 1
        native_body = int(matches[0])
        body_pose = sim.state.body_q.numpy()[0].copy()
        source_position = observed.xpos[native_body].copy()
        source_quaternion = observed.xquat[native_body][[1, 2, 3, 0]].copy()
        proof = {"kind": kind, "steps": 2000, "simTime": sim.sim_time,
                 "jointCoordinate": scalar, "nativeQpos": actual.qpos.tolist(),
                 "nativeBodyIndex": native_body, "newtonBodyIndex": 0,
                 "nativePosition": source_position.tolist(), "newtonPose": body_pose.tolist(),
                 "jointArticulation": sim.model.joint_articulation.numpy().tolist(),
                 "nativeMocap": native.nmocap}
        (tmp_path / "same-step-projection.json").write_text(json.dumps(proof, indent=2))
        assert native.nmocap == 0
        if not authored:
            assert sim.model.joint_articulation.numpy().tolist() == [-1]
            np.testing.assert_array_equal(body_pose, [0., 0., 1., 0., 0., 0., 1.])
            assert not any(row["code"] == "usd_articulation_root_compatibility"
                           for row in sim.builder.spatial_warnings)
            return
        assert sim.model.joint_articulation.numpy().tolist() == [0]
        if kind == "slide":
            assert source_position[1] == pytest.approx(scalar, abs=1e-6)
            assert body_pose[1] == pytest.approx(scalar, abs=1e-6)
        else:
            assert source_quaternion[2] == pytest.approx(math.sin(scalar / 2), abs=1e-6)
            assert body_pose[5] == pytest.approx(source_quaternion[2], abs=1e-6)
        np.testing.assert_allclose(body_pose[:3], source_position, atol=1e-6)


def test_instance_proxy_root_keeps_actual_motor_and_body_configuration_coherent(tmp_path):
    test_world_root_coordinate_and_body_projection(tmp_path, "slide", .02, True, instanceable=True)


def test_fixed_world_anchor_under_translated_parent_forwards_to_floor_contacts():
    from types import SimpleNamespace

    from apps.node.usd_mjcf import derive_usd_mjcf
    from packages.library.model_consistency import compare_models
    from packages.library.physics import contact_observations

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    root = UsdGeom.Xform.Define(stage, "/Asset")
    stage.SetDefaultPrim(root.GetPrim())
    parent = UsdGeom.Xform.Define(stage, "/Asset/Robot")
    parent.AddTranslateOp().Set((0., 0., .05))
    UsdPhysics.ArticulationRootAPI.Apply(parent.GetPrim())
    for name in ("Base", "Link"):
        body = UsdGeom.Xform.Define(stage, f"/Asset/Robot/{name}")
        UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
        colliders = [("Sphere", 0., .025)] if name == "Base" else [("Left", -.12, .08), ("Right", .12, .08)]
        for label, x, radius in colliders:
            sphere = UsdGeom.Sphere.Define(stage, f"/Asset/Robot/{name}/{label}")
            sphere.CreateRadiusAttr(radius)
            sphere.AddTranslateOp().Set((x, 0, 0))
            UsdPhysics.CollisionAPI.Apply(sphere.GetPrim())
    ground = UsdGeom.Plane.Define(stage, "/Asset/Ground")
    ground.CreateAxisAttr("Z")
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    fixed = UsdPhysics.FixedJoint.Define(stage, "/Asset/Robot/Fixed")
    fixed.CreateBody1Rel().SetTargets(["/Asset/Robot/Base"])
    fixed.CreateLocalPos0Attr((0, 0, .075))
    hinge = UsdPhysics.RevoluteJoint.Define(stage, "/Asset/Robot/Hinge")
    hinge.CreateBody0Rel().SetTargets(["/Asset/Robot/Base"])
    hinge.CreateBody1Rel().SetTargets(["/Asset/Robot/Link"])
    builder = newton.ModelBuilder()
    import_usd(builder, stage, root_path="/Asset")
    model = builder.finalize(device="cpu")
    state = model.state()
    newton.eval_fk(model, state.joint_q, state.joint_qd, state)
    newton_link = model.body_label.index("/Asset/Robot/Link")
    assert state.body_q.numpy()[newton_link, 2] == pytest.approx(.075)
    measured = contact_observations(SimpleNamespace(model=model, state=state, collision_pipeline=None))
    assert {frozenset((a, b)) for a, b, distance in measured if distance < 0.} == {
        frozenset(("/Asset/Ground", "/Asset/Robot/Link/Left")),
        frozenset(("/Asset/Ground", "/Asset/Robot/Link/Right")),
    }

    derived = derive_usd_mjcf(stage)
    native = derived.to_mjmodel()
    forward = mujoco.MjData(native)
    mujoco.mj_forward(native, forward)
    assert forward.body("Link").xpos[2] == pytest.approx(.075)
    assert {frozenset(native.geom(i).name for i in (contact.geom1, contact.geom2))
            for contact in forward.contact} == {
        frozenset(("Ground", "Link_Left")), frozenset(("Ground", "Link_Right")),
    }
    report = compare_models(model, derived)
    assert [row["status"] for row in report] == ["pass"], report


@pytest.mark.parametrize("anchor", ["world", "explicit", "identity", "standalone"])
def test_rotated_referenced_root_matches_newton_fk(anchor):
    from apps.node.usd_mjcf import derive_usd_mjcf
    from packages.library.model_consistency import compare_models

    asset = Usd.Stage.CreateInMemory()
    root = UsdGeom.Xform.Define(asset, "/Asset")
    asset.SetDefaultPrim(root.GetPrim())
    UsdPhysics.ArticulationRootAPI.Apply(root.GetPrim())
    body = UsdGeom.Xform.Define(asset, "/Asset/Base")
    body.AddTranslateOp().Set((.1, 0., .2))
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    box = UsdGeom.Cube.Define(asset, "/Asset/Base/Box")
    box.CreateSizeAttr(.1)
    box.AddRotateXOp().Set(25.)
    UsdPhysics.CollisionAPI.Apply(box.GetPrim())
    fixed = UsdPhysics.FixedJoint.Define(asset, "/Asset/Fixed")
    fixed.CreateBody1Rel().SetTargets(["/Asset/Base"])
    if anchor != "identity":
        fixed.CreateLocalPos0Attr((.1, 0., .2))
    if anchor == "explicit":
        fixed.CreateBody0Rel().SetTargets(["/Asset"])
    if anchor != "standalone":
        link = UsdGeom.Xform.Define(asset, "/Asset/Link")
        UsdPhysics.RigidBodyAPI.Apply(link.GetPrim())
        end = UsdGeom.Sphere.Define(asset, "/Asset/Link/Collider")
        end.CreateRadiusAttr(.025)
        UsdPhysics.CollisionAPI.Apply(end.GetPrim())
        hinge = UsdPhysics.RevoluteJoint.Define(asset, "/Asset/Hinge")
        hinge.CreateBody0Rel().SetTargets(["/Asset/Base"])
        hinge.CreateBody1Rel().SetTargets(["/Asset/Link"])
        hinge.CreateLocalPos0Attr((0, 0, .2))

    scene = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(scene, "Z")
    UsdGeom.SetStageMetersPerUnit(scene, 1.)
    robot = UsdGeom.Xform.Define(scene, "/Robot")
    robot.GetPrim().GetReferences().AddReference(asset.GetRootLayer().identifier)
    robot.AddTranslateOp().Set((.4, -.2, .3))
    robot.AddRotateZOp().Set(90.)
    builder = newton.ModelBuilder()
    import_usd(builder, scene, root_path="/Robot")
    model = builder.finalize(device="cpu")
    derived = derive_usd_mjcf(scene, prim_path="/Robot")
    native = derived.to_mjmodel()
    forward = mujoco.MjData(native)
    mujoco.mj_forward(native, forward)
    if anchor == "world":
        np.testing.assert_allclose(forward.body("Base").xpos, [.1, 0., .2], atol=1e-6)
        np.testing.assert_allclose(forward.body("Base").xmat.reshape(3, 3)[:, 0], [1, 0, 0], atol=1e-6)
    else:
        np.testing.assert_allclose(forward.body("Base").xpos, [.4, -.1, .5], atol=1e-6)
        np.testing.assert_allclose(forward.body("Base").xmat.reshape(3, 3)[:, 0], [0, 1, 0], atol=1e-6)
    report = compare_models(model, derived)
    assert [row["status"] for row in report] == ["pass"], report

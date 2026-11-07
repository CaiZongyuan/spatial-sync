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
def test_world_root_coordinate_and_body_projection(tmp_path, kind, target, authored):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Lab")
    body = UsdGeom.Xform.Define(stage, "/World/Lab/Part")
    body.AddTranslateOp().Set(Gf.Vec3d(0, 0, 1))
    UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
    UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(1.)
    shape = UsdGeom.Cube.Define(stage, "/World/Lab/Part/Collider")
    shape.CreateSizeAttr(.05)
    UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    joint_type = UsdPhysics.PrismaticJoint if kind == "slide" else UsdPhysics.RevoluteJoint
    joint = joint_type.Define(stage, "/World/Lab/Joint")
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

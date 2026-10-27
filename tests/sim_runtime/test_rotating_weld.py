"""A light load welded upright at an offset must stay upright during rotation."""

import math

import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

import newton
from newton.solvers import SolverMuJoCo
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from packages.sim_runtime.loaders import import_usd
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation
from packages.sim_runtime.welds import Welds, declare_weld_pairs


def test_rotating_weld_holds_small_offset_load_without_angular_collapse(tmp_path):
    path = tmp_path / "offset-load.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
    for name, position, mass, center, inertia in [
        ("Rotor", (0, 0, .5), 5, (0, 0, .004), (.04, .04, .08)),
        ("Tube", (.13, 0, .512), .015, (0, 0, .067), (2.27e-5, 2.27e-5, 6.82e-7)),
    ]:
        body = UsdGeom.Xform.Define(stage, "/World/"+name)
        body.AddTranslateOp().Set(position)
        if name == "Tube":
            body.AddRotateYOp().Set(2)
        UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
        props = UsdPhysics.MassAPI.Apply(body.GetPrim())
        props.CreateMassAttr(mass)
        props.CreateCenterOfMassAttr(Gf.Vec3f(*center))
        props.CreateDiagonalInertiaAttr(Gf.Vec3f(*inertia))
        props.CreatePrincipalAxesAttr(Gf.Quatf(1, Gf.Vec3f(0, 0, 0)))
        shape = UsdGeom.Cylinder.Define(stage, "/World/"+name+"/Shape")
        shape.CreateAxisAttr("Z")
        shape.CreateRadiusAttr(.18 if name == "Rotor" else .0085)
        shape.CreateHeightAttr(.005 if name == "Rotor" else .09)
        shape.AddTranslateOp().Set((0, 0, 0 if name == "Rotor" else .07))
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
    joint = UsdPhysics.RevoluteJoint.Define(stage, "/World/Spindle")
    joint.CreateAxisAttr("Z")
    joint.CreateBody1Rel().SetTargets(["/World/Rotor"])
    joint.CreateLocalPos0Attr(Gf.Vec3f(0, 0, .5))
    UsdPhysics.ArticulationRootAPI.Apply(joint.GetPrim())
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), "angular")
    drive.CreateTypeAttr("force")
    drive.CreateStiffnessAttr(0)
    drive.CreateDampingAttr(17.4532925)
    drive.CreateMaxForceAttr(50)
    stage.GetRootLayer().Save()

    def builder():
        result = newton.ModelBuilder(up_axis=newton.Axis.Z)
        SolverMuJoCo.register_custom_attributes(result)
        import_usd(result, path, root_path="/World")
        rotor, tube = (result.body_label.index("/World/"+name) for name in ("Rotor", "Tube"))
        declare_weld_pairs(result, [(rotor, tube)])
        return result

    with NewtonSimulation("offset-weld", device="cpu", config=SceneConfig(builder, solver="mujoco")) as sim:
        rotor, tube = (sim.model.body_label.index("/World/"+name) for name in ("Rotor", "Tube"))
        weld = Welds(sim.model, sim.solver, sim.builder.spatial_weld_pairs)
        spindle = sim.model.joint_label.index("/World/Spindle")
        target_index = int(sim.model.joint_qd_start.numpy()[spindle])
        poses = sim.body_poses()
        weld.set(rotor, tube, True, poses)
        weld.flush()
        maximum_error = 0
        for frame in range(240):
            targets = sim.control.joint_target_qd.numpy().copy()
            targets[target_index] = 2*math.pi*(1-math.exp(-frame/60))
            sim.control.joint_target_qd.assign(targets)
            sim.step(4)
            poses = sim.body_poses()
            q = poses[tube, 3:].astype(float)
            cosine = 1-2*(q[0]**2+q[1]**2)/float(q @ q)
            maximum_error = max(maximum_error, math.degrees(math.acos(max(-1, min(1, cosine)))))
        assert maximum_error < 5, f"Offset welded load tilted {maximum_error:.2f} degrees"
        weld.set(rotor, tube, False, poses)
        weld.flush()
        assert weld.counts() == (1, 0)

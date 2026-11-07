"""Mixed USD assembly preserves original calls, topology, attributes and lifetime."""

import numpy as np
import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")
from newton.solvers import SolverMuJoCo
from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from packages.sim_runtime.loaders import import_usd
from packages.sim_runtime.usd_articulations import root_import

pytestmark = pytest.mark.newton


def mixed_stage(reverse=False):
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Lab")
    names = ["Slider", "Hinge", "Free"]
    for index, name in enumerate(reversed(names) if reverse else names):
        body = UsdGeom.Xform.Define(stage, "/World/Lab/" + name)
        body.AddTranslateOp().Set(Gf.Vec3d(index, 0, 1))
        UsdPhysics.RigidBodyAPI.Apply(body.GetPrim())
        UsdPhysics.MassAPI.Apply(body.GetPrim()).CreateMassAttr(1.)
        shape = UsdGeom.Cube.Define(stage, str(body.GetPath()) + "/Collider")
        shape.CreateSizeAttr(.05)
        UsdPhysics.CollisionAPI.Apply(shape.GetPrim())
        if name != "Free":
            UsdPhysics.ArticulationRootAPI.Apply(body.GetPrim())
            body.GetPrim().CreateAttribute("probe:gain", Sdf.ValueTypeNames.Float, custom=True).Set(
                2.5 if name == "Slider" else 3.5)
    for name in (["Hinge", "Slider"] if not reverse else ["Slider", "Hinge"]):
        cls = UsdPhysics.RevoluteJoint if name == "Hinge" else UsdPhysics.PrismaticJoint
        joint = cls.Define(stage, "/World/Lab/" + name + "Joint")
        joint.CreateBody1Rel().SetTargets(["/World/Lab/" + name])
        position = UsdGeom.Xformable(stage.GetPrimAtPath("/World/Lab/" + name)).GetLocalTransformation()
        joint.CreateLocalPos0Attr(Gf.Vec3f(*position.ExtractTranslation()))
        joint.CreateAxisAttr("Z" if name == "Hinge" else "Y")
        joint.CreateLowerLimitAttr(0.)
        joint.CreateUpperLimitAttr(30. if name == "Hinge" else .2)
    return stage


class ObservedBuilder(newton.ModelBuilder):
    def __init__(self):
        super().__init__(up_axis=newton.Axis.Z)
        self.calls, self.fail_free = [], False

    def add_articulation(self, joints, label=None, custom_attributes=None):
        if self.fail_free and label and label.endswith("/Free"):
            raise RuntimeError("controlled importer failure")
        self.calls.append((list(joints), label, custom_attributes, self.current_world))
        return super().add_articulation(joints, label=label, custom_attributes=custom_attributes)


def existing(builder):
    body = builder.add_link(mass=1., label="/Existing")
    joint = builder.add_joint_prismatic(parent=-1, child=body, axis=(1., 0., 0.), label="/ExistingJoint")
    builder.add_articulation([joint], label="/ExistingArticulation", custom_attributes={"probe:gain": 77.})


def registered_builder():
    builder = ObservedBuilder()
    SolverMuJoCo.register_custom_attributes(builder)
    builder.add_custom_attribute(newton.ModelBuilder.CustomAttribute(
        name="gain", namespace="probe", dtype=float, frequency=newton.Model.AttributeFrequency.ARTICULATION,
        usd_attribute_name="probe:gain", default=-1.))
    return builder


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("existing_first", [False, True])
def test_mixed_import_preserves_membership_attributes_worlds_and_filters(reverse, existing_first):
    builder = registered_builder()
    builder.begin_world("probe")
    if existing_first:
        existing(builder)
    stage = mixed_stage(reverse)
    paths = import_usd(builder, stage, root_path="/World/Lab")
    if not existing_first:
        existing(builder)
    assert "add_articulation" not in builder.__dict__
    before = (list(builder.articulation_start), list(builder.joint_articulation))
    with root_import(builder, stage, "/World/Lab") as repeated:
        pass
    assert repeated.recovered == []
    assert before == (list(builder.articulation_start), list(builder.joint_articulation))
    shapes = list(paths["path_shape_map"].values())
    builder.add_shape_collision_filter_pair(shapes[0], shapes[-1])
    builder.end_world()
    assert builder.body_count == builder.joint_count == 4 and builder.joint_dof_count == 9
    assert builder.articulation_start == sorted(builder.articulation_start)
    assert all(value >= 0 for value in builder.joint_articulation)
    assert [(label, attrs, world) for _, label, attrs, world in builder.calls
            if label == "/ExistingArticulation"] == [("/ExistingArticulation", {"probe:gain": 77.}, 0)]
    gain = builder.custom_attributes["probe:gain"].values
    for name, expected in (("Slider", 2.5), ("Hinge", 3.5)):
        joint = builder.joint_label.index("/World/Lab/" + name + "Joint")
        assert gain[builder.joint_articulation[joint]] == expected
    assert set(builder.body_world) == set(builder.joint_world) == set(builder.articulation_world) == {0}
    model = builder.finalize(device="cpu")
    assert model.body_mass.numpy().tolist() == [1.] * 4
    assert model.shape_collision_filter_mask(np.asarray([[shapes[0], shapes[-1]]])).tolist() == [True]


def test_import_failure_restores_exact_original_instance_method():
    builder = registered_builder()
    builder.fail_free = True
    original = builder.add_articulation
    builder.add_articulation = original
    with pytest.raises(RuntimeError, match="controlled importer failure"):
        import_usd(builder, mixed_stage(), root_path="/World/Lab")
    assert builder.add_articulation is original


def test_late_root_refuses_before_recovery_mutates_a_committed_boundary():
    builder = registered_builder()
    original = builder.add_articulation
    with pytest.raises(ValueError, match="behind committed"):
        with root_import(builder, mixed_stage(), "/World/Lab") as adapter:
            body = builder.add_link(mass=1., label="/World/Lab/Slider")
            builder.add_joint_prismatic(parent=-1, child=body, axis=(0., 1., 0.),
                                       label="/World/Lab/SliderJoint")
            other = builder.add_link(mass=1., label="/Existing")
            second = builder.add_joint_prismatic(parent=-1, child=other, axis=(1., 0., 0.),
                                                 label="/ExistingJoint")
            original([second], label="/Existing")
            before = (list(builder.articulation_start), list(builder.articulation_end),
                      list(builder.joint_articulation))
            adapter.drain()
    assert before == (list(builder.articulation_start), list(builder.articulation_end),
                      list(builder.joint_articulation))
    builder._validate_structure()
    assert "add_articulation" not in builder.__dict__


def test_no_candidate_with_nonempty_articulation_references_is_an_ordinary_native_import():
    builder = ObservedBuilder()
    builder.add_custom_attribute(newton.ModelBuilder.CustomAttribute(
        name="owner", dtype=int, frequency=newton.Model.AttributeFrequency.ARTICULATION,
        references="articulation", values={0: 0}))
    stage = mixed_stage()
    for name in ("Slider", "Hinge"):
        stage.RemovePrim("/World/Lab/" + name + "Joint")
        stage.RemovePrim("/World/Lab/" + name)
    original = builder.add_articulation
    with root_import(builder, stage, "/World/Lab") as adapter:
        assert not adapter.specs
        assert builder.add_articulation.__func__ is original.__func__
        builder.add_usd(stage, root_path="/World/Lab", apply_up_axis_from_stage=False)
    model = builder.finalize(device="cpu")
    assert model.owner.numpy().tolist() == [0]


def test_unknown_articulation_transformer_is_refused_before_import_mutation():
    builder = ObservedBuilder()
    builder.add_custom_attribute(newton.ModelBuilder.CustomAttribute(
        name="opaque", dtype=float, frequency=newton.Model.AttributeFrequency.ARTICULATION,
        usd_attribute_name="*", usd_value_transformer=lambda value, context: 4.))
    with pytest.raises(ValueError, match="declarative articulation attribute"):
        import_usd(builder, mixed_stage(), root_path="/World/Lab")
    assert builder.body_count == builder.joint_count == builder.articulation_count == 0
    assert "add_articulation" not in builder.__dict__


def test_incoming_articulation_reference_is_refused_before_recovery_mutation():
    builder = registered_builder()
    builder.add_custom_attribute(newton.ModelBuilder.CustomAttribute(
        name="owner", dtype=int, frequency=newton.Model.AttributeFrequency.ARTICULATION,
        references="articulation"))
    with pytest.raises(ValueError, match="articulation-index references"):
        with root_import(builder, mixed_stage(), "/World/Lab"):
            body = builder.add_link(mass=1., label="/World/Lab/Slider")
            builder.add_joint_prismatic(parent=-1, child=body, axis=(0., 1., 0.),
                                       label="/World/Lab/SliderJoint")
            other = builder.add_link(mass=1., label="/Existing")
            joint = builder.add_joint_prismatic(parent=-1, child=other, axis=(1., 0., 0.),
                                               label="/ExistingJoint")
            builder.add_articulation([joint], label="/Existing", custom_attributes={"owner": 0})
    assert builder.articulation_count == 0
    assert builder.joint_articulation == [-1, -1]
    assert "add_articulation" not in builder.__dict__


@pytest.mark.parametrize("case", ["excluded", "connected", "fixed"])
def test_explicit_opt_out_and_other_joint_topologies_are_not_recovered(case):
    stage = mixed_stage()
    stage.RemovePrim("/World/Lab/HingeJoint")
    stage.RemovePrim("/World/Lab/Hinge")
    joint = UsdPhysics.Joint(stage.GetPrimAtPath("/World/Lab/SliderJoint"))
    if case == "excluded":
        joint.CreateExcludeFromArticulationAttr(True)
    elif case == "connected":
        other = UsdPhysics.RevoluteJoint.Define(stage, "/World/Lab/Other")
        other.CreateBody0Rel().SetTargets(["/World/Lab/Slider"])
        other.CreateBody1Rel().SetTargets(["/World/Lab/Free"])
    else:
        stage.RemovePrim("/World/Lab/SliderJoint")
        fixed = UsdPhysics.FixedJoint.Define(stage, "/World/Lab/SliderJoint")
        fixed.CreateBody1Rel().SetTargets(["/World/Lab/Slider"])
    builder = ObservedBuilder()
    with root_import(builder, stage, "/World/Lab") as adapter:
        assert not adapter.specs
    assert "add_articulation" not in builder.__dict__

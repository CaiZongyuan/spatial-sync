from types import SimpleNamespace

import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")
pytestmark = pytest.mark.newton

from newton.solvers import SolverXPBD

from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.scenes import falling_boxes_builder
from packages.sim_runtime.sites import add_joint_anchor_sites


@pytest.fixture
def existing_child_anchor():
    builder = falling_boxes_builder()
    joint = builder.joint_label.index("World/Pendulum/Hinge")
    site = builder.add_site(
        builder.joint_child[joint],
        xform=builder.joint_X_c[joint],
        label="World/Pendulum/Hinge/anchor",
    )
    return builder, joint, site


def test_existing_child_site_is_reused_and_exported_as_authoritative_anchor(existing_child_anchor):
    builder, joint, site = existing_child_anchor
    shape_count = builder.shape_count
    anchors = add_joint_anchor_sites(builder)
    assert anchors == {joint: site}
    assert builder.shape_count == shape_count
    model = builder.finalize(device="cpu")
    state = model.state()
    newton.eval_fk(model, state.joint_q, state.joint_qd, state)
    scene = SimpleNamespace(
        model=model, anchor_sites=anchors, scene_id="existing-child-anchor",
        epoch=0, physics_dt=1 / 240, solver=SolverXPBD(model),
    )
    manifest = export_manifest(scene, session_id="test", scene_revision="rev", revision_numeric_id=1)
    exported_site = next(s for s in manifest.sites if s.entityKey == "/World/Pendulum/Hinge/anchor")
    assert exported_site.role == "joint-anchor"
    assert exported_site.jointKey == "/World/Pendulum/Hinge"
    body_row = state.body_q.numpy()[builder.joint_child[joint]]
    body_world = wp.transform(wp.vec3(*body_row[:3]), wp.quat(*body_row[3:]))
    local_pose = exported_site.localPose
    world = wp.transform_multiply(body_world, wp.transform(wp.vec3(*local_pose.p), wp.quat(*local_pose.q)))
    assert tuple(wp.transform_get_translation(world)) == pytest.approx((1, 0, 1.5), abs=1e-6)


def test_joint_anchor_name_occupied_by_geometry_fails_clearly():
    builder = falling_boxes_builder()
    builder.add_shape_box(1, label="World/Pendulum/Hinge/anchor")
    with pytest.raises(ValueError, match="World/Pendulum/Hinge/anchor.*not a Site"):
        add_joint_anchor_sites(builder)

"""Physical limits use nv offsets even when a preceding free joint has nq=7."""

import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")

from newton.solvers import SolverMuJoCo

from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation

pytestmark = pytest.mark.newton


def builder():
    result = newton.ModelBuilder(up_axis=newton.Axis.Z)
    SolverMuJoCo.register_custom_attributes(result)
    bodies = []
    for index in range(3):
        body = result.add_link(
            xform=wp.transform((0, 0, 1 + index * 0.1), wp.quat_identity()), label=f"/World/Link{index}"
        )
        result.add_shape_box(body, hx=0.02, hy=0.02, hz=0.02, label=f"/World/Link{index}/Shape")
        bodies.append(body)
    free = result.add_joint_free(bodies[0], label="/World/Free")
    prism = result.add_joint_prismatic(
        bodies[0],
        bodies[1],
        axis=(1, 0, 0),
        parent_xform=wp.transform((0, 0, 0.1), wp.quat_identity()),
        limit_lower=-0.03,
        limit_upper=0.04,
        label="/World/Slide",
    )
    revolute = result.add_joint_revolute(
        bodies[1],
        bodies[2],
        axis=(0, 0, 1),
        parent_xform=wp.transform((0, 0, 0.1), wp.quat_identity()),
        limit_lower=-0.7,
        limit_upper=1.2,
        label="/World/Hinge",
    )
    result.add_articulation([free, prism, revolute], label="/World/Chain")
    return result


def test_query_returns_copied_physical_dof_limits_after_free_coordinate_shift():
    with NewtonSimulation("joint-limits", device="cpu", config=SceneConfig(builder, solver="mujoco")) as sim:
        manifest = export_manifest(sim, session_id="limits", scene_revision="r", revision_numeric_id=1)
        port = PhysicsPort(sim, manifest)
        slide = port.bind_joint("/World/Slide")
        assert slide.q.start == 7 and slide.qd.start == 6
        lower, upper = port.joint_limits(slide)
        assert lower.tolist() == pytest.approx([-0.03])
        assert upper.tolist() == pytest.approx([0.04])
        lower[0], upper[0] = -99, 99
        again_lower, again_upper = port.joint_limits("/World/Slide")
        assert again_lower.tolist() == pytest.approx([-0.03])
        assert again_upper.tolist() == pytest.approx([0.04])
        hinge_lower, hinge_upper = port.joint_limits("/World/Hinge")
        assert hinge_lower.tolist() == pytest.approx([-0.7])
        assert hinge_upper.tolist() == pytest.approx([1.2])
        with pytest.raises(KeyError):
            port.joint_limits("/World/Missing")

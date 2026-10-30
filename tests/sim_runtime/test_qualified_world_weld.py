"""World anchors use identity, never the final body's pose; no pose projection."""

import numpy as np
import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")

from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation
from packages.sim_runtime.welds import Welds, declare_weld_pairs

pytestmark = pytest.mark.newton


def test_world_weld_preserves_measured_pose_then_holds_under_real_gravity():
    from newton.solvers import SolverMuJoCo

    def builder():
        b = newton.ModelBuilder(up_axis=newton.Axis.Z)
        SolverMuJoCo.register_custom_attributes(b)
        for name, p in (("held", (0, 0, 1)), ("decoy", (8, -4, 3))):
            body = b.add_body(xform=wp.transform(p, wp.quat_identity()), label="/World/" + name)
            b.add_shape_box(body, hx=.05, hy=.05, hz=.05)
        declare_weld_pairs(b, [(-1, 0)])
        return b

    with NewtonSimulation("world-anchor", config=SceneConfig(builder, solver="mujoco")) as sim:
        welds = Welds(sim.model, sim.solver, sim.builder.spatial_weld_pairs)
        q, poses = sim.state.joint_q.numpy().copy(), sim.state.body_q.numpy().copy()
        welds.set(-1, 0, True, poses)
        welds.flush()
        np.testing.assert_array_equal(sim.state.joint_q.numpy(), q)
        np.testing.assert_array_equal(sim.state.body_q.numpy(), poses)
        np.testing.assert_allclose(welds.relpose[0], poses[0], atol=1e-7)
        native = sim.solver.mj_model
        assert 0 in (native.eq_obj1id[0], native.eq_obj2id[0])
        sim.step(240)
        assert np.linalg.norm(sim.state.body_q.numpy()[0, :3] - poses[0, :3]) < .002
        assert sim.state.body_q.numpy()[1, 2] < poses[1, 2] - 1

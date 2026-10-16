import numpy as np
import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from packages.sim_runtime.simulation import NewtonSimulation


def test_falling_box_moves_only_when_stepped_and_reset_restores_fk():
    with NewtonSimulation(device="cpu") as sim:
        initial = sim.body_poses()
        assert initial[0, :3] == pytest.approx([0, 0, 1.5])
        assert sim.sim_time == 0
        sim.step(24)
        assert sim.sim_time == pytest.approx(0.1)
        assert sim.body_poses()[0, 2] < 1.48
        assert not np.allclose(sim.body_poses()[1], initial[1])
        sim.pause()
        sim.advance(0.1)
        assert sim.sim_time == pytest.approx(0.1)
        sim.step(1)
        assert sim.sim_time == pytest.approx(25 / 240)
        sim.reset()
        assert sim.epoch == 1
        assert sim.sim_time == 0
        assert sim.paused
        np.testing.assert_allclose(sim.body_poses(), initial, atol=1e-6)


def test_initial_joint_pose_comes_from_fk_before_any_step():
    with NewtonSimulation(device="cpu") as sim:
        # The pendulum body builder origin deliberately differs from its FK pose.
        # Joint anchor at (1, 0, 1.5), COM offset 0.35 m, initial angle pi/4.
        assert sim.body_poses()[1, :3] == pytest.approx([1.24748737, 0, 1.25251263], abs=1e-6)

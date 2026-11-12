"""PhysicsPort deterministic rigid-body linear and angular velocity capture."""

import math

import numpy as np
import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation


def _rotated_free_body_config(*, solver="mujoco"):
    def build():
        import newton
        import warp as wp

        builder = newton.ModelBuilder(up_axis=newton.Axis.Z, gravity=(0.0, 0.0, 0.0))
        half_sqrt2 = math.sqrt(0.5)
        # +90 deg yaw around world Z: local +X -> world +Y, local +Y -> world -X, local +Z -> world +Z
        body_quat = wp.quat(0.0, 0.0, half_sqrt2, half_sqrt2)
        body = builder.add_link(
            xform=wp.transform(wp.vec3(1.0, 2.0, 0.5), body_quat),
            label="World/Lab/Robot/Base",
            mass=2.0,
            com=wp.vec3(0.25, 0.0, 0.0),
            inertia=wp.mat33(0.02, 0.0, 0.0, 0.0, 0.02, 0.0, 0.0, 0.0, 0.02),
        )
        builder.add_shape_box(body, hx=0.1, hy=0.1, hz=0.1)
        free_joint = builder.add_joint_free(child=body, label="World/Lab/Robot/FloatingBase")
        builder.add_articulation([free_joint], label="World/Lab/Robot")

        # Reference frame body rotated -90 deg around world X: local +Y -> world -Z, local +Z -> world +Y
        ref_quat = wp.quat(-half_sqrt2, 0.0, 0.0, half_sqrt2)
        ref = builder.add_body(
            xform=wp.transform(wp.vec3(0.0, 0.0, 0.0), ref_quat),
            label="World/Lab/RefFrame",
            mass=0.0,
        )
        builder.add_shape_sphere(ref, radius=0.05)
        return builder

    return SceneConfig(builder=build, solver=solver, physics_dt=0.001, warmup_steps=1)


def test_physics_port_reads_world_and_local_rigid_body_velocities_and_resets():
    import newton

    config = _rotated_free_body_config(solver="mujoco")
    with NewtonSimulation("velocity-probe", device="cpu", solver="mujoco", config=config) as sim:
        manifest = export_manifest(
            sim,
            session_id="vel-session",
            scene_revision="rev-1",
            revision_numeric_id=1,
            publish_hz=30,
            realtime_factor=1.0,
            strict_labels=True,
        )
        port = PhysicsPort(sim, manifest)
        base_key = "/World/Lab/Robot/Base"
        ref_key = "/World/Lab/RefFrame"

        np.testing.assert_allclose(port.body_linear_velocity(base_key), [0.0, 0.0, 0.0], atol=1e-7)
        np.testing.assert_allclose(port.body_angular_velocity(base_key), [0.0, 0.0, 0.0], atol=1e-7)

        # Assign CoM world linear velocity [1.2, -0.4, 0.3] and world angular velocity [0.5, -1.0, 2.0]
        v_world = np.array([1.2, -0.4, 0.3], dtype=np.float32)
        w_world = np.array([0.5, -1.0, 2.0], dtype=np.float32)
        sim.state.joint_qd.assign(np.concatenate([v_world, w_world]))
        newton.eval_fk(sim.model, sim.state.joint_q, sim.state.joint_qd, sim.state)

        p_before = sim.state.body_q.numpy()[port.bind_body(base_key), :3].copy()
        sim.step(1)
        port.capture()
        p_after = port.body_pose(base_key)[0]
        dp_dt = (p_after - p_before) / sim.physics_dt

        # World-frame CoM linear velocity and world-frame angular velocity
        np.testing.assert_allclose(port.body_linear_velocity(base_key), v_world, atol=5e-3)
        np.testing.assert_allclose(port.body_angular_velocity(base_key), w_world, atol=5e-3)

        # Origin linear velocity accounts for non-zero local CoM rotated into world frame
        np.testing.assert_allclose(
            port.body_linear_velocity(base_key, point="origin"), dp_dt, atol=5e-3
        )

        # Body-local frame (+90 deg yaw about Z): v_local = [v_y_world, -v_x_world, v_z_world]
        expected_v_local = np.array([v_world[1], -v_world[0], v_world[2]], dtype=np.float64)
        expected_w_local = np.array([w_world[1], -w_world[0], w_world[2]], dtype=np.float64)
        np.testing.assert_allclose(
            port.body_linear_velocity(base_key, frame="body"), expected_v_local, atol=5e-3
        )
        np.testing.assert_allclose(
            port.body_linear_velocity(base_key, frame=base_key), expected_v_local, atol=5e-3
        )
        np.testing.assert_allclose(
            port.body_angular_velocity(base_key, frame="body"), expected_w_local, atol=5e-3
        )
        np.testing.assert_allclose(
            port.body_angular_velocity(base_key, frame=base_key), expected_w_local, atol=5e-3
        )

        # Arbitrary reference body frame (-90 deg about X: q^-1 = +90 deg about X -> [x, -z, y])
        expected_w_ref = np.array([w_world[0], -w_world[2], w_world[1]], dtype=np.float64)
        np.testing.assert_allclose(
            port.body_angular_velocity(base_key, frame=ref_key), expected_w_ref, atol=5e-3
        )

        with pytest.raises(ValueError, match="point"):
            port.body_linear_velocity(base_key, point="invalid")

        sim.reset()
        port.reset()
        np.testing.assert_allclose(port.body_linear_velocity(base_key), [0.0, 0.0, 0.0], atol=1e-7)
        np.testing.assert_allclose(port.body_angular_velocity(base_key), [0.0, 0.0, 0.0], atol=1e-7)

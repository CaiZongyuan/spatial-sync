"""Real solver identity/distance checks; no sanitized body-name matching."""

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
    result.add_shape_box(
        -1,
        xform=wp.transform((0, 0, -0.1), wp.quat_identity()),
        hx=0.5,
        hy=0.5,
        hz=0.1,
        label="/World/Fixture/Obstacle",
    )
    body = result.add_body(xform=wp.transform((0, 0, 0.095), wp.quat_identity()), label="/World/Payload")
    result.add_shape_box(body, hx=0.1, hy=0.1, hz=0.1, label="/World/Payload/Floor")
    return result


def port(sim):
    return PhysicsPort(
        sim, export_manifest(sim, session_id="contact-test", scene_revision="r", revision_numeric_id=1)
    )


def test_payload_only_obstacle_collision_has_real_newton_identity_and_signed_distance():
    with NewtonSimulation("contact-test", device="cpu", config=SceneConfig(builder, solver="mujoco")) as sim:
        observed = port(sim)
        sim.step(1)
        observed.capture()
        contacts = observed.collision_contacts()
        assert contacts
        assert all(set(row.shapes) == {"/World/Payload/Floor", "/World/Fixture/Obstacle"} for row in contacts)
        assert all(set(row.bodies) == {-1, observed.bind_body("/World/Payload")} for row in contacts)
        assert min(row.distance_m for row in contacts) < -0.001
        assert observed.collision_contacts(max_distance=-0.1) == []
        with pytest.raises(ValueError, match="finite"):
            observed.collision_contacts(max_distance=float("nan"))


def test_unsupported_solver_is_unknown_rather_than_an_empty_collision_list():
    with NewtonSimulation("contact-test", device="cpu", config=SceneConfig(builder, solver="xpbd")) as sim:
        with pytest.raises(ValueError, match="require"):
            port(sim).collision_contacts()

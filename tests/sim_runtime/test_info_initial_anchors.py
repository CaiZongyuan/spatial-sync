"""Weld-only initialization keeps the existing first actuator/physics boundary."""

import numpy as np
import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")

from newton.solvers import SolverMuJoCo

from packages.info.catalog import load_catalog
from packages.info.program import ProgramConfig
from packages.protocol.schema import SessionOptions
from packages.sim_runtime import info_host
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation
from packages.sim_runtime.welds import declare_weld_pairs
from tests.unit.test_info_host import definition_root

pytestmark = pytest.mark.newton


class InitialCommands:
    id, type = "initial.commands", "counter/v1"

    class Config(ProgramConfig):
        qualifiedPlacement: bool = False

    def init(self, ctx):
        ctx.set(value=0)
        ctx.physics.set_joint_target(ctx.physics.bind_joint("/World/Slide"), [0.3])
        if ctx.config.qualifiedPlacement:
            ctx.physics.weld(-1, ctx.physics.bind_body("/World/Payload"))


def builder():
    value = newton.ModelBuilder(up_axis=newton.Axis.Z)
    SolverMuJoCo.register_custom_attributes(value)
    payload = value.add_body(xform=wp.transform((0, 0, 1), wp.quat_identity()), label="/World/Payload")
    value.add_shape_box(payload, hx=0.05, hy=0.05, hz=0.05, label="/World/Payload/Shape")
    driven = value.add_link(label="/World/Driven")
    value.add_shape_box(driven, hx=0.05, hy=0.05, hz=0.05, label="/World/Driven/Shape")
    joint = value.add_joint_prismatic(
        -1,
        driven,
        axis=(1, 0, 0),
        parent_xform=wp.transform((3, 0, 1), wp.quat_identity()),
        target_ke=1000,
        target_kd=100,
        limit_lower=-1,
        limit_upper=1,
        label="/World/Slide",
    )
    value.add_articulation([joint], label="/World/Actuator")
    declare_weld_pairs(value, [(-1, payload)])
    return value


@pytest.mark.parametrize("qualified", [False, True])
def test_initial_and_reset_anchors_flush_without_advancing_actuator_commit(tmp_path, monkeypatch, qualified):
    config = SceneConfig(builder, solver="mujoco")
    with NewtonSimulation("initial-boundary", config=config) as baseline:
        baseline.step(1)
        expected_q = baseline.state.joint_q.numpy().copy()
        expected_qd = baseline.state.joint_qd.numpy().copy()
        expected_poses = baseline.state.body_q.numpy().copy()
    definition_root(
        tmp_path,
        bindings=[
            {
                "key": "/World/Payload",
                "type": InitialCommands.type,
                "program": InitialCommands.id,
                "config": {"qualifiedPlacement": qualified},
            }
        ],
    )
    registry = {InitialCommands.id: InitialCommands}
    with NewtonSimulation("initial-boundary", config=config) as sim:
        manifest = export_manifest(sim, session_id="init", scene_revision="init", revision_numeric_id=1)
        catalog = load_catalog(manifest, "test", registry, root=tmp_path)
        monkeypatch.setattr(info_host, "load_info_catalog", lambda *args, **kwargs: catalog)
        monkeypatch.setattr(info_host, "REGISTRY", registry)
        initial_q, initial_poses = sim.state.joint_q.numpy().copy(), sim.state.body_q.numpy().copy()
        control = sim.control.joint_target_q.numpy().copy()
        host = info_host.NewtonInfoHost(sim, manifest, SessionOptions(), lambda rows: None)
        slide = host.port.bind_joint("/World/Slide")
        payload = host.port.bind_body("/World/Payload")
        np.testing.assert_array_equal(sim.control.joint_target_q.numpy(), control)
        assert host.port.target_q[slide.target] == pytest.approx([0.3])
        np.testing.assert_array_equal(sim.state.joint_q.numpy(), initial_q)
        np.testing.assert_array_equal(sim.state.body_q.numpy(), initial_poses)
        assert host.port.welds.active(-1, payload) is qualified
        assert bool(sim.model.mujoco.equality_constraint_enabled.numpy()[0]) is qualified
        sim.step(1)
        np.testing.assert_array_equal(sim.control.joint_target_q.numpy(), control)
        if not qualified:
            np.testing.assert_array_equal(sim.state.joint_q.numpy(), expected_q)
            np.testing.assert_array_equal(sim.state.joint_qd.numpy(), expected_qd)
        else:
            assert sim.state.body_q.numpy()[payload, 2] > expected_poses[payload, 2]
        sim.step(3)
        assert sim.control.joint_target_q.numpy()[slide.target] == pytest.approx([0.3])
        sim.reset()
        assert host.port.welds.active(-1, payload) is qualified
        assert bool(sim.model.mujoco.equality_constraint_enabled.numpy()[0]) is qualified
        np.testing.assert_array_equal(sim.control.joint_target_q.numpy(), control)
        sim.step(1)
        np.testing.assert_array_equal(sim.control.joint_target_q.numpy(), control)
        host.close()

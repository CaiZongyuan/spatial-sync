"""Real native contacts must survive Newton's post-compile inertial-frame update."""

import numpy as np
import pytest

newton = pytest.importorskip("newton")
mujoco = pytest.importorskip("mujoco")
wp = pytest.importorskip("warp")
pytestmark = pytest.mark.newton

from newton.solvers import SolverMuJoCo

from packages.sim_runtime import mujoco_bvh
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation
from packages.sim_runtime.welds import Welds, declare_weld_pairs


def _compound_scene():
    builder = newton.ModelBuilder(up_axis=newton.Axis.Z, gravity=(0, 0, 0))
    SolverMuJoCo.register_custom_attributes(builder)
    cfg = builder.default_shape_cfg.copy()
    cfg.gap = 0.001
    obstacle = builder.add_body(label="/Fixture/Compound")
    for index, (x, y) in enumerate(((0.15, 0), (0, 0.15), (-0.15, 0), (0, -0.15))):
        builder.add_shape_box(
            obstacle,
            xform=wp.transform((x, y, 0.0642), wp.quat_identity()),
            hx=0.015,
            hy=0.012,
            hz=0.0225,
            cfg=cfg,
            label=f"/Fixture/Compound/Part{index}",
        )
    # Repeated principal moments admit different equivalent bases in the two eigensolvers.
    builder.body_mass[obstacle] = 0.6280884742736816
    builder.body_com[obstacle] = wp.vec3(-7.3676069689554424e-9, -1.946160343990755e-9, 0.06419994682073593)
    builder.body_inertia[obstacle] = wp.mat33(
        0.007403152994811535,
        6.022782272907534e-11,
        0,
        7.963470161076458e-11,
        0.00740315206348896,
        0,
        0,
        0,
        0.014594328589737415,
    )
    probe = builder.add_body(
        xform=wp.transform((0.169, 0, 0.0642), wp.quat_identity()), label="/Fixture/Probe"
    )
    builder.add_shape_sphere(probe, radius=0.0085, cfg=cfg, label="/Fixture/Probe/Geometry")
    declare_weld_pairs(builder, [(obstacle, probe)])
    return builder


def _known_contact(model, data):
    names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, i) for i in range(model.ngeom)]
    first = next(i for i, name in enumerate(names) if "/Compound/Part0_" in name)
    second = next(i for i, name in enumerate(names) if "/Probe/Geometry_" in name)
    mujoco.mj_forward(model, data)
    distance = mujoco.mj_geomDistance(model, data, first, second, 1, np.zeros(6))
    count = sum(
        {int(data.contact[i].geom1), int(data.contact[i].geom2)} == {first, second} for i in range(data.ncon)
    )
    return distance, count


def test_cpu_constructor_recovers_contacts_without_changing_physical_inputs(monkeypatch):
    original = mujoco_bvh.normalize_body_bvh
    captured = []
    fields = (
        "body_mass",
        "body_inertia",
        "body_ipos",
        "body_iquat",
        "geom_pos",
        "geom_quat",
        "geom_aabb",
        "geom_size",
        "geom_margin",
        "geom_gap",
        "geom_contype",
        "geom_conaffinity",
        "bvh_child",
        "bvh_nodeid",
        "body_bvhadr",
        "body_bvhnum",
    )

    def capture_and_normalize(model):
        physical = {name: np.array(getattr(model, name), copy=True) for name in fields}
        captured.append(model.bvh_aabb.copy())
        result = original(model)
        for name, before in physical.items():
            np.testing.assert_array_equal(getattr(model, name), before)
        return result

    monkeypatch.setattr(mujoco_bvh, "normalize_body_bvh", capture_and_normalize)
    with NewtonSimulation(
        "bvh-regression", device="cpu", config=SceneConfig(_compound_scene, solver="mujoco")
    ) as sim:
        model, data = sim.solver.mj_model, sim.solver.mj_data
        assert len(captured) == 1
        assert not int(model.opt.disableflags) & int(mujoco.mjtDisableBit.mjDSBL_MIDPHASE)
        distance, count = _known_contact(model, data)
        assert distance == pytest.approx(-0.0045, abs=1e-7)
        assert count == 1
        state = data.qpos.copy(), data.qvel.copy()
        model.bvh_aabb[:] = captured[0]
        assert _known_contact(model, data)[1] == 0
        assert original(model)
        assert _known_contact(model, data)[1] == 1
        assert original(model) == ()
        np.testing.assert_array_equal(data.qpos, state[0])
        np.testing.assert_array_equal(data.qvel, state[1])
        normalized = model.bvh_aabb.copy()
        sim.reset()
        data = sim.solver.mj_data
        np.testing.assert_array_equal(model.bvh_aabb, normalized)
        assert _known_contact(model, data)[1] == 1
        welds = Welds(sim.model, sim.solver, sim.builder.spatial_weld_pairs)
        parent, child = next(iter(welds.pairs))
        for enabled in (True, False):
            welds.set(parent, child, enabled, sim.state.body_q.numpy())
            welds.flush()
            np.testing.assert_array_equal(model.bvh_aabb, normalized)
        assert _known_contact(model, data)[1] == 1


@pytest.mark.parametrize("span", [0.0, 5e-15])
def test_flat_bounds_keep_native_compiler_thickness(span):
    # MuJoCo 3.15 user_objects.cc inflates each endpoint by mjEPS when span < mjEPS.
    result = mujoco_bvh._box(np.zeros(3), np.full(3, span))
    np.testing.assert_allclose(result[:3] - result[3:], np.full(3, -1e-14), rtol=0, atol=1e-30)
    np.testing.assert_allclose(result[:3] + result[3:], np.full(3, span + 1e-14), rtol=0, atol=1e-30)

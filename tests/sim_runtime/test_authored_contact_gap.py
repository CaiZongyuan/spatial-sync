"""USD contactGap narrows inactive storage without changing physical force onset."""

import numpy as np
import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")
pytest.importorskip("pxr.Usd")

import mujoco
from newton.solvers import SolverMuJoCo
from pxr import Sdf, Usd, UsdGeom

from factory.cell_passage import box, rigid, translate
from packages.sim_runtime.loaders import import_usd
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation

pytestmark = pytest.mark.newton


def observe(distance, baseline=False):
    def builder():
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, "Z")
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        UsdGeom.Xform.Define(stage, "/World/Lab")
        floor = box(stage, "/World/Lab/Floor", (0, 0, -0.1), (1, 1, 0.2))
        body = rigid(stage, "/World/Lab/Box", 8.0)
        translate(body, (0, 0, 0.1 + distance))
        moving = box(stage, "/World/Lab/Box/Shape", (0, 0, 0), (0.2, 0.2, 0.2))
        if baseline:
            for shape in (floor, moving):
                shape.GetPrim().CreateAttribute("newton:contactGap", Sdf.ValueTypeNames.Float).Set(0.1)
        result = newton.ModelBuilder(up_axis=newton.Axis.Z)
        SolverMuJoCo.register_custom_attributes(result)
        import_usd(result, stage, root_path="/World/Lab")
        return result

    with NewtonSimulation("gap", device="cpu", config=SceneConfig(builder, solver="mujoco")) as sim:
        model, data = sim.solver.mj_model, sim.solver.mj_data
        contacts = data.contact[: data.ncon]
        forces = []
        for index in range(data.ncon):
            force = np.zeros(6)
            mujoco.mj_contactForce(model, data, index, force)
            forces.append(force[0])
        return {
            "margins": model.geom_margin.copy(),
            "gaps": model.geom_gap.copy(),
            "distances": np.array([row.dist for row in contacts]),
            "cutoffs": np.array([row.includemargin for row in contacts]),
            "active": np.array([row.efc_address >= 0 for row in contacts]),
            "forces": np.array(forces),
        }


def test_factory_gap_prunes_distant_inactive_pairs_and_preserves_active_force_cutoff():
    baseline, authored = observe(0.01, True), observe(0.01)
    assert baseline["distances"].size == 4 and not baseline["active"].any()
    assert authored["gaps"] == pytest.approx([0.002, 0.002])
    assert authored["distances"].size == 0
    for distance in (0.001, -0.0005, -0.005):
        baseline, authored = observe(distance, True), observe(distance)
        np.testing.assert_array_equal(authored["margins"], baseline["margins"])
        np.testing.assert_array_equal(authored["cutoffs"], baseline["cutoffs"])
        np.testing.assert_array_equal(authored["active"], baseline["active"])
        np.testing.assert_allclose(authored["distances"], baseline["distances"], atol=1e-12)
        np.testing.assert_allclose(authored["forces"], baseline["forces"], atol=1e-12)
        assert bool(authored["active"].all()) == (distance < 0)

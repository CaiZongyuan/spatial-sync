"""The real Asset validator selects only an explicitly declared solver."""

import hashlib
import json

import pytest
import yaml

pytest.importorskip("newton")
pytest.importorskip("warp")

from packages.library.metadata import asset_metadata
from packages.library.physics import simulation
from packages.library.physics import validate as validate_physics
from packages.library.structural import validate
from packages.library.validation import StructuralValidator
from packages.sim_runtime.library_scene import prepare_scene_config

pytestmark = pytest.mark.newton


def payload(tmp_path, declared):
    document = {"id": "labware/solver-box", "kind": "labware", "title": "Solver Box", "stage": "asset.usda"}
    if declared is not None:
        document["simulation"] = declared
    (tmp_path / "asset.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    (tmp_path / "LICENSE").write_text("Synthetic test fixture authored for this repository.\n",
                                      encoding="utf-8")
    (tmp_path / "asset.usda").write_text('''#usda 1.0
(metersPerUnit = 1
 upAxis = "Z"
 defaultPrim = "Asset")
def Xform "Asset" {
 def PhysicsScene "Physics" {
  custom double mjc:option:timestep = 0.0005
  custom int mjc:option:iterations = 60
  custom int mjc:option:ls_iterations = 50
  custom int mjc:option:noslip_iterations = 4
 }
 def Xform "Box" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]) {
  float physics:mass = 1
  def Cube "Collider" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
   double size = 0.1
  }
 }
}
''', encoding="utf-8")
    files = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in tmp_path.iterdir()}
    (tmp_path / "provenance.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    return {"kind": "asset", "subject": "asset:labware/solver-box@1",
            "metadata": asset_metadata(tmp_path), "package": StructuralValidator.scope(tmp_path),
            "assets": [], "programs": {}, "types": {}}


def test_explicit_asset_mujoco_reaches_actual_solver_and_two_second_validation(tmp_path):
    value = payload(tmp_path, {"solver": "mujoco", "physicsDt": .0005, "warmupSteps": 2})
    value["structure"] = validate(value)
    assert not [row for row in value["structure"]["checks"] if row["status"] == "fail"]
    with simulation(value) as sim:
        assert sim.solver_kind == "mujoco" and sim.model.device.is_cpu
        assert sim.physics_dt == .0005
        native = sim.solver.mj_model
        assert (native.opt.iterations, native.opt.ls_iterations, native.opt.noslip_iterations) == (60, 50, 4)
    result = validate_physics(value)
    assert result["physics"]["status"] == "completed"
    assert result["physics"]["solver"] == "mujoco"
    assert result["physics"]["physicsDt"] == .0005
    assert result["physics"]["simulationSeconds"] == pytest.approx(2., abs=1e-10)
    assert not any(row["status"] == "fail" for row in result["checks"])


def test_old_asset_does_not_infer_mujoco_from_authored_mjc_options(tmp_path):
    value = payload(tmp_path, None)
    assert "simulation" not in value["metadata"]
    with simulation(value) as sim:
        assert sim.solver_kind == "xpbd"
        assert sim.physics_dt == .0005


def test_asset_metadata_and_usd_clock_mismatch_is_rejected_before_allocation(tmp_path):
    value = payload(tmp_path, {"solver": "mujoco", "physicsDt": .001})
    structure = validate(value)
    assert next(row for row in structure["checks"] if row["id"] == "S2")["status"] == "fail"
    with pytest.raises(ValueError, match="physicsDt differs"):
        prepare_scene_config(value)

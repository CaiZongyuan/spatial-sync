"""CPU scene compilation against the pinned trainer SDK, including real contacts."""

import os
import subprocess
import sys
from pathlib import Path

STAGE = """#usda 1.0
(defaultPrim = "World"; metersPerUnit = 1; upAxis = "Z")
def Xform "World" {
    def Xform "Lab" (
        prepend apiSchemas = ["MaterialBindingAPI"]
    ) {
        rel material:binding:physics = </World/Lab/Grip>
        def Material "Grip" (prepend apiSchemas = ["PhysicsMaterialAPI"]) {
            float physics:dynamicFriction = 0.37
        }
        def Plane "Floor" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
            uniform token axis = "Z"
        }
        def Xform "Robot" (prepend apiSchemas = ["PhysicsArticulationRootAPI"]) {
            def Xform "A" (prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsFilteredPairsAPI"]) {
                rel physics:filteredPairs = </World/Lab/Robot/C>
                double3 xformOp:translate = (0, 0, 1)
                uniform token[] xformOpOrder = ["xformOp:translate"]
                def Sphere "Ball" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
                    double radius = 0.1
                }
            }
            def Xform "B" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {
                def Sphere "Ball" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
                    double radius = 0.1
                }
            }
            def Xform "C" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {
                def Sphere "Ball" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
                    double radius = 0.1
                }
            }
            def PhysicsRevoluteJoint "AB" {
                rel physics:body0 = </World/Lab/Robot/A>
                rel physics:body1 = </World/Lab/Robot/B>
                point3f physics:localPos0 = (0.1, 0, 0)
                bool physics:collisionEnabled = true
            }
            def PhysicsRevoluteJoint "BC" {
                rel physics:body0 = </World/Lab/Robot/B>
                rel physics:body1 = </World/Lab/Robot/C>
            }
        }
    }
}
"""


def test_final_mjlab_scene_preserves_usd_contacts(tmp_path):
    import pytest

    trainer = os.environ.get("SPATIAL_MJLAB_TEST_PYTHON")
    if not trainer:
        pytest.importorskip("mjlab")
        trainer = sys.executable
    path = tmp_path / "stage.usda"
    path.write_text(STAGE)
    root = Path(__file__).resolve().parents[2]
    environment = os.environ | {"PYTHONPATH": str(root), "CUDA_VISIBLE_DEVICES": "", "MUJOCO_GL": "disable"}
    subprocess.run([trainer, str(Path(__file__).resolve()), str(path)],
                   cwd=tmp_path, env=environment, check=True, timeout=60)


def check_composition(path):
    import math

    import mujoco
    import mujoco_warp
    import warp as wp
    from mjlab.entity import EntityCfg
    from mjlab.scene import Scene, SceneCfg
    from mjlab.terrains.terrain_entity import TerrainEntityCfg

    from apps.node import usd_mjcf

    derived = usd_mjcf.derive_usd_mjcf(
        path, prim_path="/World/Lab/Robot", include_static_colliders=False
    )

    def contacts(model):
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        return {
            frozenset(model.geom(int(i)).name.rsplit("/", 1)[-1] for i in (contact.geom1, contact.geom2))
            for contact in data.contact
        }

    def callback(spec):
        spec.option.gravity[2] = -8
        # Translation and rotation within an infinite plane are physically equivalent.
        spec.geom("terrain").pos = (.3, -.4, 0.)
        spec.geom("terrain").quat = (math.sqrt(.5), 0., 0., math.sqrt(.5))
        spec.add_sensor(name="floor-position", type=mujoco.mjtSensor.mjSENS_FRAMEPOS,
                        objtype=mujoco.mjtObj.mjOBJ_GEOM, objname="terrain")

    terrain = TerrainEntityCfg(terrain_type="plane")
    cfg = SceneCfg(
        terrain=terrain, entities={"robot": EntityCfg(spec_fn=derived.to_mjspec)}, spec_fn=callback,
    )
    expected = {frozenset(("A_Ball", "B_Ball"))}
    assert contacts(derived.to_mjmodel()) == expected
    old_model = Scene(cfg, device="cpu").compile()
    assert contacts(old_model) == set()
    original_origin = old_model.site("env_origin_0").pos.copy()
    usd_mjcf.configure_mjlab_usd_scene(cfg, path)
    final = Scene(cfg, device="cpu").compile()
    assert contacts(final) == expected
    assert math.isclose(final.geom("terrain").friction[0], .37, abs_tol=1e-7)
    assert final.sensor("floor-position").objid[0] == final.geom("terrain").id
    assert (final.site("env_origin_0").pos == original_origin).all()
    assert final.opt.gravity[2] == -8
    with wp.ScopedDevice("cpu"):
        compiled = mujoco_warp.put_model(final)
        pairs = compiled.nxn_geom_pair_filtered.numpy()
        enabled = compiled.nxn_pairid_filtered.numpy()[:, 0] > -2
    robot_pairs = {
        frozenset(final.geom(int(index)).name for index in pair)
        for pair in pairs[enabled]
        if all(final.geom(int(index)).name.startswith("robot/") for index in pair)
    }
    assert robot_pairs == {frozenset(("robot/A_Ball", "robot/B_Ball"))}
    print("Measured allowed pairs", contacts(final), "and ground friction", final.geom("terrain").friction[0])

    def move_parent(spec):
        spec.body("terrain").pos = (0., 0., .25)

    def tilt_plane(spec):
        spec.geom("terrain").quat = (math.sqrt(.5), math.sqrt(.5), 0., 0.)

    def disable_contacts(spec):
        spec.geom("terrain").contype = spec.geom("terrain").conaffinity = 2

    for override in (move_parent, tilt_plane, disable_contacts):
        altered = SceneCfg(terrain=terrain, entities=cfg.entities, spec_fn=override)
        usd_mjcf.configure_mjlab_usd_scene(altered, path)
        try:
            Scene(altered, device="cpu").compile()
        except usd_mjcf.UnsupportedUsdPhysics as exc:
            assert exc.prim_path == "/World/Lab/Floor"
        else:
            raise AssertionError(f"Final scene accepted ground override {override.__name__}")


if __name__ == "__main__":
    check_composition(Path(sys.argv[1]))

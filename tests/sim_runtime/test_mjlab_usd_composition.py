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
    run_pinned_composition(tmp_path, "parent")


def test_final_mjlab_scene_rejects_lost_ground_pairs(tmp_path):
    run_pinned_composition(tmp_path, "mask")


def test_final_mjlab_scene_rejects_lost_ground_friction(tmp_path):
    run_pinned_composition(tmp_path, "friction")


def test_mjlab_simulation_rejects_contact_disabled_after_scene_composition(tmp_path):
    run_pinned_composition(tmp_path, "disable")


def test_mjlab_simulation_rejects_robot_exclusion_added_by_scene_callback(tmp_path):
    run_pinned_composition(tmp_path, "exclude")


def test_mjlab_simulation_rejects_shifted_usd_collider(tmp_path):
    run_pinned_composition(tmp_path, "geometry")


def test_mjlab_simulation_compares_compiled_meshes_in_the_link_frame(tmp_path):
    run_pinned_composition(tmp_path, "mesh")


def run_pinned_composition(tmp_path, case):
    import pytest

    trainer = os.environ.get("SPATIAL_MJLAB_TEST_PYTHON")
    if not trainer:
        pytest.importorskip("mjlab")
        trainer = sys.executable
    path = tmp_path / "stage.usda"
    path.write_text(STAGE)
    root = Path(__file__).resolve().parents[2]
    environment = os.environ | {"PYTHONPATH": str(root), "CUDA_VISIBLE_DEVICES": "", "MUJOCO_GL": "disable"}
    result = subprocess.run([trainer, str(Path(__file__).resolve()), str(path), case], cwd=tmp_path,
                            env=environment, capture_output=True, text=True, timeout=60)
    print(result.stdout)
    assert result.returncode == 0, result.stderr


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
    verify = usd_mjcf.configure_mjlab_usd_scene(cfg, path)
    final = Scene(cfg, device="cpu").compile()
    verify(final)
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
        verify = usd_mjcf.configure_mjlab_usd_scene(altered, path)
        try:
            verify(Scene(altered, device="cpu").compile())
        except usd_mjcf.UnsupportedUsdPhysics as exc:
            assert exc.prim_path == "/World/Lab/Floor"
        else:
            raise AssertionError(f"Final scene accepted ground override {override.__name__}")


def check_ground_fidelity(path, case):
    import mujoco
    from mjlab.entity import EntityCfg
    from mjlab.scene import Scene, SceneCfg
    from mjlab.terrains.terrain_entity import TerrainEntityCfg

    from apps.node import usd_mjcf

    # Keep the second collider above the ground to test eligibility independently of current contact.
    path.write_text(STAGE.replace("(0, 0, 1)", "(0, 0, .075)").replace("(0.1, 0, 0)", "(0.3, 0, .3)"))
    derived = usd_mjcf.derive_usd_mjcf(path, prim_path="/World/Lab/Robot", include_static_colliders=False)

    def scene(callback=None, *, validate=True):
        cfg = SceneCfg(terrain=TerrainEntityCfg(terrain_type="plane"),
                       entities={"robot": EntityCfg(spec_fn=derived.to_mjspec)}, spec_fn=callback)
        verify = usd_mjcf.configure_mjlab_usd_scene(cfg, path) if validate else None
        model = Scene(cfg, device="cpu").compile()
        if verify is not None:
            verify(model)
        return model

    def ground_contacts(model):
        ground = model.geom("terrain").id
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        return [(int(contact.dim), float(contact.friction[0])) for contact in data.contact
                if ground in (contact.geom1, contact.geom2)]

    def mutation(spec):
        ground = spec.geom("terrain")
        ground.friction = (.37, .005, .0001)
        if case == "mask":
            geom = spec.geom("robot/A_Ball")
            geom.contype = geom.conaffinity = 2
        else:
            ground.priority = 1
            ground.condim = 1

    original = ground_contacts(scene())
    altered = ground_contacts(scene(mutation, validate=False))
    assert len(original) == 1 and original[0][0] == 3
    if case == "mask":
        assert altered == []
    else:
        assert altered == [(1, .37)]
    print(f"{case} baseline {original}, mutation {altered}", flush=True)
    try:
        scene(mutation)
    except usd_mjcf.UnsupportedUsdPhysics as exc:
        assert exc.prim_path == "/World/Lab/Floor"
        assert ("eligibility" if case == "mask" else "contact parameters") in str(exc)
    else:
        raise AssertionError(f"Final scene accepted {case} mutation")

    def reencode(spec):
        for geom in spec.geoms:
            if geom.contype or geom.conaffinity:
                geom.contype = geom.conaffinity = 2
        spec.geom("terrain").priority = 1

    equivalent = ground_contacts(scene(reencode))
    assert equivalent == original

    def remove_separated_pair(spec):
        # The unchecked specimen uses the same ground material.
        spec.geom("terrain").friction = (.37, .005, .0001)
        geom = spec.geom("robot/B_Ball")
        geom.contype = geom.conaffinity = 2

    if case == "mask":
        assert ground_contacts(scene(remove_separated_pair, validate=False)) == original
        try:
            scene(remove_separated_pair)
        except usd_mjcf.UnsupportedUsdPhysics as exc:
            assert exc.prim_path == "/World/Lab/Floor"
            assert "/World/Lab/Robot/B/Ball" in str(exc)
        else:
            raise AssertionError("Final scene accepted removal of a separated ground pair")


def check_final_simulation(path, case):
    import mujoco
    from mjlab.entity import EntityCfg
    from mjlab.scene import Scene, SceneCfg
    from mjlab.sim import MujocoCfg, Simulation, SimulationCfg
    from mjlab.terrains.terrain_entity import TerrainEntityCfg

    from apps.node import usd_mjcf

    # A touches the floor and overlaps B above it. C remains initially separated.
    path.write_text(STAGE.replace("(0, 0, 1)", "(0, 0, .075)")
                    .replace("(0.1, 0, 0)", "(0.1, 0, .10)")
                    .replace('rel physics:body1 = </World/Lab/Robot/C>',
                             'rel physics:body1 = </World/Lab/Robot/C>\n'
                             '                point3f physics:localPos0 = (0.5, 0, 0)'))
    derived = usd_mjcf.derive_usd_mjcf(path, prim_path="/World/Lab/Robot", include_static_colliders=False)
    callback_calls = []

    def add_exclusion(spec):
        callback_calls.append(1)
        spec.add_exclude(bodyname1="robot/A", bodyname2="robot/B")

    def shift_collider(spec):
        callback_calls.append(1)
        spec.geom("robot/A_Ball").pos[2] += .5

    def build(*, callback=None, disabled=()):
        cfg = SceneCfg(terrain=TerrainEntityCfg(terrain_type="plane"),
                       entities={"robot": EntityCfg(spec_fn=derived.to_mjspec)}, spec_fn=callback)
        verify = usd_mjcf.configure_mjlab_usd_scene(cfg, path)
        scene = Scene(cfg, device="cpu")
        settings = MujocoCfg(timestep=.005, iterations=13, cone="elliptic", disableflags=disabled)
        sim = Simulation(1, SimulationCfg(mujoco=settings), scene.compile(), device="cpu")
        assert sim.mj_model.opt.timestep == .005 and sim.mj_model.opt.iterations == 13
        assert sim.mj_model.opt.cone == mujoco.mjtCone.mjCONE_ELLIPTIC
        data = mujoco.MjData(sim.mj_model)
        mujoco.mj_forward(sim.mj_model, data)
        contacts = {frozenset(sim.mj_model.geom(i).name for i in (row.geom1, row.geom2))
                    for row in data.contact}
        return sim, verify, contacts

    expected = {frozenset(("terrain", "robot/A_Ball")), frozenset(("robot/A_Ball", "robot/B_Ball"))}
    good_sim, good_verify, original = build()
    assert original == expected
    good_verify(good_sim.mj_model)
    if case == "disable":
        sim, verify, actual = build(disabled=("contact",))
        assert actual == set()
        assert sim.mj_model.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_CONTACT
    elif case == "exclude":
        sim, verify, actual = build(callback=add_exclusion)
        assert actual == {frozenset(("terrain", "robot/A_Ball"))}
        assert len(callback_calls) == 1
    else:
        sim, verify, actual = build(callback=shift_collider)
        assert actual == set()
        assert sim.mj_model.geom("robot/A_Ball").pos[2] == .5
        assert len(callback_calls) == 1
    print(f"{case} original={sorted(map(sorted, original))} final={sorted(map(sorted, actual))} "
          f"disableflags={sim.mj_model.opt.disableflags}", flush=True)
    try:
        verify(sim.mj_model)
    except usd_mjcf.UnsupportedUsdPhysics as exc:
        if case == "disable":
            assert exc.prim_path == "/World/Lab/Floor"
        else:
            assert exc.prim_path == "/World/Lab/Robot/A/Ball"
            if case == "exclude":
                assert "/World/Lab/Robot/B/Ball" in str(exc)
            else:
                assert "link-local position" in str(exc)
    else:
        raise AssertionError(f"Final Simulation accepted {case}")


def check_mesh_geometry(path):
    import mujoco
    import numpy as np
    from mjlab.entity import EntityCfg
    from mjlab.scene import Scene, SceneCfg
    from mjlab.sim import Simulation, SimulationCfg
    from mjlab.terrains.terrain_entity import TerrainEntityCfg

    from apps.node import usd_mjcf

    # An asymmetric hull on C stays away from the floor and other links.
    stage = STAGE.replace('def Xform "C" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {', '''
            def Xform "C" (prepend apiSchemas = ["PhysicsRigidBodyAPI"]) {
                def Mesh "Hull" (prepend apiSchemas = ["PhysicsCollisionAPI"]) {
                    point3f[] points = [(0.25, 0, 0), (0.29, 0, 0), (0.25, 0.06, 0), (0.25, 0, 0.08)]
                    int[] faceVertexCounts = [3, 3, 3, 3]
                    int[] faceVertexIndices = [0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3]
                }
    ''')
    path.write_text(stage)
    derived = usd_mjcf.derive_usd_mjcf(path, prim_path="/World/Lab/Robot", include_static_colliders=False)

    def build(callback=None):
        cfg = SceneCfg(terrain=TerrainEntityCfg(terrain_type="plane"),
                       entities={"robot": EntityCfg(spec_fn=derived.to_mjspec)}, spec_fn=callback)
        verify = usd_mjcf.configure_mjlab_usd_scene(cfg, path)
        model = Scene(cfg, device="cpu").compile()
        return Simulation(1, SimulationCfg(), model, device="cpu"), verify

    def reframe_mesh(spec, *, compensate):
        geom = spec.geom("robot/C_Hull")
        mesh = spec.mesh(geom.meshname)
        vertices = np.asarray(mesh.uservert).reshape(-1, 3)
        # Offsetting mesh authoring coordinates changes the compiler's principal
        # frame. The inverse geom offset preserves every physical vertex.
        order = [2, 0, 3, 1]
        indices = np.argsort(order)
        offset = np.array([.5, 0., 0.])
        mesh.uservert = (vertices[order] + offset).ravel()
        mesh.userface = indices[np.asarray(mesh.userface)].ravel()
        if compensate:
            geom.pos[:] -= offset
            spec.geom("robot/A_Ball").quat[:] *= -1

    def forward_mesh_center(sim):
        model = sim.mj_model
        data = mujoco.MjData(model)
        mujoco.mj_forward(model, data)
        geom = model.geom("robot/C_Hull").id
        assert all(geom not in (contact.geom1, contact.geom2) for contact in data.contact)
        mesh = model.geom_dataid[geom]
        start, count = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
        local_center = model.mesh_vert[start:start + count].mean(axis=0)
        return data.geom_xpos[geom] + data.geom_xmat[geom].reshape(3, 3) @ local_center

    before, verify_before = build()
    verify_before(before.mj_model)
    equivalent, verify_equivalent = build(lambda spec: reframe_mesh(spec, compensate=True))
    verify_equivalent(equivalent.mj_model)
    center = forward_mesh_center(before)
    np.testing.assert_allclose(forward_mesh_center(equivalent), center, rtol=0., atol=1e-7)
    data = mujoco.MjData(equivalent.mj_model)
    # Model geometry is independent of where a reset or an initial pose puts the root.
    data.qpos[2] += .75
    mujoco.mj_forward(equivalent.mj_model, data)
    verify_equivalent(equivalent.mj_model)

    changed, verify_changed = build(lambda spec: reframe_mesh(spec, compensate=False))
    np.testing.assert_allclose(forward_mesh_center(changed) - center, [.5, 0, 0], rtol=0., atol=1e-7)
    try:
        verify_changed(changed.mj_model)
    except usd_mjcf.UnsupportedUsdPhysics as exc:
        assert exc.prim_path == "/World/Lab/Robot/C/Hull"
        assert "link-local collider vertices" in str(exc)
    else:
        raise AssertionError("Final Simulation accepted a shifted mesh")
    print("Equivalent mesh frame, vertex order, quaternion sign and root pose accepted", flush=True)


if __name__ == "__main__":
    path, case = Path(sys.argv[1]), sys.argv[2]
    if case == "parent":
        check_composition(path)
    elif case in ("mask", "friction"):
        check_ground_fidelity(path, case)
    elif case == "mesh":
        check_mesh_geometry(path)
    else:
        check_final_simulation(path, case)

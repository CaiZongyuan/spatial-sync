"""Contact position-stage facts must match the captured post-step pose."""

import os
from types import SimpleNamespace

import numpy as np
import pytest

newton = pytest.importorskip("newton")
wp = pytest.importorskip("warp")

from newton.solvers import SolverMuJoCo

from packages.programs.labware.contacts import contacts_for_bodies
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.scenes import SceneConfig
from packages.sim_runtime.simulation import NewtonSimulation

pytestmark = pytest.mark.newton


def moving_box(gap, velocity):
    def builder():
        result = newton.ModelBuilder(up_axis=newton.Axis.Z, gravity=(0, 0, 0))
        SolverMuJoCo.register_custom_attributes(result)
        result.add_shape_box(-1, hx=.1, hy=.1, hz=.1, label="/World/Wall")
        body = result.add_body(xform=wp.transform((.2 + gap, 0, 0), wp.quat_identity()), label="/World/Box")
        result.add_shape_box(body, hx=.1, hy=.1, hz=.1, label="/World/Box/Collider")
        # Authored initial velocity; the real solver performs all subsequent motion.
        result.joint_qd[0] = velocity
        return result

    return SceneConfig(builder, solver="mujoco")


def observed(sim):
    manifest = export_manifest(sim, session_id="phase", scene_revision="r", revision_numeric_id=1)
    return PhysicsPort(sim, manifest)


def test_sparse_contact_selection_decodes_only_matching_rows(monkeypatch):
    """Pure array fixture: no solver/model construction or native stepping."""
    import mujoco

    from packages.sim_runtime import physics_port

    distances = np.r_[[-.0015, -.0005, 0], np.full(4096, .001)]
    geometries = np.tile([0, 1], (len(distances), 1))
    contacts = SimpleNamespace(dist=distances, geom=geometries)

    class ContactArray:
        dist, geom = contacts.dist, contacts.geom

        def __getitem__(self, index):
            return SimpleNamespace(dist=self.dist[index], geom=self.geom[index])

    def array(value):
        return SimpleNamespace(numpy=lambda: np.asarray(value))

    native = SimpleNamespace(nbody=2, ngeom=2, nflex=0, nplugin=0, body_mocapid=np.array([-1, -1]))
    model = SimpleNamespace(world_count=1, shape_count=2, shape_body=array([-1, 0]),
                            shape_label=["wall", "box"], device=SimpleNamespace(is_cpu=True))
    solver = SimpleNamespace(mj_model=native, mjc_geom_to_newton_shape=array([[0, 1]]),
                             mjc_body_to_newton=array([[-1, 0]]), _update_mjc_data=lambda *args: None)
    port = object.__new__(PhysicsPort)
    port.sim = SimpleNamespace(model=model, solver=solver, solver_kind="mujoco")
    port._contact_binding = (
        model, solver, native, solver.mjc_geom_to_newton_shape, solver.mjc_body_to_newton,
    )
    port._contact_cache = port._contact_error = None
    port.q = port.qd = np.zeros(0, dtype=np.float32)
    port.last_time = 0
    port.frame_body = np.array([[.2, 0, 0, 0, 0, 0, 1]])
    port._contact_data = SimpleNamespace(contact=ContactArray(), ncon=len(distances),
                                         xpos=np.array([[0, 0, 0], [.2, 0, 0]]),
                                         xquat=np.array([[1, 0, 0, 0], [1, 0, 0, 0]]))
    port._contact_state = SimpleNamespace(joint_q=SimpleNamespace(assign=lambda value: None),
                                          joint_qd=SimpleNamespace(assign=lambda value: None))
    monkeypatch.setattr(mujoco, "mj_fwdPosition", lambda *args: None)
    monkeypatch.setattr(mujoco, "mj_kinematics", lambda *args: None)
    monkeypatch.setattr(mujoco, "mj_comPos", lambda *args: None)
    monkeypatch.setattr(mujoco, "mj_collision", lambda *args: None)
    decoded = []
    real_contact = physics_port.PhysicalContact

    def count(*args):
        decoded.append(args)
        return real_contact(*args)

    monkeypatch.setattr(physics_port, "PhysicalContact", count)
    assert port.collision_contacts(max_distance=-.002) == []
    assert len(decoded) == 0, "Unselected positive margins must never become Python contact objects"
    rows = port.collision_contacts()
    assert [row.distance_m for row in rows] == distances[:3].tolist()
    assert all(row.bodies == (-1, 0) and row.shapes == ("wall", "box") for row in rows)
    assert len(decoded) == 3
    assert len(port.collision_contacts(max_distance=-.001)) == 1 and len(decoded) == 3
    assert len(port.collision_contacts(max_distance=.002)) == len(distances)
    assert len(decoded) == len(distances)
    # Even an unselected positive row must retain the existing finite check.
    distances[-1] = np.nan
    port._contact_cache = None
    with pytest.raises(ValueError, match="distance is unknown"):
        port.collision_contacts(max_distance=-.002)


@pytest.mark.parametrize("gap,velocity", [(.0001, -.2), (-.0001, .2)])
def test_current_box_contact_matches_post_step_pose_in_both_directions(gap, velocity):
    with NewtonSimulation("phase", device="cpu", physics_dt=.01, config=moving_box(gap, velocity)) as sim:
        port = observed(sim)
        sim.step(1)
        port.capture()
        distance = float(port.body_pose("/World/Box")[0][0]) - .2
        rows = port.collision_contacts()
        if velocity < 0:
            assert distance < -.001  # The existing safety gate really is exceeded.
            assert rows, "New current penetration was absent from the previous position-stage cache"
            assert all(row.distance_m == pytest.approx(distance, abs=1e-7) for row in rows)
            assert all(set(row.shapes) == {"/World/Wall", "/World/Box/Collider"} for row in rows)
            assert contacts_for_bodies(port, {port.bind_body("/World/Box")})
        else:
            assert distance > .001
            assert rows == [], "Old contact must not qualify a grasp at a now-separated pose"


def live_buffers(sim):
    """Defined physical buffers, excluding inactive MuJoCo arena workspace views."""
    result = {}
    for label, owner in (("state", sim.state), ("next", sim.next_state), ("control", sim.control),
                         ("model", sim.model), ("solver", sim.solver),
                         ("model.mujoco", getattr(sim.model, "mujoco", None)),
                         ("solver.shadow_model", getattr(sim.solver, "mjw_model", None)),
                         ("solver.shadow_data", getattr(sim.solver, "mjw_data", None))):
        if owner is None:
            continue
        for name, value in vars(owner).items():
            if callable(getattr(value, "numpy", None)):
                result[label + "." + name] = value.numpy().tobytes()
            elif isinstance(value, np.ndarray):
                result[label + "." + name] = value.tobytes()
    data = sim.solver.mj_data
    for name in ("qpos", "qvel", "qacc", "qacc_warmstart", "ctrl", "act", "act_dot", "eq_active",
                 "qfrc_applied", "xfrc_applied", "mocap_pos", "mocap_quat", "xpos", "xquat", "geom_xpos",
                 "geom_xmat", "efc_force", "sensordata"):
        result["native." + name] = getattr(data, name).tobytes()
    for name in ("qM", "qLD", "M", "D"):
        if hasattr(data, name):
            result["native." + name] = getattr(data, name).tobytes()
    for name in ("geom", "dist", "pos", "frame", "friction"):
        result["contacts." + name] = np.asarray(getattr(data.contact, name)[:data.ncon]).tobytes()
    result["native.counts"] = (data.time, data.ncon, data.nefc)
    for name in dir(sim.solver.mj_model):
        if not name.startswith("_"):
            value = getattr(sim.solver.mj_model, name)
            if isinstance(value, np.ndarray):
                result["native_model." + name] = value.tobytes()
    return result


def test_query_preserves_live_solver_staging_controls_contacts_and_state():
    with NewtonSimulation("phase", device="cpu", physics_dt=.01, config=moving_box(.0001, -.2)) as sim:
        port = observed(sim)
        sim.step(1)
        port.capture()
        before = live_buffers(sim)
        assert port.collision_contacts()
        assert port.collision_contacts(max_distance=-.01) == []
        assert live_buffers(sim) == before


@pytest.mark.parametrize("gap,velocity", [(.0001, -.2), (-.0001, .2)])
def test_queries_do_not_change_subsequent_real_dynamics(gap, velocity):
    trajectories = []
    for query in (False, True):
        with NewtonSimulation("phase", device="cpu", physics_dt=.01, config=moving_box(gap, velocity)) as sim:
            port = observed(sim) if query else None
            rows = []
            for _ in range(12):
                sim.step(1)
                if query:
                    port.capture()
                    before = live_buffers(sim)
                    port.collision_contacts()
                    port.collision_contacts(max_distance=-.001)
                    assert live_buffers(sim) == before
                # Compare future physical outcomes, not allocations/model pointer
                # identities belonging to two separately constructed solvers.
                rows.append({key: value for key, value in live_buffers(sim).items()
                             if key.startswith(("state.", "next.", "control.", "native.", "contacts."))})
            trajectories.append(rows)
    assert trajectories[0] == trajectories[1]


def test_initial_reset_and_same_time_getters_use_one_query_per_actual_capture(monkeypatch):
    import mujoco

    collisions = []
    stage = ("mj_collision" if os.getenv("SPATIAL_NEWTON_CONTACT_COLLISION_ONLY") == "1"
             else "mj_fwdPosition")
    collision = getattr(mujoco, stage)

    def observe(model, data):
        collisions.append(id(data))
        return collision(model, data)

    with NewtonSimulation("phase", device="cpu", physics_dt=.01, config=moving_box(-.0001, .2)) as sim:
        port = observed(sim)
        monkeypatch.setattr(mujoco, stage, observe)
        initial = port.collision_contacts()
        assert initial and len(collisions) == 1
        assert port.collision_contacts(max_distance=-.001) == []
        port.capture()  # Paused/same-time RPC capture cannot trigger another collision traversal.
        assert port.collision_contacts() == initial and len(collisions) == 1
        assert port._contact_data.time == port.last_time == 0
        frozen = port.body.copy()
        sim.step(2)  # Both live Newton buffers are reused, without a new Port capture.
        np.testing.assert_array_equal(port.body, frozen)
        assert port.collision_contacts() == initial and len(collisions) == 1
        port.capture()
        assert port.collision_contacts() == [] and len(collisions) == 2
        assert port._contact_data.time == port.last_time
        sim.reset()
        port.reset()
        before = live_buffers(sim)
        assert port.collision_contacts() == initial and len(collisions) == 3
        assert port._contact_data.time == port.last_time == 0
        assert live_buffers(sim) == before
        assert len(set(collisions)) == 1  # One bounded private MjData, reused across epochs.


def test_unknown_mapping_model_and_pose_never_return_empty_safe_contacts(monkeypatch):
    with NewtonSimulation("phase", device="cpu", config=moving_box(.0001, 0)) as sim:
        port = observed(sim)
        with monkeypatch.context() as patch:
            patch.setattr(sim.solver, "mjc_geom_to_newton_shape", None)
            with pytest.raises(ValueError, match="identity|mapping"):
                port.collision_contacts()
        with monkeypatch.context() as patch:
            patch.setattr(sim.solver, "mj_model", object())
            with pytest.raises(ValueError, match="identity"):
                port.collision_contacts()
        # A malformed captured snapshot must not silently certify unrelated geometry.
        port.frame_body[0, 0] += .01
        before = live_buffers(sim)
        with pytest.raises(ValueError, match="pose does not match"):
            port.collision_contacts()
        with pytest.raises(ValueError, match="pose does not match"):
            port.collision_contacts()
        assert live_buffers(sim) == before


def test_nonzero_com_and_active_contact_qualified_weld_remain_unchanged():
    from packages.sim_runtime.welds import declare_weld_pairs

    def builder():
        result = newton.ModelBuilder(up_axis=newton.Axis.Z, gravity=(0, 0, 0))
        SolverMuJoCo.register_custom_attributes(result)
        wall = result.add_link(label="/World/WallBody")
        fixed = result.add_joint_fixed(-1, wall, label="/World/Mount")
        result.add_articulation([fixed])
        result.add_shape_box(wall, hx=.1, hy=.1, hz=.1, label="/World/WallBody/Shape")
        body = result.add_body(
            xform=wp.transform((.1999, 0, 0), wp.quat_identity()), label="/World/Box",
            com=(.03, .01, 0), mass=1, inertia=wp.mat33(.01, 0, 0, 0, .01, 0, 0, 0, .01), lock_inertia=True,
        )
        result.add_shape_box(body, hx=.1, hy=.1, hz=.1, label="/World/Box/Collider")
        declare_weld_pairs(result, [(wall, body)])
        return result

    with NewtonSimulation("phase", device="cpu", config=SceneConfig(builder, solver="mujoco")) as sim:
        port = observed(sim)
        wall, body = port.bind_body("/World/WallBody"), port.bind_body("/World/Box")
        assert not port.welds.active(wall, body)
        assert port.collision_contacts()  # Conditional test weld only after actual geometric contact.
        port.weld(wall, body)
        port.commit()
        assert sim.solver.mj_data.eq_active.any()
        sim.step(1)
        port.capture()
        before = live_buffers(sim)
        port.collision_contacts()
        assert live_buffers(sim) == before
        assert port.welds.active(wall, body)

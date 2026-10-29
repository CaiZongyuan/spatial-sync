"""Indexed CPU snapshot and one buffered actuator commit per control tick."""

import os
from copy import copy
from dataclasses import dataclass
from itertools import product
from numbers import Integral
from time import monotonic
from types import SimpleNamespace

import numpy as np
import warp as wp

from packages.programs.tube.facts import site_relative_velocities
from packages.protocol.math import compose_pose, rotate_vector

from .welds import Welds


@dataclass(frozen=True)
class JointRef:
    q: slice
    qd: slice
    target: slice
    child: int = -1


@dataclass(frozen=True)
class SiteRef:
    body: int
    local: dict


@dataclass(frozen=True)
class PhysicalContact:
    bodies: tuple[int, int]
    shapes: tuple[str, str]
    distance_m: float


def _scope_masks(contype, affinity, interested):
    """Encode the original pair truth table using only interested classes."""
    ct, af = np.asarray(contype, dtype=np.uint32), np.asarray(affinity, dtype=np.uint32)
    classes = np.unique(np.stack((ct[interested], af[interested]), axis=1), axis=0)
    if len(classes) > 32:
        return None
    scoped_ct, scoped_af = np.zeros_like(ct), np.zeros_like(af)
    for index, (kind, accepts) in enumerate(classes):
        bit = np.uint32(1) << np.uint32(index)
        allowed = ((kind & af) | (ct & accepts)) != 0
        scoped_af[allowed] |= bit
        scoped_ct[interested & (ct == kind) & (af == accepts)] = bit
    return scoped_ct.view(np.int32), scoped_af.view(np.int32)


class PhysicsPort:
    def __init__(self, simulation, manifest):
        self.sim = simulation
        model = simulation.model
        self.bodies = {"/" + label.strip("/"): i for i, label in enumerate(model.body_label)}
        self.body_keys = {int(node.debug["newtonBodyIndex"]): node.entityKey
                          for node in manifest.nodes if node.kind == "body"}
        q, qd = model.joint_q_start.numpy(), model.joint_qd_start.numpy()
        targets = model.joint_target_q_start.numpy()
        children = model.joint_child.numpy()
        self.joints = {
            "/" + label.strip("/"): JointRef(
                slice(int(q[i]), int(q[i + 1])),
                slice(int(qd[i]), int(qd[i + 1])),
                slice(int(targets[i]), int(targets[i + 1])),
                int(children[i]),
            )
            for i, label in enumerate(model.joint_label)
        }
        self._joint_limit_lower = model.joint_limit_lower.numpy().copy()
        self._joint_limit_upper = model.joint_limit_upper.numpy().copy()
        self.sites = {
            site.entityKey: SiteRef(
                self.bodies[site.parentBodyKey] if site.parentBodyKey else -1, site.localPose.model_dump()
            )
            for site in manifest.sites
        }
        self._site_groups = {}
        self._site_motion_history = {}
        self._collision_bounds = {}
        self._contact_binding = (
            model, simulation.solver, getattr(simulation.solver, "mj_model", None),
            getattr(simulation.solver, "mjc_geom_to_newton_shape", None),
            getattr(simulation.solver, "mjc_body_to_newton", None),
        )
        self._contact_data = self._contact_state = None
        self._contact_cache = self._contact_error = None
        self._contact_scope_cache, self._contact_scope_errors = {}, {}
        self._contact_scope_model = None
        self._targets = tuple(
            wp.clone(array, device="cpu", pinned=model.device.is_cuda)
            for array in (simulation.control.joint_target_q, simulation.control.joint_target_qd)
        )
        self.target_q, self.target_qd = (array.numpy() for array in self._targets)
        self._staging = (
            tuple(
                wp.empty_like(array, device="cpu", pinned=True)
                for array in (simulation.state.body_q, simulation.state.joint_q, simulation.state.joint_qd)
            )
            if model.device.is_cuda
            else None
        )
        self.welds = Welds(model, simulation.solver, getattr(simulation.builder, "spatial_weld_pairs", {}))
        self.last_time = None
        self.body = None
        self.releasing = set()
        self.capture()

    def bind_body(self, key):
        return self.bodies[key]

    def collision_contacts(self, *, max_distance=0.0, bodies=None):
        """Captured-pose CPU contacts, mapped through Newton's shape identity.

        MuJoCo sanitizes body names and includes separated pairs inside contact
        margins. Neither is a physical contact oracle. Unknown backends fail
        closed rather than returning an apparently empty contact list. One
        private position-stage query is shared by all reads of this capture.
        """
        solver = self.sim.solver
        if self.sim.solver_kind != "mujoco" or not self.sim.model.device.is_cpu:
            raise ValueError("Physical contact queries require the CPU MuJoCo backend")
        if not np.isfinite(max_distance):
            raise ValueError("Contact distance bound must be finite")
        binding = (self.sim.model, solver, getattr(solver, "mj_model", None),
                   getattr(solver, "mjc_geom_to_newton_shape", None),
                   getattr(solver, "mjc_body_to_newton", None))
        if any(current is not captured
               for current, captured in zip(binding, self._contact_binding, strict=True)):
            raise ValueError("Contact model identity changed; a new PhysicsPort is required")
        if solver.mjc_geom_to_newton_shape is None or solver.mjc_body_to_newton is None:
            raise ValueError("Solver contact identity mapping is unavailable")
        scope = None
        if bodies is not None:
            if (not bodies or any(isinstance(body, bool) or not isinstance(body, Integral)
                                  or not -1 <= body < len(self.frame_body) for body in bodies)):
                raise ValueError("Contact body scope is unknown")
            scope = tuple(sorted(set(int(body) for body in bodies)))
        scoped = scope is not None and os.getenv("SPATIAL_NEWTON_CONTACT_SCOPED") == "1"
        if scoped and scope not in self._contact_scope_cache:
            if scope in self._contact_scope_errors:
                raise ValueError(self._contact_scope_errors[scope])
            native = self._scope_model(scope)
            if native is None:
                scoped = False  # Unsupported encoding/callback: keep the original full query.
            else:
                try:
                    self._contact_scope_cache[scope] = self._current_contacts(native=native)
                except (AttributeError, AssertionError, RuntimeError, TypeError, ValueError) as error:
                    self._contact_scope_errors[scope] = f"Scoped contact facts are unavailable: {error}"
                    raise ValueError(self._contact_scope_errors[scope]) from error
        if scoped:
            snapshot = self._contact_scope_cache[scope]
        else:
            snapshot = self._full_contacts()
        selected = np.flatnonzero(snapshot.distances <= max_distance)
        if scope is not None and len(selected):
            owners = snapshot.shape_bodies[snapshot.mapping[snapshot.geometries[selected]]]
            selected = selected[np.any(np.isin(owners, scope), axis=1)]
        return self._decode_contacts(snapshot, selected)

    def _full_contacts(self):
        if self._contact_error is not None:
            raise ValueError(self._contact_error)
        if self._contact_cache is None:
            try:
                self._contact_cache = self._current_contacts()
            except (AttributeError, AssertionError, RuntimeError, TypeError, ValueError) as error:
                self._contact_error = f"Captured-pose contact facts are unavailable: {error}"
                raise ValueError(self._contact_error) from error
        return self._contact_cache

    def _scope_model(self, bodies):
        import mujoco

        native = self.sim.solver.mj_model
        if native.nflex or native.nplugin or mujoco.get_mjcb_contactfilter() is not None:
            return None
        mapping = self.sim.solver.mjc_geom_to_newton_shape.numpy()
        if (mapping.shape != (1, native.ngeom) or np.any(mapping < 0)
                or np.any(mapping >= self.sim.model.shape_count)):
            return None
        owners = self.sim.model.shape_body.numpy()[mapping[0]]
        masks = _scope_masks(native.geom_contype, native.geom_conaffinity, np.isin(owners, bodies))
        if masks is None:
            return None
        if self._contact_scope_model is None:
            self._contact_scope_model = copy(native)
        query_model = self._contact_scope_model
        query_model.geom_contype[:], query_model.geom_conaffinity[:] = masks
        query_model.body_contype[:] = 0
        query_model.body_conaffinity[:] = 0
        np.bitwise_or.at(query_model.body_contype, query_model.geom_bodyid, query_model.geom_contype)
        np.bitwise_or.at(query_model.body_conaffinity, query_model.geom_bodyid, query_model.geom_conaffinity)
        return query_model

    @staticmethod
    def _decode_contacts(snapshot, selected):
        result = []
        # Positive margin pairs dominate large scenes. Select compact rows
        # before allocating Python objects or resolving shape/body identities.
        for index in selected:
            index = int(index)
            row = snapshot.decoded.get(index)
            if row is None:
                shapes = snapshot.mapping[snapshot.geometries[index]]
                row = PhysicalContact(
                    tuple(int(snapshot.shape_bodies[shape]) for shape in shapes),
                    tuple(snapshot.shape_labels[shape] for shape in shapes),
                    float(snapshot.distances[index]),
                )
                snapshot.decoded[index] = row
            result.append(row)
        return result

    def _current_contacts(self, *, native=None):
        import mujoco

        solver, model = self.sim.solver, self.sim.model
        native = solver.mj_model if native is None else native
        if model.world_count != 1 or native.nflex or native.nplugin:
            raise ValueError("Contact snapshots require one rigid CPU world without plugins")
        geometry_map = solver.mjc_geom_to_newton_shape.numpy()
        if geometry_map.shape != (1, native.ngeom):
            raise ValueError("Solver contact geometry mapping is unavailable")
        body_map = solver.mjc_body_to_newton.numpy()
        if (body_map.shape != (1, native.nbody) or np.any(body_map < -1)
                or np.any(body_map >= len(self.frame_body))):
            raise ValueError("Solver contact body mapping is unavailable")
        if (not np.isfinite(self.frame_body).all() or not np.isfinite(self.q).all()
                or not np.isfinite(self.qd).all() or not np.isfinite(self.last_time)):
            raise ValueError("Captured pose, coordinates and time must be finite")
        if self._contact_data is None:
            self._contact_data = mujoco.MjData(solver.mj_model)
            self._contact_state = SimpleNamespace(
                joint_q=wp.array(self.q, dtype=wp.float32, device=model.device),
                joint_qd=wp.array(self.qd, dtype=wp.float32, device=model.device),
            )
        else:
            self._contact_state.joint_q.assign(self.q)
            self._contact_state.joint_qd.assign(self.qd)
        query = self._contact_data
        query.time = self.last_time
        # Pinned Newton's existing coordinate/COM conversion writes only this
        # private MjData on CPU. In particular reset need not have synced LIVE
        # qpos yet, so copying the solver's current qpos would be incorrect.
        solver._update_mjc_data(query, model, self._contact_state)
        body_map = body_map[0]
        for body_id in np.flatnonzero(native.body_mocapid >= 0):
            body = int(body_map[body_id])
            if body < 0:
                raise ValueError("Mocap body has no captured Newton identity")
            mocap = int(native.body_mocapid[body_id])
            pose = self.frame_body[body]
            query.mocap_pos[mocap] = pose[:3]
            query.mocap_quat[mocap] = pose[[6, 3, 4, 5]]
        # Both paths update rigid geometry without integration/control/dynamics.
        # The owned opt-in omits unused constraint assembly; the supported full
        # position stage remains the default. All output stays private.
        if os.getenv("SPATIAL_NEWTON_CONTACT_COLLISION_ONLY") == "1":
            mujoco.mj_kinematics(native, query)
            mujoco.mj_comPos(native, query)
            mujoco.mj_collision(native, query)
        else:
            mujoco.mj_fwdPosition(native, query)
        mapped = np.flatnonzero(body_map >= 0)
        expected = self.frame_body[body_map[mapped]]
        actual_q = query.xquat[mapped][:, [1, 2, 3, 0]]
        quaternion_error = np.minimum(np.abs(actual_q - expected[:, 3:]).max(axis=1),
                                      np.abs(actual_q + expected[:, 3:]).max(axis=1))
        # A float32 FK representation check, not a physical contact allowance.
        roundoff = 32 * np.finfo(np.float32).eps
        if (not np.allclose(query.xpos[mapped], expected[:, :3], rtol=0, atol=roundoff)
                or np.any(quaternion_error > roundoff)):
            raise ValueError("Query pose does not match the captured Newton frame")
        mapping = geometry_map[0]
        shape_bodies = model.shape_body.numpy()
        distances = np.array(query.contact.dist[:query.ncon], copy=True)
        if not np.isfinite(distances).all():
            raise ValueError("Solver contact distance is unknown")
        geometries = np.array(query.contact.geom[:query.ncon], copy=True)
        if (geometries.shape != (query.ncon, 2) or np.any(geometries < 0)
                or np.any(geometries >= len(mapping))):
            raise ValueError("Solver contact geometry identity is unknown")
        shapes = mapping[geometries]
        if np.any(shapes < 0) or np.any(shapes >= model.shape_count):
            raise ValueError("Solver contact has no Newton shape identity")
        return SimpleNamespace(
            distances=distances, geometries=geometries, mapping=mapping.copy(),
            shape_bodies=shape_bodies.copy(), shape_labels=tuple(model.shape_label), decoded={},
        )

    def preview_snapshot(self):
        from .kinematic_snapshot import KinematicSnapshot

        return KinematicSnapshot(self)

    def bind_joint(self, key):
        return self.joints[key]

    def joint_limits(self, joint):
        """Return copied physical DOF limits; nq and nv need not have equal offsets."""
        ref = self.bind_joint(joint) if isinstance(joint, str) else joint
        if not isinstance(ref, JointRef) or ref not in self.joints.values():
            raise KeyError("Unknown physical joint reference")
        return self._joint_limit_lower[ref.qd].copy(), self._joint_limit_upper[ref.qd].copy()

    def bind_site(self, key):
        return self.sites[key]

    def bind_sites(self, keys):
        token = tuple(keys)
        if token not in self._site_groups:
            self._site_groups[token] = tuple(self.sites[key] for key in keys)
        return self._site_groups[token]

    def collision_bounds(self, body):
        """Immutable collision-only AABB in body coordinates, in metres.

        Newton's scaled shape-local bounds include mesh vertices and analytic
        primitive support. Sites and visual-only shapes cannot enlarge them.
        """
        if body not in self._collision_bounds:
            import newton

            model = self.sim.model
            owners, flags = model.shape_body.numpy(), model.shape_flags.numpy()
            lower = model.shape_collision_aabb_lower.numpy()
            upper = model.shape_collision_aabb_upper.numpy()
            transforms = model.shape_transform.numpy()
            points = []
            for index in np.flatnonzero(owners == body):
                if not int(flags[index]) & int(newton.ShapeFlags.COLLIDE_SHAPES):
                    continue
                transform = wp.transform(transforms[index, :3], transforms[index, 3:])
                points.extend(wp.transform_point(transform, wp.vec3(*point)) for point in product(
                    *zip(lower[index], upper[index], strict=True)))
            if not points or not np.isfinite(points).all():
                raise ValueError("Body collision bounds are unavailable or nonfinite")
            bounds = np.array([np.min(points, axis=0), np.max(points, axis=0)], dtype=float)
            bounds.setflags(write=False)
            self._collision_bounds[body] = bounds
        return self._collision_bounds[body]

    def capture(self):
        # Own the actual capture: the CPU solver reuses its two state buffers.
        # Neither a later physics step nor LIVE mj_data may change this frame.
        previous = getattr(self, "previous_positions", None)
        old_body, old_q, old_qd = (getattr(self, key, None) for key in ("frame_body", "q", "qd"))
        arrays = self.sim.state.body_q, self.sim.state.joint_q, self.sim.state.joint_qd
        if self._staging:
            with wp.ScopedDevice(self.sim.model.device):
                for target, source in zip(self._staging, arrays, strict=True):
                    wp.copy(target, source)
                wp.synchronize_device(self.sim.model.device)
            arrays = self._staging
        self.body, self.q, self.qd = (array.numpy().copy() for array in arrays)
        self.frame_body = self.body.copy()
        self.sample_time_ms = monotonic()*1000
        dt = self.sim.sim_time - self.last_time if self.last_time is not None else 0
        same_frame = previous is not None and self.last_time == self.sim.sim_time
        epoch = getattr(self.sim, "epoch", None)
        if (not same_frame or getattr(self, "_contact_epoch", None) != epoch
                or not np.array_equal(old_body, self.frame_body) or not np.array_equal(old_q, self.q)
                or not np.array_equal(old_qd, self.qd)):
            self._contact_cache = self._contact_error = None
            self._contact_scope_cache, self._contact_scope_errors = {}, {}
        self._contact_epoch = epoch
        self.velocity = (
            (self.body[:, :3] - previous) / dt
            if previous is not None and dt > 0
            else self.velocity if same_frame else np.zeros((len(self.body), 3))
        )
        self.previous_positions = self.body[:, :3].copy()
        self.last_time = self.sim.sim_time
        self._site_cache = {}
        self._site_pose_groups = {}
        if not same_frame:
            self._site_velocity_groups = {}

    def joint_q(self, key):
        return self.q[(self.joints[key] if isinstance(key, str) else key).q]

    def joint_qd(self, key):
        return self.qd[(self.joints[key] if isinstance(key, str) else key).qd]

    def body_pose(self, key):
        pose = self.body[self.bodies[key] if isinstance(key, str) else key]
        return pose[:3], pose[3:]

    def site_pose(self, key):
        site = self.sites[key] if isinstance(key, str) else key
        cached = self._site_cache.get(id(site))
        if cached is not None:
            return cached
        if site.body < 0:
            result = np.array(site.local["p"]), np.array(site.local["q"])
        else:
            pose = self.body[site.body]
            world = compose_pose({"p": pose[:3], "q": pose[3:]}, site.local)
            result = np.array(world["p"]), np.array(world["q"])
        self._site_cache[id(site)] = result
        return result

    def set_joint_target(self, key, values):
        self.target_q[(self.joints[key] if isinstance(key, str) else key).target] = values

    def site_poses(self, sites):
        token = id(sites)
        if token not in self._site_pose_groups:
            self._site_pose_groups[token] = np.array([np.concatenate(self.site_pose(site)) for site in sites])
        return self._site_pose_groups[token]

    def relative_site_velocity(self, body, sites):
        token = id(sites)
        if token not in self._site_velocity_groups:
            poses = self.site_poses(sites)
            previous = self._site_motion_history.get(token)
            if previous is None:
                velocity = np.broadcast_to(self.velocity, (len(sites), *self.velocity.shape))
            else:
                bodies_before, sites_before, sampled_at = previous
                velocity = site_relative_velocities(
                    bodies_before, self.body, sites_before, poses, self.last_time - sampled_at
                )
            self._site_velocity_groups[token] = velocity
            self._site_motion_history[token] = self.body.copy(), poses.copy(), self.last_time
        return self._site_velocity_groups[token][:, body, :]

    def set_joint_target_velocity(self, key, values):
        self.target_qd[(self.joints[key] if isinstance(key, str) else key).qd] = values

    def weld(self, a_body, b_body):
        a = self.bodies[a_body] if isinstance(a_body, str) else a_body
        b = self.bodies[b_body] if isinstance(b_body, str) else b_body
        self.welds.set(a, b, True, self.body)

    def unweld(self, a_body, b_body):
        a = self.bodies[a_body] if isinstance(a_body, str) else a_body
        b = self.bodies[b_body] if isinstance(b_body, str) else b_body
        self.welds.set(a, b, False, self.body)

    def local_position(self, body, position):
        p, q = self.body_pose(body)
        return np.array(rotate_vector([-q[0], -q[1], -q[2], q[3]], np.asarray(position) - p))

    def commit(self):
        with wp.ScopedDevice(self.sim.model.device):
            wp.copy(self.sim.control.joint_target_q, self._targets[0])
            wp.copy(self.sim.control.joint_target_qd, self._targets[1])
        self.welds.flush()

    def reset(self):
        self.welds.reset()
        self.target_q[:] = self.sim.control.joint_target_q.numpy()
        self.target_qd[:] = self.sim.control.joint_target_qd.numpy()
        self.last_time, self.body = None, None
        self.previous_positions = None
        self._site_motion_history.clear()
        self.releasing.clear()
        self.capture()

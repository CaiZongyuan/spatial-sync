"""Indexed CPU snapshot and one buffered actuator commit per control tick."""

from dataclasses import dataclass
from time import monotonic

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


@dataclass(frozen=True)
class SiteRef:
    body: int
    local: dict


class PhysicsPort:
    def __init__(self, simulation, manifest):
        self.sim = simulation
        model = simulation.model
        self.bodies = {"/" + label.strip("/"): i for i, label in enumerate(model.body_label)}
        q, qd = model.joint_q_start.numpy(), model.joint_qd_start.numpy()
        targets = model.joint_target_q_start.numpy()
        self.joints = {
            "/" + label.strip("/"): JointRef(
                slice(int(q[i]), int(q[i + 1])),
                slice(int(qd[i]), int(qd[i + 1])),
                slice(int(targets[i]), int(targets[i + 1])),
            )
            for i, label in enumerate(model.joint_label)
        }
        self.sites = {
            site.entityKey: SiteRef(
                self.bodies[site.parentBodyKey] if site.parentBodyKey else -1, site.localPose.model_dump()
            )
            for site in manifest.sites
        }
        self._site_groups = {}
        self._site_motion_history = {}
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

    def bind_joint(self, key):
        return self.joints[key]

    def bind_site(self, key):
        return self.sites[key]

    def bind_sites(self, keys):
        token = tuple(keys)
        if token not in self._site_groups:
            self._site_groups[token] = tuple(self.sites[key] for key in keys)
        return self._site_groups[token]

    def capture(self):
        # These are the only state transfers programs consume. On CPU .numpy()
        # is a view; the previous positions alone are retained for linear speed.
        previous = getattr(self, "previous_positions", None)
        arrays = self.sim.state.body_q, self.sim.state.joint_q, self.sim.state.joint_qd
        if self._staging:
            with wp.ScopedDevice(self.sim.model.device):
                for target, source in zip(self._staging, arrays, strict=True):
                    wp.copy(target, source)
                wp.synchronize_device(self.sim.model.device)
            arrays = self._staging
        self.body, self.q, self.qd = (array.numpy() for array in arrays)
        self.frame_body = self.body.copy()
        self.sample_time_ms = monotonic()*1000
        dt = self.sim.sim_time - self.last_time if self.last_time is not None else 0
        self.velocity = (
            (self.body[:, :3] - previous) / dt
            if previous is not None and dt > 0
            else np.zeros((len(self.body), 3))
        )
        self.previous_positions = self.body[:, :3].copy()
        self.last_time = self.sim.sim_time
        self._site_cache = {}
        self._site_pose_groups = {}
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

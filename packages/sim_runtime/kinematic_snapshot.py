"""Evaluate actual Newton FK into private buffers without touching live physics."""

import hashlib
from copy import deepcopy

import numpy as np

from packages.protocol.math import compose_pose


class KinematicSnapshot:
    def __init__(self, port):
        import newton
        import warp as wp

        self._model = port.sim.model
        self._sites = deepcopy(port.sites)
        self._body_keys = dict(port.body_keys)
        state = port.sim.state
        self._body = state.body_q.numpy().copy()
        if port.sim.solver_kind == "xpbd":
            with wp.ScopedDevice(self._model.device):
                q, qd = wp.clone(state.joint_q), wp.clone(state.joint_qd)
                newton.eval_ik(self._model, state, q, qd)
            self._q, self._qd = q.numpy().copy(), qd.numpy().copy()
        else:
            self._q, self._qd = state.joint_q.numpy().copy(), state.joint_qd.numpy().copy()
        self._starts = self._model.joint_q_start.numpy().copy()
        self._dstarts = self._model.joint_qd_start.numpy().copy()
        self._articulations = self._model.joint_articulation.numpy().copy()
        self._children = self._model.joint_child.numpy().copy()
        self._lower = self._model.joint_limit_lower.numpy().copy()
        self._upper = self._model.joint_limit_upper.numpy().copy()
        self._joints = {}
        for index, label in enumerate(self._model.joint_label):
            self._joints.setdefault("/" + label.strip("/"), []).append(index)
        self._bindings = {}
        self._state = None
        self.fingerprint = hashlib.sha256(self._q.tobytes() + self._body.tobytes()).digest()

    def _indices(self, keys):
        token = tuple(keys)
        if token not in self._bindings:
            result = []
            for key in keys:
                matches = self._joints.get(key, [])
                if len(matches) != 1:
                    raise ValueError("Preview joint identity is missing or ambiguous: " + key)
                index = matches[0]
                if (self._starts[index + 1] - self._starts[index] != 1
                        or self._dstarts[index + 1] - self._dstarts[index] != 1
                        or self._articulations[index] < 0):
                    raise ValueError("Preview requires scalar joints in Newton forward kinematics: " + key)
                result.append(index)
            self._bindings[token] = result
        return self._bindings[token]

    def positions(self, keys):
        return [float(self._q[self._starts[index]]) for index in self._indices(keys)]

    def validate_targets(self, keys, targets):
        indices = self._indices(keys)
        for target in targets:
            if len(target) != len(indices) or not np.isfinite(target).all():
                raise ValueError("Preview target must match the finite configured joint coordinates")
            for index, value in zip(indices, target, strict=True):
                dof = self._dstarts[index]
                if not self._lower[dof] <= value <= self._upper[dof]:
                    raise ValueError("Preview target exceeds actual joint limits")

    def _site_pose(self, key, bodies):
        site = self._sites[key]
        if site.body < 0:
            return deepcopy(site.local)
        body = bodies[site.body]
        return compose_pose({"p": body[:3], "q": body[3:]}, site.local)

    def forward(self, keys, positions, site):
        import newton
        import warp as wp

        indices = self._indices(keys)
        if len(positions) != len(indices) or not np.isfinite(positions).all():
            raise ValueError("Preview FK requires finite joint coordinates of the configured size")
        q = self._q.copy()
        for index, value in zip(indices, positions, strict=True):
            q[self._starts[index]] = value
        with wp.ScopedDevice(self._model.device):
            if self._state is None:
                self._state = self._model.state()
            self._state.body_q.assign(self._body)
            self._state.joint_q.assign(q)
            self._state.joint_qd.assign(self._qd)
            newton.eval_fk(self._model, self._state.joint_q, self._state.joint_qd, self._state)
        return self._site_pose(site, self._state.body_q.numpy())

    def target_body_poses(self, keys):
        if self._state is None:
            raise RuntimeError("Target body poses require a completed FK sample")
        articulations = {int(self._articulations[index]) for index in self._indices(keys)}
        bodies = self._state.body_q.numpy()
        children = {int(child) for child, articulation in zip(
            self._children, self._articulations, strict=True)
                    if int(articulation) in articulations}
        return {self._body_keys[index]: {"p": bodies[index, :3].tolist(), "q": bodies[index, 3:].tolist()}
                for index in sorted(children) if index in self._body_keys}

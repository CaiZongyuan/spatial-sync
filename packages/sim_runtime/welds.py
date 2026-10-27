"""The only boundary to Newton 1.7's private MuJoCo equality API."""

import newton
import numpy as np
import warp as wp
from newton._src.solvers.mujoco.enums import EqType
from newton._src.solvers.mujoco.equality import _add_equality_constraint


def declare_welds(builder, parents, tubes):
    """Declare every possible pair disabled, before model finalization."""
    declare_weld_pairs(builder, ((parent, tube) for parent in parents for tube in tubes))


def declare_weld_pairs(builder, endpoints):
    """Declare the exact pairs resolved by the scene's existing programs."""
    pairs = {}
    for parent, tube in endpoints:
        pairs[parent, tube] = _add_equality_constraint(
            builder, EqType.WELD, body1=parent, body2=tube, enabled=False, label=f"weld:{parent}:{tube}",
            # Default soft equalities let a small offset load rotate tens of
            # degrees during a 60 rpm teaching run. Keep weld holds rigid at
            # the 240 Hz physics step; this is still a solver constraint.
            custom_attributes={"mujoco:eq_solref": [.0085, 1.0],
                               "mujoco:eq_solimp": [.99, .999, .001, .5, 2.0]},
        )
    builder.spatial_weld_pairs = pairs


class Welds:
    def __init__(self, model, solver, pairs):
        self.model, self.solver, self.pairs = model, solver, pairs
        if not pairs:
            self.enabled, self.relpose = np.zeros(0, dtype=bool), np.empty((0, 7))
            self.dirty = False
            return
        self.enabled = model.mujoco.equality_constraint_enabled.numpy().copy()
        self.relpose = model.mujoco.equality_constraint_relpose.numpy().copy()
        self.dirty = False

    def active(self, parent, tube):
        return bool(self.enabled[self.pairs[parent, tube]])

    def counts(self):
        return len(self.pairs), int(np.count_nonzero(self.enabled))

    def set(self, parent, tube, enabled, poses):
        index = self.pairs[parent, tube]
        if bool(self.enabled[index]) == enabled:
            return
        if enabled:
            a = wp.transform(tuple(poses[parent][:3]), tuple(poses[parent][3:]))
            b = wp.transform(tuple(poses[tube][:3]), tuple(poses[tube][3:]))
            self.relpose[index] = np.asarray(wp.transform_multiply(wp.transform_inverse(a), b))
        self.enabled[index] = enabled
        self.dirty = True

    def flush(self):
        if self.dirty:
            if self.pairs:
                self.model.mujoco.equality_constraint_enabled.assign(self.enabled)
                self.model.mujoco.equality_constraint_relpose.assign(self.relpose)
                self.solver.notify_model_changed(newton.ModelFlags.CONSTRAINT_PROPERTIES)
            self.dirty = False

    def reset(self):
        self.enabled[:] = False
        self.dirty = True
        self.flush()

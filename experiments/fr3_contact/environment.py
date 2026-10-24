"""Experiment-owned manager environment; all backend packages remain untouched."""

import json

from isaaclab.envs import ManagerBasedRLEnv
from isaaclab_newton.physics import NewtonManager

from . import physics_validity


class FR3ContactEnv(ManagerBasedRLEnv):
    def __init__(self, *args, **kwargs):
        physics_validity.install()
        super().__init__(*args, **kwargs)

    def load_managers(self):
        self._fr3_validity = NewtonManager._solver._fr3_episode_observer.validity
        super().load_managers()

    def step(self, action):
        self._fr3_validity.transition_outputs.zero_()
        history = getattr(self, "_fr3_placement_progress", None)
        if history is not None and self.cfg.terminations is not None:
            self._fr3_validity.lifted.copy_(history.lifted)
        result = super().step(action)
        outputs = self._fr3_validity.transition_outputs
        expected = self.cfg.decimation * NewtonManager._num_substeps
        if not bool((outputs == expected).all()):
            raise RuntimeError(
                f"Raw solver output coverage failed: expected {expected}, got {outputs.tolist()}"
            )
        if self.common_step_counter <= 3:
            print("FR3_NATIVE_COVERAGE=" + json.dumps({
                "control_step": self.common_step_counter, "expected_outputs": expected,
                "observed_min": int(outputs.min()), "observed_max": int(outputs.max()),
                "cuda_graph": NewtonManager._graph is not None,
            }), flush=True)
        return result

"""Experiment-owned manager environment; all backend packages remain untouched."""

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
        return super().step(action)

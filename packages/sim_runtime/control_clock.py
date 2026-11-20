"""Simulation-time sampling for the Newton program loop and deadline admission."""


class ControlClock:
    def __init__(self, physics_dt, control_hz, origin=0.):
        self.physics_dt = physics_dt
        self.control_dt = 1 / control_hz
        self.reset(origin)

    def reset(self, origin=0.):
        self._last_sample = origin
        self._next_sample = origin + self.control_dt

    def advance(self, sim_time):
        """Advance one physics step and return any due sample's elapsed time."""
        sim_time += self.physics_dt
        if sim_time + 1e-10 < self._next_sample:
            return sim_time, None
        elapsed = sim_time - self._last_sample
        self._last_sample = sim_time
        self._next_sample += self.control_dt
        return sim_time, elapsed

"""Bounded wall-clock budget, independent of the publication clock."""

import math
import time


class Pacer:
    def __init__(self, *, physics_dt, realtime_factor=1.0, max_lag=0.25, now=None, max_batch=64):
        if not math.isfinite(physics_dt) or physics_dt <= 0:
            raise ValueError("physics_dt must be finite and positive")
        if realtime_factor is not None and (not math.isfinite(realtime_factor) or realtime_factor <= 0):
            raise ValueError("realtime_factor must be positive or None")
        self.physics_dt = physics_dt
        self.realtime_factor = realtime_factor
        self.max_lag = max_lag
        self.max_batch = max_batch
        self.dropped_wall_seconds = 0.0
        self.reset_clock(time.monotonic() if now is None else now)

    def reset_clock(self, now=None):
        self._last = time.monotonic() if now is None else now
        self._budget = 0.0

    def steps_due(self, now=None) -> int:
        now = time.monotonic() if now is None else now
        elapsed = max(0, now - self._last)
        self._last = now
        rtf = self.realtime_factor
        if rtf is None:
            return self.max_batch
        self._budget += elapsed * rtf
        cap = max(self.max_lag * rtf, self.physics_dt)
        if self._budget > cap:
            self.dropped_wall_seconds += (self._budget - cap) / rtf
            self._budget = cap
        count = min(self.max_batch, int((self._budget + 1e-12) / self.physics_dt))
        self._budget -= count * self.physics_dt
        return count

    def return_steps(self, count):
        """Return reserved but unexecuted steps when control interrupts a batch."""
        if self.realtime_factor is not None:
            self._budget += count * self.physics_dt

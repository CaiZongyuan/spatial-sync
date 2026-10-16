"""Synchronized joint-space trapezoids, independent of the physics engine."""

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Trajectory:
    start: np.ndarray
    end: np.ndarray
    acceleration_time: np.ndarray
    local_duration: np.ndarray
    acceleration: np.ndarray
    duration: float

    def sample(self, elapsed):
        if self.duration == 0 or elapsed >= self.duration:
            return self.end.copy(), np.zeros_like(self.end)
        scale = self.local_duration / self.duration
        t = max(0.0, elapsed) * scale
        ramp = self.acceleration_time
        remaining = self.local_duration - t
        distance = np.where(
            t < ramp,
            0.5 * self.acceleration * t**2,
            np.where(
                remaining < ramp,
                np.abs(self.end - self.start) - 0.5 * self.acceleration * remaining**2,
                self.acceleration * ramp * (t - 0.5 * ramp),
            ),
        )
        velocity = self.acceleration * np.minimum(np.minimum(t, remaining), ramp) * scale
        sign = np.sign(self.end - self.start)
        return self.start + sign * distance, sign * velocity


def plan_trajectory(start, end, velocity_limits, *, speed_scale=0.5, speed=1.0, acceleration=2.0):
    start, end, limits = [np.asarray(v, dtype=float) for v in (start, end, velocity_limits)]
    if (
        start.ndim != 1
        or end.shape != start.shape
        or limits.shape != start.shape
        or not all(np.all(np.isfinite(v)) for v in (start, end, limits))
        or np.any(limits <= 0)
        or not 0 < speed_scale <= 1
        or not 0 < speed <= 1
        or not np.isfinite(acceleration)
        or acceleration <= 0
    ):
        raise ValueError("Finite equal-length joints and positive speed/acceleration limits are required")
    distance = np.abs(end - start)
    velocity = limits * speed_scale * speed
    ramp = np.minimum(velocity / acceleration, np.sqrt(distance / acceleration))
    duration = ramp + np.divide(distance, acceleration * ramp, out=np.zeros_like(ramp), where=ramp > 0)
    return Trajectory(
        start.copy(),
        end.copy(),
        ramp,
        duration,
        np.full_like(start, acceleration),
        float(np.max(duration, initial=0)),
    )

"""Finite, normalized poses with an explicit last-valid authority boundary."""

import numpy as np


class PoseFilter:
    def __init__(self, body_count: int):
        self.previous = None
        self.consecutive_invalid = np.zeros(body_count, dtype=np.int64)
        self.total_invalid = 0
        self.warning_indices = []

    def update(self, sample: np.ndarray) -> np.ndarray:
        sample = np.array(sample, dtype=np.float64, copy=True)
        if sample.shape != (len(self.consecutive_invalid), 7):
            raise ValueError("Unexpected body transform array shape")
        with np.errstate(over="ignore", invalid="ignore"):
            norms = np.linalg.norm(sample[:, 3:], axis=1)
        valid = np.isfinite(sample).all(axis=1) & np.isfinite(norms) & (norms > 1e-12)
        if self.previous is None and not valid.all():
            raise ValueError("Invalid pose in initial Newton snapshot")
        if self.previous is None:
            self.previous = np.zeros_like(sample)
        sample[valid, 3:] /= norms[valid, None]
        sample[~valid] = self.previous[~valid]
        opposite = np.sum(sample[:, 3:] * self.previous[:, 3:], axis=1) < 0
        sample[opposite, 3:] *= -1
        self.consecutive_invalid[valid] = 0
        self.consecutive_invalid[~valid] += 1
        self.total_invalid += int((~valid).sum())
        self.warning_indices = np.flatnonzero(~valid & (self.consecutive_invalid == 30)).tolist()
        self.previous = sample.copy()
        return sample

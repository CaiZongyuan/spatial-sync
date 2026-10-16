"""Finite, normalized poses with an explicit last-valid authority boundary."""

import numpy as np


class PoseFilter:
    def __init__(self, body_count: int):
        self.previous = None
        self._known = np.zeros(body_count, dtype=bool)
        self.consecutive_invalid = np.zeros(body_count, dtype=np.int64)
        self.total_invalid = 0
        self.warning_indices = []

    def update(self, sample: np.ndarray, body_indices=None) -> np.ndarray:
        indices = np.arange(len(self._known)) if body_indices is None else np.asarray(body_indices)
        sample = np.array(sample, dtype=np.float64, copy=True)
        if sample.shape != (len(indices), 7):
            raise ValueError("Unexpected body transform array shape")
        with np.errstate(over="ignore", invalid="ignore"):
            norms = np.linalg.norm(sample[:, 3:], axis=1)
        valid = np.isfinite(sample).all(axis=1) & np.isfinite(norms) & (norms > 1e-12)
        if np.any(~valid & ~self._known[indices]):
            raise ValueError("Invalid pose in initial Newton snapshot")
        if self.previous is None:
            self.previous = np.zeros((len(self._known), 7), dtype=np.float64)
        sample[valid, 3:] /= norms[valid, None]
        previous = self.previous[indices]
        sample[~valid] = previous[~valid]
        opposite = np.sum(sample[:, 3:] * previous[:, 3:], axis=1) < 0
        sample[opposite, 3:] *= -1
        self.consecutive_invalid[indices[valid]] = 0
        self.consecutive_invalid[indices[~valid]] += 1
        self.total_invalid += int((~valid).sum())
        self.warning_indices = indices[~valid & (self.consecutive_invalid[indices] == 30)].tolist()
        self.previous[indices] = sample
        self._known[indices] = True
        return sample

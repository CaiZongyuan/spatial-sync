"""Numeric contracts shared by managed training and standalone inference."""

import numpy as np


def command_endpoints(bounds):
    """Return ordered command endpoints in the actor's finite float32 domain."""
    original = np.asarray(bounds, dtype=np.float64)
    if (
        original.ndim != 2 or original.shape[1] != 2 or not original.shape[0]
        or not np.isfinite(original).all() or np.any(original[:, 0] > original[:, 1])
        or np.any(np.abs(original) > np.finfo(np.float32).max)
    ):
        raise ValueError("Expected finite ordered float32 command bounds")
    return original.astype(np.float32)


def command_in_bounds(values, bounds):
    original = np.asarray(bounds, dtype=np.float64)
    tensor = command_endpoints(original).astype(np.float64)
    values = np.asarray(values, dtype=np.float64)
    # A decimal endpoint and its actual tensor representation both denote the boundary.
    low, high = np.minimum(original[:, 0], tensor[:, 0]), np.maximum(original[:, 1], tensor[:, 1])
    return values.shape == low.shape and bool(np.all((low <= values) & (values <= high)))


def action_delta_limit(action):
    """Read the optional offset cap without interpreting null as a numeric array."""
    limit = action.get("maxDelta")
    if limit is None and action["space"] == "joint_position_delta":
        raise ValueError("joint_position_delta requires maxDelta")
    return limit

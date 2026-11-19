"""Simulation-time reference motion sampling shared with the trainer and export runner."""

import math
from bisect import bisect_right


def slerp_xyzw(start, end, fraction):
    dot = sum(a * b for a, b in zip(start, end, strict=True))
    if dot < 0:
        end = [-value for value in end]
        dot = -dot
    if dot > 0.9995:
        result = [a + fraction * (b - a) for a, b in zip(start, end, strict=True)]
        length = math.hypot(*result)
        return [value / length for value in result]
    angle = math.acos(min(1.0, dot))
    denominator = math.sin(angle)
    left = math.sin((1 - fraction) * angle) / denominator
    right = math.sin(fraction * angle) / denominator
    return [left * a + right * b for a, b in zip(start, end, strict=True)]


def motion_observations(motion, elapsed):
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not math.isfinite(elapsed):
        raise ValueError("Motion elapsed time must be finite seconds")
    frames = motion["frames"]
    duration = frames[-1]["time"]
    elapsed = max(0.0, elapsed)
    if motion["loop"]:
        time = elapsed % duration
        angle = math.tau * time / duration
        phase = [math.sin(angle), math.cos(angle)]
    else:
        time = min(elapsed, duration)
        phase = [duration - time]
    index = min(bisect_right(frames, time, key=lambda frame: frame["time"]), len(frames) - 1)
    start, end = frames[index - 1], frames[index]
    fraction = (time - start["time"]) / (end["time"] - start["time"])

    def lerp(field):
        return [a + fraction * (b - a) for a, b in zip(start[field], end[field], strict=True)]

    return {
        "motion_phase": phase,
        "reference_motion_frame": [
            *lerp("position"),
            *slerp_xyzw(start["orientation"], end["orientation"], fraction),
            *lerp("joints"),
        ],
    }

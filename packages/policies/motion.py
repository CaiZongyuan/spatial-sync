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


def _interval(motion, elapsed):
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

    return phase, start, end, fraction, not motion["loop"] and elapsed >= duration


def _lerp(start, end, fraction):
    return [a + fraction * (b - a) for a, b in zip(start, end, strict=True)]


def motion_observations(motion, elapsed):
    phase, start, end, fraction, _ = _interval(motion, elapsed)
    return {
        "motion_phase": phase,
        "reference_motion_frame": [
            *_lerp(start["position"], end["position"], fraction),
            *slerp_xyzw(start["orientation"], end["orientation"], fraction),
            *_lerp(start["joints"], end["joints"], fraction),
        ],
    }


def reference_tracking(motion, elapsed):
    _, start, end, fraction, held = _interval(motion, elapsed)
    dt = end["time"] - start["time"]
    a, b = start["orientation"], end["orientation"]
    sign = -1 if sum(x * y for x, y in zip(a, b, strict=True)) < 0 else 1
    x, y, z, w = a
    u, v, t, r = (sign * value for value in b)
    # q_end * conjugate(q_start) gives angular velocity in the reference world frame.
    axis = [w*u - r*x - v*z + t*y, w*v - r*y - t*x + u*z, w*t - r*z - u*y + v*x]
    cosine = w*r + x*u + y*v + z*t
    length = math.hypot(*axis)
    rate = 2 * math.atan2(length, cosine) / (length * dt) if length and not held else 0
    return {
        "rootPosition": _lerp(start["position"], end["position"], fraction),
        "rootQuaternion": slerp_xyzw(a, b, fraction),
        "rootLinearVelocity": [0., 0., 0.] if held else [
            (right - left) / dt for left, right in zip(start["position"], end["position"], strict=True)
        ],
        "rootAngularVelocity": [value * rate for value in axis],
        "bodyLocal": {
            key: _lerp(start["bodyLocal"][key], end["bodyLocal"][key], fraction)
            for key in sorted(start.get("bodyLocal", {}))
        },
    }

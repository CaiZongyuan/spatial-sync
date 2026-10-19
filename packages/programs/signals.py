"""Simulation-time signal functions shared by virtual lab devices."""

import math


def lag(value, target, dt, time_constant):
    return target + (value - target) * math.exp(-dt / time_constant)


def noisy(ctx, value, amplitude, minimum, maximum):
    return min(maximum, max(minimum, value + ctx.rng.uniform(-amplitude, amplitude)))

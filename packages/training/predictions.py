"""Numeric prediction syntax shared by Plan validation and Notebook judgement."""

import math
import re

PREDICTION = re.compile(r"\s*(>=|<=|>|<|==|=)?\s*([-+]?\d+(?:\.\d*)?(?:[eE][-+]?\d+)?)\s*")


def prediction(value):
    if type(value) in (int, float) and math.isfinite(value):
        return ">=", float(value)
    match = PREDICTION.fullmatch(value) if isinstance(value, str) else None
    if match is None or not math.isfinite(float(match[2])):
        raise ValueError("Prediction requires a finite numeric threshold")
    return match[1] or ">=", float(match[2])

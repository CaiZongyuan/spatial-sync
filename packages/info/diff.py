"""Deadband and monotonic publication limits with a final pending value."""

import time
from copy import deepcopy

from .schema_subset import json_equal

_JSON_SCALARS = (type(None), bool, int, float, str)


def _copy_value(value):
    # Subclasses and unknown values may own mutable state; only exact primitive
    # JSON types can share a reference without weakening defensive ownership.
    return value if type(value) in _JSON_SCALARS else deepcopy(value)


def changed(previous, value, deadband):
    if deadband is not None:
        if type(previous) in (int, float) and type(value) in (int, float):
            return abs(previous - value) >= deadband and previous != value
        if (
            isinstance(previous, list)
            and isinstance(value, list)
            and len(previous) == len(value)
            and all(type(v) in (int, float) for v in previous + value)
        ):
            return any(abs(a - b) >= deadband and a != b for a, b in zip(previous, value))
    return not json_equal(previous, value)


class PropertyDiff:
    def __init__(self, definitions, *, clock=None):
        self.definitions = definitions
        self.clock = clock or time.monotonic
        self.sent = {}
        self.at = {}
        self.pending = {}

    def update(self, values, publication_time=None, *, force=()):
        now = self.clock() if publication_time is None else publication_time
        if not values and not self.pending:
            return {}
        for prop, value in values.items():
            self.pending[prop] = _copy_value(value)
        outgoing = {}
        for prop, value in list(self.pending.items()):
            definition = self.definitions[prop]
            if prop not in force and prop in self.sent and not changed(
                self.sent[prop], value, definition.get("deadband")
            ):
                del self.pending[prop]
                continue
            rate = definition.get("maxRateHz")
            if prop not in force and rate and prop in self.at and now - self.at[prop] + 1e-10 < 1 / rate:
                continue
            outgoing[prop] = _copy_value(value)
            self.sent[prop], self.at[prop] = _copy_value(value), now
            del self.pending[prop]
        return outgoing

    def reset(self):
        self.sent.clear()
        self.at.clear()
        self.pending.clear()

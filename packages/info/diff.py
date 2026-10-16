"""Deadband and simulation-time publication limits with a final pending value."""

from copy import deepcopy

from .schema_subset import json_equal


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
    def __init__(self, definitions):
        self.definitions = definitions
        self.sent = {}
        self.at = {}
        self.pending = {}

    def update(self, values, sim_time):
        self.pending.update(deepcopy(values))
        outgoing = {}
        for prop, value in list(self.pending.items()):
            definition = self.definitions[prop]
            if prop in self.sent and not changed(self.sent[prop], value, definition.get("deadband")):
                del self.pending[prop]
                continue
            rate = definition.get("maxRateHz")
            if rate and prop in self.at and sim_time - self.at[prop] + 1e-10 < 1 / rate:
                continue
            outgoing[prop] = deepcopy(value)
            self.sent[prop], self.at[prop] = deepcopy(value), sim_time
            del self.pending[prop]
        return outgoing

    def reset(self):
        self.sent.clear()
        self.at.clear()
        self.pending.clear()

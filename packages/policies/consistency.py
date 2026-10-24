"""Numerical comparison of independently exported training observations and states."""

import numpy as np

from .observations import assemble, vector


def compare_observations(spec, samples):
    if not samples:
        raise ValueError("At least one independently observed training state is required")
    maximum = 0.0
    per_item = {item.name: 0.0 for item in spec.observations}
    for sample in samples:
        expected = vector(sample["trainingObservation"], spec.size)
        actual = assemble(
            spec,
            sample["state"],
            previous_action=sample["previousAction"],
            references=sample.get("references", {}),
        )
        error = np.abs(actual - expected)
        maximum = max(maximum, float(error.max()))
        offset = 0
        for item in spec.observations:
            end = offset + item.shape[0]
            per_item[item.name] = max(per_item[item.name], float(error[offset:end].max()))
            offset = end
    return {
        "samples": len(samples),
        "requiredSamples": 256,
        "maxAbsoluteError": maximum,
        "tolerance": 1e-4,
        "perItem": per_item,
        "numericalPass": len(samples) >= 256 and maximum <= 1e-4,
        "qualified": False,
        "provenance": "caller-supplied independent training samples",
    }

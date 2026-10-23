"""Measured evidence is scoped to actual immutable inputs, not guessed timing defaults."""

import json

import pytest

from packages.training.profiles import measured_profile


@pytest.mark.parametrize("mismatch", [None, "taskContentHash", "hardware", "model", "hyperparameters"])
def test_imported_measurement_matches_exact_scope_and_retains_conservative_samples(mismatch):
    job = {"taskContentHash": "a" * 64}
    plan = {"algorithm": {"name": "PPO"}, "model": {"actor": [32]}, "hyperparameters": {"num_envs": 4}}
    node = {"hardware": "actual-hardware-scope", "gpus": [], "cpuAvailable": True}
    receipt = {
        "schema": "lw.training-profile/1",
        **job,
        **plan,
        "hardware": node["hardware"],
        "resourceId": "cpu",
        "samplesSeconds": [2, 3, 2.5],
        "setupSeconds": 4,
        "measuredAt": 100,
    }
    if mismatch:
        receipt[mismatch] = "wrong scope"
        with pytest.raises(ValueError, match="does not match"):
            measured_profile(json.dumps(receipt).encode(), job, plan, node, "cpu")
    else:
        profile, digest = measured_profile(json.dumps(receipt).encode(), job, plan, node, "cpu")
        assert profile["iterationSeconds"] == 3 and profile["samples"] == 3 and profile["setupSeconds"] == 4
        assert digest in profile["evidence"] and len(profile["key"]) == 64

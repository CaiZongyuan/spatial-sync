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


@pytest.mark.parametrize("mutation", [None, "legacy_schema", "entry", "compiler", "versions"])
def test_modern_measurement_cannot_be_rebound_to_current_runtime(mutation):
    from tests.gateway.test_remote_training_nodes import MJLAB_VERSIONS

    job = {"taskContentHash": "a" * 64, "backend": "mjlab"}
    plan = {
        "algorithm": {"version": "5.0.1"}, "model": {"actor": [32]}, "hyperparameters": {"num_envs": 4},
    }
    identity = {
        "backend": "mjlab", "entry": "mjlab:managed", "compilerHash": "b" * 64,
        "trainerVersions": MJLAB_VERSIONS,
    }
    node = {
        "id": "local", "hardware": "node-hardware/2:fixture", "gpus": [], "cpuAvailable": True,
        "trainerVersions": MJLAB_VERSIONS, "compilerHash": "b" * 64,
        "trainingEntries": {"mjlab": "mjlab:managed"},
    }
    receipt = {
        "schema": "lw.training-profile/2", "taskContentHash": job["taskContentHash"],
        "runtimeIdentity": identity, **plan, "hardware": node["hardware"], "resourceId": "cpu",
        "samplesSeconds": [2, 3], "setupSeconds": 4, "dispatchOverheadSeconds": 1.5, "measuredAt": 100,
    }
    if mutation == "legacy_schema":
        receipt["schema"] = "lw.training-profile/1"
        del receipt["runtimeIdentity"]
    if mutation == "entry":
        receipt["runtimeIdentity"] = identity | {"entry": "custom"}
    if mutation == "compiler":
        node["compilerHash"] = "c" * 64
    if mutation == "versions":
        node["trainerVersions"] = MJLAB_VERSIONS | {"torch": "2.9.1+cu128"}
    raw = json.dumps(receipt).encode()
    if mutation:
        with pytest.raises(ValueError, match="does not match"):
            measured_profile(raw, job, plan, node, "cpu")
    else:
        profile, _ = measured_profile(raw, job, plan, node, "cpu")
        assert profile["dispatchOverheadSeconds"] == 1.5

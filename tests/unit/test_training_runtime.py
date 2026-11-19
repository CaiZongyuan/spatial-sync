"""The gateway and launcher bridge share the exact RSL-RL version contract."""

import pytest

from apps.node import bridge_callback
from packages.training.runtime import RuntimeMismatch, training_runtime
from tests.gateway.test_remote_training_nodes import ISAAC_VERSIONS, MJLAB_VERSIONS


@pytest.mark.parametrize("kind,backend,pinned", [
    ("training", "mjlab", "5.0.1"),
    ("plan_validation", "mjlab", "5.0.1"),
    ("training", "isaaclab", "5.4.1+workstation"),
])
def test_training_lease_matches_actual_bridge_versions(monkeypatch, kind, backend, pinned):
    mjlab = backend == "mjlab"
    versions = (MJLAB_VERSIONS if mjlab else ISAAC_VERSIONS) | {"rsl_rl": pinned, "torch": (
        "2.9.1+cu128" if mjlab else "2.11.0+cu128"
    )}
    installed = {
        distribution: versions[name]
        for name, distribution in bridge_callback.trainer_distributions(mjlab).items()
    }
    monkeypatch.setattr(bridge_callback.importlib.metadata, "version", installed.__getitem__)
    job = {"kind": kind, "plan": {"algorithm": {"version": pinned}}}
    node = {
        "id": "worker", "hardware": "node-hardware/2:fixture", "trainerVersions": versions,
        "compilerHash": "b" * 64 if mjlab else None,
        "trainingEntries": {backend: "mjlab:managed" if mjlab else "isaaclab:cli"},
    }
    lease = training_runtime(node, job, backend)
    assert bridge_callback.verify_trainer_versions(job | lease, mjlab) == versions
    missing = {key: value for key, value in versions.items() if key != "rsl_rl"}
    wrong = versions | {"rsl_rl": pinned.split("+")[0] + "+other"}
    for incompatible in (missing, wrong):
        with pytest.raises(RuntimeMismatch, match="pinned Job runtime"):
            training_runtime(node | {"trainerVersions": incompatible}, job, backend)
    installed["rsl-rl-lib"] = wrong["rsl_rl"]
    with pytest.raises(RuntimeError, match="differs from the approved plan"):
        bridge_callback.verify_trainer_versions(job | lease, mjlab)

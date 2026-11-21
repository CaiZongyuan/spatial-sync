"""The gateway and launcher bridge share the exact RSL-RL version contract."""

from types import SimpleNamespace

import pytest

from apps.node import bridge_callback, runner
from packages.training.runtime import (
    INITIALIZATION_R1_COMPILER,
    INITIALIZATION_R2_COMPILER,
    INITIALIZED_CHECKPOINT_MIGRATION_HEADER,
    PRE_INITIALIZATION_COMPILER,
    RuntimeMismatch,
    compatible_checkpoint_runtime,
    training_runtime,
)
from tests.gateway.test_remote_training_nodes import ISAAC_VERSIONS, MJLAB_VERSIONS


def test_initialized_checkpoint_runtime_migration_is_exact():
    current = "c" * 64
    source = {"backend": "mjlab", "entry": "mjlab:managed", "compilerHash": INITIALIZATION_R2_COMPILER,
              "trainerVersions": MJLAB_VERSIONS}
    target = source | {"compilerHash": current}
    plan = {"initialization": {"sha256": "a" * 64}}
    assert compatible_checkpoint_runtime(target, target, plan, current)
    assert not compatible_checkpoint_runtime(source, target, plan, current)
    assert not compatible_checkpoint_runtime(source, target, plan, current, migration_version="2")
    assert compatible_checkpoint_runtime(source, target, plan, current, migration_version="1")
    versions = MJLAB_VERSIONS | {"torch": "2.9.1+other"}
    for change in ({"backend": "isaaclab"}, {"entry": "other"}, {"trainerVersions": versions}):
        assert not compatible_checkpoint_runtime(
            source, target | change, plan, current, migration_version="1",
        )
    isaac = {"backend": "isaaclab"}
    assert not compatible_checkpoint_runtime(
        source | isaac, target | isaac, plan, current, migration_version="1",
    )
    assert not compatible_checkpoint_runtime(source, target, plan, "d" * 64, migration_version="1")
    for compiler in (PRE_INITIALIZATION_COMPILER, INITIALIZATION_R1_COMPILER):
        historical = source | {"compilerHash": compiler}
        assert compatible_checkpoint_runtime(historical, target, {}, current)
        assert not compatible_checkpoint_runtime(historical, target, plan, current, migration_version="1")


@pytest.mark.parametrize("pinned", ["5.0.1", "5.0.2"])
def test_installed_managed_node_publishes_migration_header_from_runtime(tmp_path, monkeypatch, pinned):
    caps = tmp_path / "caps.json"
    caps.write_text('{"hardware":"controlled","gpus":[],"checkpointVersion":1}')
    monkeypatch.setattr(runner, "node_credentials", lambda args: ("test-node", "fixture-token"))
    monkeypatch.setattr(runner, "trainer_versions", lambda prefix, *, is_mjlab: (
        MJLAB_VERSIONS | {"rsl_rl": pinned} if is_mjlab else {}
    ))
    observed = []

    async def capture(node):
        observed.append(node.headers.get(INITIALIZED_CHECKPOINT_MIGRATION_HEADER))

    monkeypatch.setattr(runner.LocalNode, "run", capture)
    runner.run_node(SimpleNamespace(
        trainer="[]", mjlab_trainer='["installed-python"]', capabilities=str(caps), root=str(tmp_path),
        url="http://127.0.0.1:8000", interval=10, timeout=180,
    ))
    assert observed == ["1" if pinned == "5.0.1" else None]


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

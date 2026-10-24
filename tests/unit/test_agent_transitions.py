"""Real Harness files, interrupted swaps and unrelated editor saves cross one interface."""

import json
import os

import pytest

from packages.library.agents import AgentLibrary, pack_harness
from packages.library.common import LibraryError, content_hash
from tests.unit.test_agent_harness import changed_archive, library


def candidate(tmp_path):
    agents = library(tmp_path)
    original = agents.export("lab-operator", 1)
    agents.create("rsi", original)
    version = agents.snapshot("rsi", changed_archive(tmp_path))
    return agents, original, version


def restore(agents, version, **overrides):
    return agents.transition(
        "rsi", target_version=1, expected_version=version["version"],
        expected_hash=version["contentHash"], request_id="reject-round-one",
        outcome_version=version["version"], outcome="rejected", **overrides,
    )


def test_rejection_restores_actual_files_keeps_candidate_and_never_reuses_its_version(tmp_path):
    agents, original, version = candidate(tmp_path)
    directory = agents.user_root / "agents/rsi/harness"
    receipt = restore(agents, version)
    assert receipt["status"] == "committed" and receipt["targetVersion"] == 1
    assert pack_harness(directory) == original
    assert content_hash(directory) == agents.version("rsi", 1)["contentHash"]
    assert agents.show("rsi")["currentVersion"] == agents.show("rsi")["referenceVersion"] == 1
    assert agents.version("rsi", 2)["decision"] == "rejected"
    assert agents.export("rsi", 2) != original
    assert restore(agents, version) == receipt
    assert agents.snapshot("rsi", changed_archive(tmp_path, "A different bounded change.\n"))["version"] == 3
    assert restore(agents, version) == receipt
    assert agents.show("rsi")["currentVersion"] == 3


@pytest.mark.parametrize("failure", ["publish-files", "publish-metadata"])
def test_interrupted_file_swap_is_recovered_from_its_durable_intent(tmp_path, monkeypatch, failure):
    agents, original, version = candidate(tmp_path)
    if failure == "publish-files":
        rename = os.rename

        def interrupted(source, target):
            if source.name == "staged" and target.name == "harness":
                raise OSError("Controlled interruption after the old files were retained")
            return rename(source, target)

        monkeypatch.setattr("packages.library.agent_transitions.os.rename", interrupted)
    else:
        from packages.library.transactions import durable_json

        def interrupted(path, value):
            if path.name == "agent.yaml":
                raise OSError("Controlled interruption after the new files were published")
            return durable_json(path, value)

        monkeypatch.setattr("packages.library.agent_transitions.durable_json", interrupted)
    with pytest.raises(LibraryError) as failed:
        restore(agents, version)
    assert failed.value.status == 503
    monkeypatch.undo()
    restarted = AgentLibrary(agents.roots)
    assert restarted.recover() == [
        {"agentId": "agent/rsi", "requestId": "reject-round-one", "status": "committed"}
    ]
    assert pack_harness(agents.user_root / "agents/rsi/harness") == original
    assert restarted.show("rsi")["currentVersion"] == 1
    assert restore(restarted, version)["status"] == "committed"


def test_unexpected_editor_bytes_are_preserved_instead_of_silently_restored(tmp_path):
    agents, original, version = candidate(tmp_path)
    directory = agents.user_root / "agents/rsi/harness"
    edited = directory / "AGENTS.md"
    edited.write_text("Unrelated later user edit.\n", encoding="utf-8")
    with pytest.raises(LibraryError) as conflict:
        restore(agents, version)
    assert conflict.value.status == 409
    assert edited.read_text(encoding="utf-8") == "Unrelated later user edit.\n"
    assert agents.show("rsi")["currentVersion"] == 2
    assert agents.export("rsi", 1) == original


def test_editor_save_at_the_swap_is_preserved_and_never_committed_as_a_restore(tmp_path, monkeypatch):
    agents, _, version = candidate(tmp_path)
    directory = agents.user_root / "agents/rsi/harness"
    rename = os.rename

    def concurrent_save(source, target):
        if source == directory:
            (source / "AGENTS.md").write_text("Saved at the publication point.\n", encoding="utf-8")
        return rename(source, target)

    monkeypatch.setattr("packages.library.agent_transitions.os.rename", concurrent_save)
    with pytest.raises(LibraryError) as conflict:
        restore(agents, version)
    assert conflict.value.status == 409
    assert (directory / "AGENTS.md").read_text(encoding="utf-8") == "Saved at the publication point.\n"
    assert agents.show("rsi")["currentVersion"] == 2


def test_restart_preserves_editor_conflict_after_intent_was_saved(tmp_path, monkeypatch):
    agents, _, version = candidate(tmp_path)
    edited = agents.user_root / "agents/rsi/harness/AGENTS.md"
    from packages.library.transactions import durable_json

    def interrupted(path, value):
        durable_json(path, value)
        if path.name == "intent.json":
            edited.write_text("User saved after durable intent.\n", encoding="utf-8")
            raise OSError("Controlled interruption before swap")

    monkeypatch.setattr("packages.library.agent_transitions.durable_json", interrupted)
    with pytest.raises(LibraryError):
        restore(agents, version)
    monkeypatch.undo()
    recovered = AgentLibrary(agents.roots).recover()
    assert recovered == [{"agentId": "agent/rsi", "requestId": "reject-round-one", "status": "blocked"}]
    assert edited.read_text(encoding="utf-8") == "User saved after durable intent.\n"
    assert agents.show("rsi")["currentVersion"] == 2
    intent = agents.user_root / "agents/rsi/transitions/reject-round-one/intent.json"
    assert json.loads(intent.read_bytes())["status"] == "prepared"

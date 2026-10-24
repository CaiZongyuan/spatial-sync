"""Restricted execution checks real frozen Harness archives, not their mutable worktree."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from packages.agents.bundle import PROFILE, prepare_harness
from packages.info.plans import PlanError
from packages.library.agents import AgentLibrary, pack_harness

ROOT = Path(__file__).resolve().parents[2]
LAB_PIN = "5fea203445c6aeee93b5cdfe8cb00f49a3488cdb03f3c4b03f2c2c4ac3ba540d"


def library(tmp_path):
    user = tmp_path / "library"
    (user / "agents").mkdir(parents=True)
    return AgentLibrary({"builtin": ROOT / "library", "user": user})


def create_harness(agents, root, *, hook=False, custom_mcp=False):
    root.mkdir()
    (root / "AGENTS.md").write_text("Follow the public task statement.\n", encoding="utf-8")
    (root / "skills.lock").write_text("skills: []\n", encoding="utf-8")
    marker = root.parent / "must-not-execute"
    command = f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')"
    servers = {"lab": {"command": "lw", "args": ["mcp"], "env": {"LW_URL": "${LW_URL}"}}}
    if custom_mcp:
        servers = {"evil": {"command": "python", "args": ["-c", command]}}
    (root / "mcp.yaml").write_text(yaml.safe_dump({"servers": servers}), encoding="utf-8")
    if hook:
        (root / "hooks").mkdir()
        (root / "hooks" / "before.py").write_text(command, encoding="utf-8")
    agents.create("untrusted-harness", pack_harness(root))
    return marker


def test_shipped_harness_recovers_the_exact_historical_lab_skill_without_using_current_bytes(tmp_path):
    agents = library(tmp_path)
    prepared = prepare_harness(agents, "lab-operator", 1)
    assert prepared.profile == PROFILE and prepared.platform_mcp
    files = dict(prepared.files)
    assert hashlib.sha256(files["skills/lab/SKILL.md"]).hexdigest() == LAB_PIN
    assert prepared.harness_hash == agents.version("lab-operator", 1)["contentHash"]
    assert {name for name, _ in prepared.skills} == {"lab", "lab-operate"}
    assert "hooks/README.md" in dict((name.removeprefix("harness/"), data) for name, data in prepared.files)


@pytest.mark.parametrize("mode", ["hook", "stdio"])
def test_legal_executable_authoring_inputs_are_rejected_before_they_can_execute(tmp_path, mode):
    agents = library(tmp_path)
    marker = create_harness(agents, tmp_path / "authoring", hook=mode == "hook", custom_mcp=mode == "stdio")
    # Import/export really supports these legal archived inputs; execution has an explicit profile gate.
    assert agents.export("untrusted-harness", 1)
    with pytest.raises(PlanError) as failure:
        prepare_harness(agents, "untrusted-harness", 1)
    assert failure.value.detail["code"] == "unsupported_harness_capability"
    assert not marker.exists()


def test_missing_locked_skill_does_not_silently_install_the_current_skill(tmp_path):
    agents = library(tmp_path)
    create_harness(agents, tmp_path / "authoring")
    # Snapshot a valid changed lock as a second real version.
    root = agents.user_root / "agents/untrusted-harness/harness"
    (root / "skills.lock").write_text(yaml.safe_dump({"skills": [{"name": "lab", "version": "f" * 64}]}))
    version = agents.snapshot("untrusted-harness")
    with pytest.raises(PlanError) as failure:
        prepare_harness(agents, "untrusted-harness", version["version"])
    assert failure.value.detail["code"] == "skill_version_unavailable"


def test_selected_version_does_not_follow_mutable_current_harness_files(tmp_path):
    agents = library(tmp_path)
    create_harness(agents, tmp_path / "authoring")
    before = prepare_harness(agents, "untrusted-harness", 1)
    current = agents.user_root / "agents/untrusted-harness/harness/AGENTS.md"
    current.write_text("This is a different, unpublished strategy.\n", encoding="utf-8")
    after = prepare_harness(agents, "untrusted-harness", 1)
    assert before.files == after.files and before.harness_hash == after.harness_hash
    assert b"unpublished" not in dict(after.files)["harness/AGENTS.md"]
    assert json.dumps(after.manifest, sort_keys=True) == json.dumps(before.manifest, sort_keys=True)

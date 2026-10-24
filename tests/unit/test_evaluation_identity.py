"""Candidate class edits cannot disguise changed shared physics or judge bytes."""

import hashlib
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.evaluation_identity import (
    ENVIRONMENT,
    JUDGE,
    bind_source_identity,
    freeze_identity,
    split_program_class,
    verify_identity,
)
from packages.library.common import BUILTIN_ROOT
from packages.library.scenes import SceneLibrary
from packages.library.suites import SuiteLibrary
from packages.protocol.internal import SessionOptions

PROGRAM = b"""HOME = 0

def kinematics():
    return "fixed-observation"

class SelectedProgram:
    def tick(self):
        return "candidate-one"

class OtherProgram:
    def tick(self):
        return "fixed-other-device"
"""


def sha(value):
    return hashlib.sha256(value).hexdigest()


def test_class_only_change_keeps_module_environment_but_shared_helpers_and_other_programs_do_not():
    one, environment, location = split_program_class(PROGRAM, "SelectedProgram")
    two, same_environment, _ = split_program_class(
        PROGRAM.replace(b"candidate-one", b"candidate-two"), "SelectedProgram"
    )
    assert sha(one) != sha(two) and environment == same_environment
    assert b"def kinematics" in environment and b"OtherProgram" in environment
    assert location == {"class": "SelectedProgram", "firstLine": 6, "lastLine": 8}
    _, physics_changed, _ = split_program_class(
        PROGRAM.replace(b"fixed-observation", b"changed-observation"), "SelectedProgram"
    )
    _, other_changed, _ = split_program_class(
        PROGRAM.replace(b"fixed-other-device", b"changed-other-device"), "SelectedProgram"
    )
    assert physics_changed != environment and other_changed != environment


def test_recovery_retains_actual_dirty_bytes_and_lock_instead_of_only_a_commit_hash(tmp_path):
    root = tmp_path / "project"
    for name in set(ENVIRONMENT) | set(JUDGE):
        path = root / name
        if not path.suffix:
            path /= "fixture.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# dirty local implementation\n")
    suite_path, scene_path = root / "library/suite/1", root / "library/scene/1"
    suite_path.mkdir(parents=True)
    scene_path.mkdir(parents=True)
    (suite_path / "private.yaml").write_bytes(b"private: retained-only-in-owned-snapshot\n")
    (scene_path / "scene.yaml").write_bytes(b"scene: fixed\n")
    (scene_path / "geometry.usd").write_bytes(b"#usda 1.0\n")
    suite = SimpleNamespace(
        id="suite/fixture@1",
        content_hash="a" * 64,
        path=suite_path,
        metadata={"source": "synthetic"},
        grading={"fixture": {"dimensions": []}},
    )
    scene = SimpleNamespace(
        id="fixture@1",
        content_hash="b" * 64,
        path=scene_path,
        asset_dependencies=lambda: [],
        template_dependencies=lambda: [],
    )
    inputs = freeze_identity(
        "eval-fixture",
        suite,
        scene,
        "agent/fixture",
        root / "owned-inputs",
        candidate={"kind": "agent-harness", "contentHash": "c" * 64},
        root=root,
    )
    verify_identity(inputs, root=root, directory=root / "owned-inputs")
    extra = root / "packages/programs/new_dependency.py"
    extra.write_bytes(b"# new implementation must invalidate old evidence\n")
    with pytest.raises(ValueError, match="implementation changed"):
        verify_identity(inputs, root=root)
    extra.unlink()
    (root / "uv.lock").write_text("changed after capture\n", encoding="utf-8")
    with ZipFile(root / "owned-inputs" / inputs["recovery"]["file"]) as archive:
        assert archive.read("code/uv.lock") == b"# dirty local implementation\n"
        assert archive.read("suite/private.yaml") == b"private: retained-only-in-owned-snapshot\n"
        assert archive.read("scene/geometry.usd") == b"#usda 1.0\n"
    assert inputs["files"]["uv.lock"] == sha(b"# dirty local implementation\n")


def test_shared_candidate_class_stays_unknown_even_for_a_supported_actual_source():
    session = SimpleNamespace(
        source=SyntheticSource("owned-identity-test"),
        manifest=SimpleNamespace(source=SimpleNamespace(kind="synthetic", newtonVersion=None, solver=None)),
        options=SessionOptions(),
        catalog=SimpleNamespace(
            entities={"/target": {"program": "robot_arm.virtual"}, "/other": {"program": "robot_arm.virtual"}}
        ),
    )
    frozen = {
        "candidateIdentity": {
            "kind": "scene-program",
            "programId": "robot_arm.virtual",
            "contentHash": "candidate-content",
            "target": "/target",
        },
        "programSlot": {"programId": "robot_arm.virtual", "target": "/target"},
        "environmentInputs": {},
        "judgeInputs": {},
    }
    identity, actual, reason = bind_source_identity(frozen, session)
    assert identity is None and reason == "shared_candidate_implementation"
    assert actual["implementation"] == "apps.api.app.sources.synthetic_source.SyntheticSource"


def test_program_and_weight_candidates_freeze_the_same_fixed_suite_program_slot(tmp_path):
    scenes = SceneLibrary(user_root=tmp_path / "library", state_root=tmp_path / "state")
    suites = SuiteLibrary({"builtin": BUILTIN_ROOT, "user": tmp_path / "library"})
    suites.refresh()
    suite = suites.resolve("suite/lab-demo-pick-place@1")
    scene = scenes.resolve(suite.metadata["scene"], launch=True, source=suite.metadata["source"])
    program = freeze_identity("eval-script", suite, scene, "robot_arm.virtual", tmp_path / "snapshots")
    policy = freeze_identity(
        "eval-policy",
        suite,
        scene,
        "policy/fixture@1",
        tmp_path / "snapshots",
        candidate={
            "kind": "policy",
            "id": "policy/fixture@1",
            "contentHash": "weights-one",
            "task": "training@1",
        },
    )
    second = freeze_identity(
        "eval-policy2",
        suite,
        scene,
        "policy/fixture@2",
        tmp_path / "snapshots",
        candidate={
            "kind": "policy",
            "id": "policy/fixture@2",
            "contentHash": "weights-two",
            "task": "training@2",
        },
    )
    assert program["unknownReason"] is None
    assert program["programSlot"] == policy["programSlot"] == second["programSlot"]
    assert program["environmentInputs"] == policy["environmentInputs"] == second["environmentInputs"]
    assert program["judgeInputs"] == policy["judgeInputs"] == second["judgeInputs"]
    assert program["candidateIdentity"] != policy["candidateIdentity"] != second["candidateIdentity"]

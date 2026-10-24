"""Immutable DemoSet versions, idempotent admission and held-out data exclusion."""

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from apps.api.app.demonstrations import DemonstrationService
from packages.info.plans import PlanError
from packages.library.common import content_hash
from packages.protocol.schema import Actor
from packages.training.demonstration_evidence import recording_evidence
from packages.training.demonstrations import Demonstrations
from tests.unit.test_demonstration_evidence import FIXTURE, recorded_task

ACTOR = Actor(kind="user", name="demonstration-fixture")


def library(tmp_path):
    return Demonstrations(SimpleNamespace(roots={"user": tmp_path / "library"}, _lock=threading.RLock()))


def test_repeat_and_concurrent_add_publish_one_version_without_changing_recorded_bytes(tmp_path):
    demos = library(tmp_path)
    before = content_hash(FIXTURE)
    evidence = recording_evidence(FIXTURE, recorded_task())
    with ThreadPoolExecutor(max_workers=4) as workers:
        values = list(workers.map(lambda _: demos.add(evidence, FIXTURE, ACTOR), range(4)))
    assert len({value["id"] for value in values}) == 1
    assert sum(not value["idempotent"] for value in values) == 1
    assert len(demos._sets()) == 1
    assert len(demos.list(recorded_task()["id"])) == 1
    assert demos.list("task/another-task@1") == []
    frozen = demos.recording(evidence["recordingId"], before)
    assert content_hash(frozen) == before == content_hash(FIXTURE)


def test_new_version_keeps_old_manifest_and_recording_references_immutable(tmp_path):
    demos = library(tmp_path)
    task = recorded_task()
    first = demos.add(recording_evidence(FIXTURE, task), FIXTURE, ACTOR)
    old = demos.show(first["set"]["id"])
    source = tmp_path / "second"
    shutil.copytree(FIXTURE, source)
    path = source / "recording.json"
    metadata = json.loads(path.read_bytes())
    metadata["sessionId"] = "session-another-success"
    path.write_text(json.dumps(metadata), encoding="utf-8")
    second = demos.add(recording_evidence(source, task), source, ACTOR)
    assert second["set"]["id"] == "demoset/recorded-demo-probe@2"
    assert demos.show(first["set"]["id"]) == old
    assert len(demos.show(second["set"]["id"])["demonstrations"]) == 2
    declarations = [{"set": first["set"]["id"], "purpose": "reset"}]
    target = task["task"] | {"sceneContentHash": task["sceneContentHash"]}
    assert demos.resolve_inputs(declarations, target) == [first["set"] | {"purpose": "reset"}]
    shutil.rmtree(source)  # Ordinary retention cannot remove a frozen Demonstration.
    reference = demos.show(second["set"]["id"])["demonstrations"][-1]
    assert demos.recording(reference["recordingId"], reference["recordingContentHash"]).is_dir()
    with pytest.raises(PlanError, match="fixed source Task"):
        demos.add(recording_evidence(FIXTURE, task) | {"task": "task/other@1"}, FIXTURE, ACTOR)


def service(tmp_path, records):
    task = recorded_task()
    calls = []

    def resolve(identifier, relative):
        calls.append(identifier)
        return FIXTURE / relative

    manager = SimpleNamespace(
        library_assets=SimpleNamespace(roots={"user": tmp_path / "library"}, _lock=threading.RLock()),
        sessions={}, evaluations=SimpleNamespace(store=SimpleNamespace(records=records)),
        tasks=SimpleNamespace(show=lambda identifier: task), recordings=SimpleNamespace(resolve=resolve),
    )
    return DemonstrationService(manager), task, calls


@pytest.mark.parametrize("split", ["test", "private", None])
def test_known_heldout_source_cannot_be_laundered_with_task_or_another_train_receipt(tmp_path, split):
    evidence = recording_evidence(FIXTURE, recorded_task())
    episode = {"recordingId": evidence["recordingId"], "success": True,
               "invocationId": evidence["invocationId"]}
    demos, task, calls = service(tmp_path, {
        "eval-train": {"evaluationId": "eval-train", "split": "train", "episodes": [episode]},
        "eval-heldout": {"evaluationId": "eval-heldout", "split": split, "episodes": [episode]},
    })
    with pytest.raises(PlanError) as error:
        demos.add(evidence["recordingId"], task["id"], ACTOR)
    assert error.value.detail["code"] == "demo_private_source"
    assert calls == [] and demos.library.list() == []


def test_source_task_is_explicit_or_inferred_from_frozen_evaluation_and_negative_is_preserved(tmp_path):
    evidence = recording_evidence(FIXTURE, recorded_task())
    demos, task, calls = service(tmp_path, {})
    with pytest.raises(PlanError) as missing:
        demos.add(evidence["recordingId"], None, ACTOR)
    assert missing.value.detail["code"] == "demo_task_required" and calls == []
    identity = {"candidate": "d" * 64}
    record = {
        "evaluationId": "eval-train", "split": "train",
        "episodes": [{"recordingId": evidence["recordingId"], "success": False,
                      "invocationId": evidence["invocationId"]}],
        "notebookContext": {"plan": {"task": task["id"]}, "implementations": identity,
                            "taskContentHash": task["contentHash"]},
        "notebookExecution": identity,
    }
    demos.manager.evaluations.store.records = {"eval-train": record}
    with pytest.raises(PlanError) as negative:
        demos.add(evidence["recordingId"], task["id"], ACTOR)
    assert negative.value.detail["code"] == "demo_not_successful" and calls == []
    record["episodes"][0]["success"] = True
    admitted = demos.add(evidence["recordingId"], None, ACTOR)
    assert admitted["task"] == task["id"]
    assert admitted["sourceProgram"]["implementationHash"] == "d" * 64
    assert demos.add(evidence["recordingId"], None, ACTOR)["idempotent"]


def test_recording_mutated_after_success_validation_cannot_publish_or_satisfy_a_pinned_set(tmp_path):
    demos = library(tmp_path)
    source = tmp_path / "recording"
    shutil.copytree(FIXTURE, source)
    evidence = recording_evidence(source, recorded_task())
    path = source / "recording.json"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(PlanError) as changed:
        demos.add(evidence, source, ACTOR)
    assert changed.value.detail["code"] == "demo_recording_changed" and demos.list() == []
    path.write_bytes(original)
    admitted = demos.add(evidence, source, ACTOR)
    frozen = demos.recording(evidence["recordingId"], evidence["recordingContentHash"])
    (frozen / "recording.json").write_bytes(original + b" ")
    target = recorded_task()["task"] | {"sceneContentHash": recorded_task()["sceneContentHash"]}
    with pytest.raises(PlanError) as corrupted:
        demos.resolve_inputs([{"set": admitted["set"]["id"], "purpose": "reset"}], target)
    assert corrupted.value.detail["code"] == "demo_recording_changed"

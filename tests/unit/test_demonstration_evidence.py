"""A completed recording is not proof of Task success or sufficient provenance."""

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest

from packages.info.log import FILE_HEADER, Record, read_segment
from packages.info.plans import PlanError
from packages.library.common import content_hash
from packages.training.demonstration_evidence import recording_evidence
from tests.fixtures.training_task import task_document

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/recordings/v2"


def recorded_task():
    metadata = json.loads((FIXTURE / "recording.json").read_bytes())
    task = task_document("recorded-demo-probe")
    task["scene"] = metadata["sceneVersion"]
    return {"id": "task/recorded-demo-probe@1", "task": task, "contentHash": "a" * 64,
            "sceneContentHash": metadata["sceneContentHash"]}


def test_existing_real_recording_supplies_success_params_program_and_honest_unknown_code_version():
    before = content_hash(FIXTURE)
    evidence = recording_evidence(FIXTURE, recorded_task())
    assert evidence["sourceProgram"]["id"] == "robot_arm.virtual"
    assert evidence["sourceProgram"]["implementationHash"] is None
    assert evidence["params"] == {"from": "rack.A1", "to": "centrifuge.slot1"}
    assert evidence["successEvidence"]["kind"] == "recorded_task_condition"
    assert evidence["task"] == "task/recorded-demo-probe@1"
    assert content_hash(FIXTURE) == before


@pytest.mark.parametrize("success,code", [
    ({"property": "gripperWidth", "gte": 0.09}, "demo_not_successful"),
    ({"property": "absentObservation", "equals": 1}, "demo_success_unknown"),
])
def test_complete_recording_does_not_imply_success(success, code):
    task = recorded_task()
    task["task"]["success"] = success
    with pytest.raises(PlanError) as error:
        recording_evidence(FIXTURE, task)
    assert error.value.detail["code"] == code


@pytest.mark.parametrize("change,code", [
    ({"status": "incomplete"}, "demo_recording_incomplete"),
    ({"sceneContentHash": None}, "demo_recording_invalid"),
    ({"segments": []}, "demo_episode_ambiguous"),
])
def test_incomplete_or_missing_identity_is_rejected(tmp_path, change, code):
    target = tmp_path / "recording"
    shutil.copytree(FIXTURE, target)
    path = target / "recording.json"
    metadata = json.loads(path.read_bytes())
    path.write_text(json.dumps(metadata | deepcopy(change)), encoding="utf-8")
    with pytest.raises(PlanError) as error:
        recording_evidence(target, recorded_task())
    assert error.value.detail["code"] == code


def test_format_one_remains_readable_but_missing_scene_hash_cannot_be_invented():
    from packages.info.recording import recording_metadata

    old = FIXTURE.parent / "v1"
    metadata = recording_metadata(json.loads((old / "recording.json").read_bytes()))
    assert metadata["formatVersion"] == 1 and metadata["sceneContentHash"] is None
    task = recorded_task()
    task["task"]["scene"] = metadata["sceneVersion"]
    with pytest.raises(PlanError) as error:
        recording_evidence(old, task)
    assert error.value.detail["code"] == "demo_scene_mismatch"


@pytest.mark.parametrize("mutation,code", [
    ("gap", "demo_observation_gap"), ("epoch", "demo_episode_ambiguous"),
    ("multiple", "demo_episode_ambiguous"), ("missing", "demo_episode_ambiguous"),
    ("truncated", "demo_recording_incomplete"),
])
def test_ambiguous_or_incomplete_episode_evidence_is_not_training_data(tmp_path, mutation, code):
    target = tmp_path / "recording"
    shutil.copytree(FIXTURE, target)
    path = target / "recording.json"
    metadata = json.loads(path.read_bytes())
    segment = target / metadata["segments"][0]["file"]
    records = read_segment(segment.read_bytes()).records
    if mutation == "truncated":
        segment.write_bytes(segment.read_bytes()[:-1])
    else:
        if mutation == "gap":
            records.append(Record(3, records[-1].t_ms, b'{"count":1}'))
        elif mutation == "epoch":
            value = json.loads(records[1].payload)
            value["epoch"] += 1
            records.append(Record(2, records[-1].t_ms, json.dumps(value).encode()))
        elif mutation == "multiple":
            value = next(
                json.loads(record.payload) for record in reversed(records)
                if record.kind == 2 and json.loads(record.payload).get("type") == "invocation.update"
            )
            value["invocationId"] += "-second"
            records.append(Record(2, records[-1].t_ms, json.dumps(value).encode()))
        else:
            retained = []
            for record in records:
                if record.kind == 2:
                    value = json.loads(record.payload)
                    if value.get("type") == "invocation.update":
                        continue
                    if value.get("type") == "entity.snapshot":
                        value["invocations"] = []
                        record = Record(2, record.t_ms, json.dumps(value).encode())
                retained.append(record)
            records = retained
        payload = FILE_HEADER + b"".join(record.encode() for record in records)
        segment.write_bytes(payload)
        metadata["segments"][0]["bytes"] = len(payload)
        path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(PlanError) as error:
        recording_evidence(target, recorded_task())
    assert error.value.detail["code"] == code

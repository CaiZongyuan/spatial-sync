"""Controlled Task hook proves capture provenance mechanics, never real training quality."""

import hashlib
import importlib.util
import json
from types import SimpleNamespace

import pytest

from apps.node.observation_capture import capture_observations, code_hash


def controlled_hook(tmp_path, monkeypatch, *, duplicate=False):
    package = tmp_path / "task"
    package.mkdir()
    module_file = package / "sampler.py"
    module_file.write_text(
        "class Environment:\n"
        "    def lw_policy_observation_samples(self, count):\n"
        "        return {'method': 'controlled independent arithmetic fixture', 'samples': [\n"
        "            {'sourceStateId': 'state-' + str(" + ("0" if duplicate else "i") + "),\n"
        "             'state': {'joints': {'/World/Lab/RobotA/fr3/fr3_joint1': {'q': i/25600, 'qd': 0}}},\n"
        "             'trainingObservation': [i/12800], 'previousAction': [0]}\n"
        "            for i in range(count)]}\n",
        encoding="utf-8",
    )
    (package / "obs_spec.yaml").write_text("controlled observation file\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("controlled_task_sampler", module_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("LW_TASK_PACKAGE", str(package))
    runner = SimpleNamespace(env=SimpleNamespace(unwrapped=module.Environment()))
    job = {
        "jobId": "job-controlled",
        "leaseId": "lease-controlled",
        "task": "task/probe@1",
        "taskContentHash": "a" * 64,
        "scene": "robot-and-tubes@2",
        "sceneContentHash": "b" * 64,
        "seed": 42,
    }
    inputs = {"job": job, "task": {"observation": "obs_spec.yaml"}}
    export = {"checkpointSha256": "c" * 64, "policySha256": "d" * 64}
    versions = {"versions": {"isaaclab": "controlled-fixture", "rsl_rl": "controlled-fixture"}}
    output = tmp_path / "output"
    output.mkdir()
    return runner, output, inputs, export, versions, module_file


def test_owned_capture_binds_actual_hook_source_dispatch_and_sample_bytes(tmp_path, monkeypatch):
    runner, output, inputs, export, versions, module_file = controlled_hook(tmp_path, monkeypatch)
    receipt = capture_observations(runner, output, inputs, export, versions)
    envelope = json.loads((output / receipt["samplesArtifact"]).read_bytes())
    assert receipt["sampleCount"] == len(envelope["samples"]) == 256
    assert len({sample["sourceStateId"] for sample in envelope["samples"]}) == 256
    assert receipt["capture"]["hookStatus"] == "returned"
    assert receipt["capture"]["taskSourceFile"] == "sampler.py"
    assert receipt["capture"]["taskSourceSha256"] == code_hash(module_file)
    assert receipt["capture"]["hookModule"] == "controlled_task_sampler"
    assert receipt["policy"]["onnxSha256"] == export["policySha256"]
    assert receipt["source"]["versions"] == versions
    assert (
        receipt["source"]["attemptId"]
        == "attempt-" + hashlib.sha256(b"job-controlled:lease-controlled").hexdigest()
    )
    assert (
        receipt["samplesSha256"]
        == hashlib.sha256((output / receipt["samplesArtifact"]).read_bytes()).hexdigest()
    )
    assert "qualified" not in receipt and "qualified" not in envelope


def test_legacy_environment_without_hook_keeps_evidence_missing(tmp_path):
    assert capture_observations(SimpleNamespace(), tmp_path, {}, {}, {}) is None
    assert capture_observations(SimpleNamespace(env=SimpleNamespace()), tmp_path, {}, {}, {}) is None
    assert list(tmp_path.iterdir()) == []


def test_duplicate_state_ids_and_foreign_hook_source_do_not_create_capture(tmp_path, monkeypatch):
    args = controlled_hook(tmp_path, monkeypatch, duplicate=True)
    with pytest.raises(ValueError, match="distinct"):
        capture_observations(*args[:5])
    assert not (args[1] / "observations/capture.json").exists()
    other = tmp_path / "foreign-package"
    other.mkdir()
    monkeypatch.setenv("LW_TASK_PACKAGE", str(other))
    with pytest.raises(ValueError, match="frozen Task"):
        capture_observations(*args[:5])

"""Controlled native-call fixtures prove input-boundary mechanics, not actual training quality."""

import json
from types import SimpleNamespace

import numpy as np

from apps.node.native_observation_capture import NativeObservationCapture, source_identity
from apps.node.observation_capture import code_hash


class Tensor:
    def __init__(self, rows):
        self.data = np.asarray(rows, dtype=np.float32)
        self.shape, self.dtype = self.data.shape, self.data.dtype

    def __getitem__(self, indices):
        return Tensor(self.data[indices])

    def detach(self):
        return self

    def to(self, **_):
        return self

    def tolist(self):
        return self.data.tolist()


class Normalizer:
    def __init__(self):
        self.hooks = []

    def register_forward_pre_hook(self, callback):
        self.hooks.append(callback)
        return SimpleNamespace(remove=lambda: self.hooks.remove(callback))

    def __call__(self, value):
        for callback in self.hooks:
            callback(self, (value,))
        return Tensor(value.data * 10)  # This normalization belongs inside the controlled export.

    def state_dict(self):
        return {}


class Actor:
    def __init__(self):
        self.obs_groups, self.obs_normalizer = ["policy"], Normalizer()


class Algorithm:
    def __init__(self):
        self.actor = Actor()

    def get_policy(self):
        return self.actor

    def act(self, observation):
        self.actor.obs_normalizer(observation)
        return Tensor([[0.25]])


class Runner:
    def __init__(self):
        self.alg = Algorithm()

    def export_policy_to_onnx(self, *_):
        raise AssertionError("This controlled test executes no model export")


class Environment:
    def __init__(self):
        self.state = {"joints": {"/World/Joint": {"q": 0.0, "qd": 0.0}}}
        self.supply_vector = False

    def lw_policy_observation_state(self, indices):
        value = {"state": self.state}
        if self.supply_vector:
            value["trainingObservation"] = [999.0]
        return [value for _ in indices]


def setup(tmp_path, monkeypatch):
    runner, env = Runner(), Environment()
    observer = SimpleNamespace(env=env, selected=[0], epochs=[0], physics_count=0, disabled=False)
    env.sim = SimpleNamespace(get_physics_step_count=lambda: observer.physics_count)
    from apps.node import native_observation_capture

    profile = {
        "id": "controlled-native-call-fixture",
        "evidenceMode": "controlled",
        "collectorSha256": code_hash(native_observation_capture.__file__),
        "runner": source_identity(runner),
        "actor": source_identity(runner.alg.actor),
        "act": source_identity(runner.alg.act),
        "exporter": source_identity(runner.export_policy_to_onnx),
        "stateProvider": source_identity(env.lw_policy_observation_state),
        "observationGroups": ["policy"],
        "boundary": "rsl-mlp-pre-normalizer",
        "normalization": "inside-exported-graph",
        "previousAction": "raw-native-algorithm-output",
        "previousActionReset": "zero-on-environment-epoch",
        "inputWidth": 1,
        "actionWidth": 1,
    }
    package = tmp_path / "task"
    package.mkdir()
    (package / "obs_spec.yaml").write_text("controlled input contract\n", encoding="utf-8")
    monkeypatch.setenv("LW_TASK_PACKAGE", str(package))
    inputs = {
        "job": {
            "jobId": "job-controlled",
            "leaseId": "lease-controlled",
            "task": "task/probe@1",
            "taskContentHash": "a" * 64,
            "scene": "robot-and-tubes@2",
            "sceneContentHash": "b" * 64,
            "seed": 42,
        },
        "task": {"observation": "obs_spec.yaml"},
    }
    return runner, env, observer, profile, inputs


def test_copies_actual_pre_normalizer_actor_input_and_excludes_non_action_training_calls(
    tmp_path, monkeypatch
):
    runner, env, observer, profile, inputs = setup(tmp_path, monkeypatch)
    original = runner.alg.act.__func__
    with NativeObservationCapture(runner, observer, profile, inputs) as capture:
        runner.alg.act(Tensor([[0.0]]))  # Actual previous raw action is now known.
        for index in range(1, 257):
            observer.physics_count = index
            env.state["joints"]["/World/Joint"]["q"] = index / 512
            runner.alg.act(Tensor([[index / 256]]))
        runner.alg.actor.obs_normalizer(Tensor([[999.0]]))  # SGD-style call outside alg.act.
    assert capture.error is None and len(capture.samples) == 256
    assert "act" not in vars(runner.alg) and runner.alg.act.__func__ is original
    assert runner.alg.actor.obs_normalizer.hooks == []
    assert capture.samples[0]["trainingObservation"] == [1 / 256]
    assert capture.samples[0]["state"]["joints"]["/World/Joint"]["q"] == 1 / 512
    assert capture.samples[-1]["previousAction"] == [0.25]
    output = tmp_path / "output"
    output.mkdir()
    proof = capture.publish(
        output,
        {"checkpointSha256": "c" * 64, "policySha256": "d" * 64, "afterLearn": True, "iteration": 0},
        {"versions": {"rsl_rl": "controlled"}},
    )
    assert proof["sampleCount"] == len(proof["witnesses"]) == 256
    assert proof["capture"]["runtime"]["normalization"] == "inside-exported-graph"
    assert "qualified" not in json.dumps(proof)


def test_task_returned_actor_vector_and_unreviewed_implementation_never_make_a_native_proof(
    tmp_path, monkeypatch
):
    runner, env, observer, profile, inputs = setup(tmp_path, monkeypatch)
    env.supply_vector = True
    with NativeObservationCapture(runner, observer, profile, inputs) as capture:
        runner.alg.act(Tensor([[0.0]]))
        observer.physics_count = 1
        runner.alg.act(Tensor([[0.0]]))
    assert "cannot supply actor vectors" in capture.error and not capture.samples
    profile["actor"] = profile["actor"] | {"sha256": "f" * 64}
    with NativeObservationCapture(runner, observer, profile, inputs) as unknown:
        assert unknown.error and runner.alg.actor.obs_normalizer.hooks == []


def test_an_action_without_a_step_is_not_a_witness_and_reset_uses_zero_previous_action(tmp_path, monkeypatch):
    runner, env, observer, profile, inputs = setup(tmp_path, monkeypatch)
    with NativeObservationCapture(runner, observer, profile, inputs) as capture:
        runner.alg.act(Tensor([[0.0]]))
        runner.alg.act(Tensor([[0.0]]))
        assert not capture.samples
        observer.physics_count = 1
        env.state["joints"]["/World/Joint"]["q"] = 0.1
        runner.alg.act(Tensor([[0.2]]))
        observer.epochs[0] += 1
        env.state["joints"]["/World/Joint"]["q"] = 0.0
        runner.alg.act(Tensor([[0.0]]))
    assert capture.error is None and len(capture.samples) == 2
    assert capture.samples[0]["previousAction"] == [0.25]
    assert capture.samples[1]["previousAction"] == [0.0]

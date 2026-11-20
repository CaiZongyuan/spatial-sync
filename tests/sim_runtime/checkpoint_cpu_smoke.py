"""Run with the pinned mjlab trainer Python to exercise real CPU PPO checkpoint continuation."""

import json
import os
import sys
import tempfile
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

import onnx
import torch
from mjlab.rl import MjlabOnPolicyRunner
from mjlab.rl.config import RslRlOnPolicyRunnerCfg
from tensordict import TensorDict

from apps.node import bridge_callback, checkpoint_runner, native_observation_capture, observation_capture
from apps.node.checkpoint_runner import capture, restore
from apps.node.environment_observer import EnvironmentObserver
from apps.node.native_observation_capture import source_identity
from packages.training import checkpoint_format
from packages.training.checkpoint_format import digest, read_checkpoint


class FixturePhysics:
    num_envs = 4
    physics_dt = 0.01

    def __init__(self):
        self.common_step_counter = 0
        self._sim_step_counter = 0
        self.state = torch.arange(12, dtype=torch.float32).reshape(4, 3) / 10

    def lw_policy_observation_state(self, indices):
        return [{"state": {"position": self.state[index].tolist()}} for index in indices]

    def reset(self):
        return self.state, {}

    def step(self, actions):
        self.common_step_counter += 1
        self._sim_step_counter += 1
        self.state = self.state * 0.7 + torch.cat((actions.tanh(), actions[:, :1] * 0.2), dim=1)
        done = torch.zeros(4, dtype=torch.bool)
        return self.state, 1 - self.state.square().sum(dim=1), done, done, {}


class VectorFixture:
    device = "cpu"
    num_envs = 4
    num_actions = 2
    cfg = {}
    max_episode_length = 20

    def __init__(self):
        self.unwrapped = FixturePhysics()
        self.episode_length_buf = torch.zeros(self.num_envs, dtype=torch.long)

    def get_observations(self):
        state = self.unwrapped.state
        return TensorDict({"actor": state, "critic": state}, batch_size=[self.num_envs])

    def step(self, actions):
        _, reward, terminated, truncated, extras = self.unwrapped.step(actions)
        return self.get_observations(), reward, (terminated | truncated).to(torch.long), extras


def runner(directory, *, width=8):
    config = RslRlOnPolicyRunnerCfg(
        num_steps_per_env=4,
        save_interval=1,
        logger="tensorboard",
        upload_model=False,
    )
    config.actor.obs_normalization = config.critic.obs_normalization = True
    config.actor.hidden_dims = config.critic.hidden_dims = (width,)
    config.algorithm.num_learning_epochs = 1
    config.algorithm.num_mini_batches = 2
    return MjlabOnPolicyRunner(VectorFixture(), asdict(config), str(directory), device="cpu")


def equal(left, right):
    if isinstance(left, torch.Tensor):
        torch.testing.assert_close(left, right, rtol=0, atol=0)
    elif isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            equal(left[key], right[key])
    elif isinstance(left, (tuple, list)):
        assert len(left) == len(right)
        for a_value, b_value in zip(left, right, strict=True):
            equal(a_value, b_value)
    else:
        assert left == right


def main():
    assert version("rsl-rl-lib") == "5.0.1"
    assert version("mjlab") == "1.3.0"
    assert version("torch").split("+")[0] == "2.9.1"
    torch.set_num_threads(1)
    torch.manual_seed(21)
    identity = {
        "schemaVersion": 1,
        "jobId": "job-test",
        "leaseId": "1",
        "inputsSha256": "a" * 64,
        "runtime": {"backend": "mjlab"},
    }
    with tempfile.TemporaryDirectory() as root:
        a = runner(Path(root) / "a")
        a.learn(num_learning_iterations=2)
        raw = capture(a, identity, iteration=2)
        metadata, _, _, _ = read_checkpoint(raw)
        descriptor = identity | {
            "iteration": 2,
            "tensorSha256": metadata["tensorSha256"],
            "sha256": digest(raw),
            "size": len(raw),
        }

        reject_restore(runner(Path(root) / "wrong-model", width=9), raw, descriptor, "signature")
        b = runner(Path(root) / "b")
        reject_restore(b, raw[:-1], descriptor, "bytes differ")
        _, tensors, _, _ = read_checkpoint(raw)
        wrong_type = tensors.replace(b'"F32"', b'"I32"', 1)
        assert len(wrong_type) == len(tensors) and wrong_type != tensors
        bad_metadata = metadata | {"tensorSha256": digest(wrong_type)}
        bad_payload = checkpoint_format.pack_checkpoint(bad_metadata, wrong_type)
        reject_restore(
            b,
            bad_payload,
            descriptor
            | {
                "tensorSha256": digest(wrong_type),
                "sha256": digest(bad_payload),
                "size": len(bad_payload),
            },
            "signature",
        )
        restore(b, raw, descriptor)
        equal(a.alg.save(), b.alg.save())
        assert b.current_learning_iteration == 2
        assert b.env.unwrapped.common_step_counter == 8
        assert b.alg.actor.obs_normalizer.count.item() == 32
        assert {v["step"].item() for v in b.alg.optimizer.state.values()} == {4}
        assert b.alg.learning_rate == a.alg.learning_rate
        b.learn(num_learning_iterations=1)
        assert b.current_learning_iteration == 2
        assert b.env.unwrapped.common_step_counter == 12
        assert b.alg.actor.obs_normalizer.count.item() == 48
        assert {v["step"].item() for v in b.alg.optimizer.state.values()} == {6}
        assert any(
            not torch.equal(tensor, b.alg.actor.state_dict()[key])
            for key, tensor in a.alg.actor.state_dict().items()
            if ".weight" in key
        )
        print("Real RSL-RL CPU PPO: Adam steps 4 → 6, normalizer count 32 → 48, env steps 8 → 12")
        exercise_bridge(Path(root), a, raw, descriptor)


def reject_restore(target, raw, descriptor, expected):
    try:
        restore(target, raw, descriptor)
    except ValueError as error:
        assert expected in str(error)
    else:
        raise AssertionError(f"Checkpoint expected to fail on {expected}")


def exercise_bridge(root, trained, raw, descriptor):
    import apps.node.view_bridge as view_bridge

    aliases = {
        "lw_checkpoint_runner": checkpoint_runner,
        "lw_checkpoint_format": checkpoint_format,
        "lw_native_observation_capture": native_observation_capture,
        "lw_observation_capture": observation_capture,
        "lw_view_bridge": view_bridge,
    }
    sys.modules.update(aliases)
    source_path = root / "resume.tar"
    source_path.write_bytes(raw)
    (root / "obs.yaml").write_text("portable CPU fixture")
    os.environ["LW_TASK_PACKAGE"] = str(root)
    os.chdir(root)
    task = {"mjlab": {}, "observation": "obs.yaml"}
    job = {
        "jobId": "job-cpu-final",
        "leaseId": "2",
        "task": "task/cpu@1",
        "scene": "scene@1",
        "taskContentHash": "a" * 64,
        "sceneContentHash": "b" * 64,
        "seed": 21,
    }
    base = {
        "schemaVersion": 2,
        "job": job,
        "task": task,
        "checkpointIdentity": {
            "schemaVersion": 1,
            "jobId": job["jobId"],
            "leaseId": job["leaseId"],
            "inputsSha256": "a" * 64,
            "runtime": {"backend": "mjlab"},
        },
        "plan": {"algorithm": {"version": "5.0.1"}, "hyperparameters": {"max_iterations": 4}},
        "hardware": {"device": "cpu"},
    }
    final = runner(root / "final-runner")
    profile = {
        "id": "pinned-cpu-test",
        "evidenceMode": "controlled",
        "inputWidth": 3,
        "actionWidth": 2,
        "collectorSha256": observation_capture.code_hash(native_observation_capture.__file__),
        "observerSha256": observation_capture.code_hash(sys.modules[EnvironmentObserver.__module__].__file__),
        "runner": source_identity(final),
        "actor": source_identity(final.alg.actor),
        "act": source_identity(final.alg.act),
        "exporter": source_identity(final.export_policy_to_onnx),
        "stateProvider": source_identity(final.env.unwrapped.lw_policy_observation_state),
        "observationGroups": ["actor"],
        "environments": [0, 1, 2, 3],
        "boundary": "rsl-mlp-pre-normalizer",
        "normalization": "inside-exported-graph",
        "previousAction": "raw-native-algorithm-output",
        "previousActionReset": "zero-on-environment-epoch",
    }
    manifest = root / "manifest.json"
    os.environ["LW_NODE_INPUTS"] = str(manifest)
    os.environ["LW_NODE_OUTPUT"] = str(root / "final")
    manifest.write_text(
        json.dumps(
            base
            | {
                "job": job | {"kind": "checkpoint_finalize"},
                "nativeObservationProfile": profile,
                "resumeCheckpoint": descriptor,
                "checkpointPath": str(source_path),
            }
        )
    )
    bridge_callback.install()
    bridge_callback.ensure_runner_bridged(type(final))

    def forbidden_update():
        raise AssertionError("finalizer performed a PPO update")

    final.alg.update = forbidden_update
    final.learn(num_learning_iterations=4)
    equal(trained.alg.save(), final.alg.save())
    payload = json.loads((root / "final/observations/native-capture.json").read_bytes())
    assert payload["afterLearn"] is False
    assert payload["finalizedCheckpointSha256"] == descriptor["sha256"]
    assert len({row["sourceStateId"] for row in payload["witnesses"]}) == 256
    assert len({row["stateSha256"] for row in payload["witnesses"]}) == 256
    model = onnx.load(root / "final/policy.onnx")
    onnx.checker.check_model(model)
    assert [dim.dim_value for dim in model.graph.input[0].type.tensor_type.shape.dim] == [1, 3]
    print("Managed bridge finalization: static ONNX [1,3], 256 native states, 0 PPO updates")

    # Restore the original class before installing a second isolated dispatch in this process.
    from rsl_rl.runners import OnPolicyRunner

    raw_learn = OnPolicyRunner.learn.__wrapped__
    OnPolicyRunner.learn = MjlabOnPolicyRunner.learn = raw_learn
    os.environ["LW_NODE_OUTPUT"] = str(root / "regular")
    manifest.write_text(json.dumps(base | {"job": job | {"kind": "training"}}))
    saves = []
    original_publish = checkpoint_runner.publish

    def save(path, owner, identity, iteration):
        saves.append(iteration)
        return original_publish(path, owner, identity, iteration)

    checkpoint_runner.publish = save
    bridge_callback.install()
    bridge_callback.ensure_runner_bridged(MjlabOnPolicyRunner)
    regular = runner(root / "regular-runner")
    regular.cfg["save_interval"] = 2
    regular.learn(num_learning_iterations=4)
    assert saves == [1, 3, 4]
    assert (root / "regular/checkpoint.safetensors").is_file()
    assert not list(root.glob("regular-runner/**/*.pt"))
    print("Managed bridge save_interval=2: completed iterations [1,3,4], final safetensors")

    # Submit the approved total to the bridge. Only iterations after the checkpoint run.
    OnPolicyRunner.learn = MjlabOnPolicyRunner.learn = raw_learn
    resumed_root = root / "resumed"
    resumed_root.mkdir()
    os.chdir(resumed_root)
    os.environ["LW_NODE_OUTPUT"] = str(resumed_root / "output")
    saves.clear()
    manifest.write_text(
        json.dumps(
            base
            | {
                "job": job | {"kind": "training"},
                "resumeCheckpoint": descriptor,
                "checkpointPath": str(source_path),
            }
        )
    )
    bridge_callback.install()
    bridge_callback.ensure_runner_bridged(MjlabOnPolicyRunner)
    resumed = runner(resumed_root / "runner")
    resumed.learn(num_learning_iterations=4)
    assert saves == [3, 4]
    assert {state["step"].item() for state in resumed.alg.optimizer.state.values()} == {8}
    assert resumed.env.unwrapped.common_step_counter == 16
    assert resumed.alg.actor.obs_normalizer.count.item() == 64
    print("Managed bridge resume 2/4: two updates, Adam step 8, normalizer count 64, env steps 16")


if __name__ == "__main__":
    main()

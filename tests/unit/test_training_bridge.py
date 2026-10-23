"""The registration callback exports the learned runner, including a zero-based final checkpoint."""

import hashlib
import json
import sys
import types

import pytest

from apps.node import bridge_callback


def test_registration_installs_after_learn_export_bound_to_exact_final_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_callback, "external_files", lambda policy, output: [])
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "output"
    inputs = tmp_path / "inputs.json"
    inputs.write_text(
        json.dumps(
            {
                "job": {
                    key: key
                    for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "seed")
                },
                "plan": {"algorithm": {"version": "5.4.1"}},
                "hardware": {"device": "cpu", "gpu": None},
            }
        )
    )
    monkeypatch.setenv("LW_NODE_OUTPUT", str(output))
    monkeypatch.setenv("LW_NODE_INPUTS", str(inputs))
    monkeypatch.setattr(
        bridge_callback.importlib.metadata,
        "version",
        lambda name: "5.4.1" if name == "rsl-rl-lib" else "fixture",
    )
    order = []

    class Runner:
        def __init__(self):
            self.weight = 0
            self.current_learning_iteration = 0
            self.logger = types.SimpleNamespace(
                log_dir=str(tmp_path / "logs/run"), rewbuffer=[2], log=lambda **kw: None
            )

        def learn(self):
            order.append("learn")
            self.weight = 7
            directory = tmp_path / "logs/run"
            directory.mkdir(parents=True)
            (directory / "model_0.pt").write_bytes(b"trained actor 7")
            self.logger.log(it=0)

        def export_policy_to_onnx(self, path, filename):
            assert self.weight == 7, "Exporting an initial or different runner must fail this oracle"
            order.append("export")
            (output / filename).write_bytes(b"trained policy 7")

    monkeypatch.setitem(sys.modules, "rsl_rl", types.ModuleType("rsl_rl"))
    module = types.ModuleType("rsl_rl.runners")
    module.OnPolicyRunner = Runner
    monkeypatch.setitem(sys.modules, "rsl_rl.runners", module)
    # The frozen Isaac CLI intersects remaining argv with this return value.
    # None preserves every override; [] would silently remove CPU and preset selection.
    returned = bridge_callback.install()
    assert returned is None and order == []
    overrides = [
        "presets=newton_mjwarp",
        "env.sim.physics.solver_cfg.use_mujoco_cpu=true",
        "agent.device=cpu",
        "agent.num_steps_per_env=16",
        "agent.algorithm.learning_rate=0.001",
    ]
    retained = overrides if returned is None else [argument for argument in overrides if argument in returned]
    assert retained == overrides
    Runner().learn()
    assert order == ["learn", "export"]
    export = json.loads((output / "export.json").read_text())
    assert export["iteration"] == 0 and export["checkpoint"] == "checkpoints/run/model_0.pt"
    assert export["checkpointSha256"] == hashlib.sha256(b"trained actor 7").hexdigest()
    assert export["policySha256"] == hashlib.sha256(b"trained policy 7").hexdigest()


@pytest.mark.parametrize("location", ["policy.onnx.data", "../outside.data", "/outside.data"])
def test_external_policy_data_is_completely_hashed_and_confined(monkeypatch, tmp_path, location):
    class Tensor:
        DESCRIPTOR = types.SimpleNamespace(full_name="onnx.TensorProto")
        data_location = 1
        external_data = [types.SimpleNamespace(key="location", value=location)]

        def ListFields(self):
            return []

    class Model:
        DESCRIPTOR = types.SimpleNamespace(full_name="onnx.ModelProto")

        def ListFields(self):
            return [(types.SimpleNamespace(cpp_type=10, CPPTYPE_MESSAGE=10, is_repeated=True), [Tensor()])]

    module = types.SimpleNamespace(
        load_model=lambda path, load_external_data: Model(), TensorProto=types.SimpleNamespace(EXTERNAL=1)
    )
    monkeypatch.setitem(sys.modules, "onnx", module)
    (tmp_path / "policy.onnx.data").write_bytes(b"complete actor weights")
    if location != "policy.onnx.data":
        with pytest.raises(ValueError, match="safe relative"):
            bridge_callback.external_files(tmp_path / "policy.onnx", tmp_path)
    else:
        result = bridge_callback.external_files(tmp_path / "policy.onnx", tmp_path)
        assert result == [
            {"path": location, "size": 22, "sha256": hashlib.sha256(b"complete actor weights").hexdigest()}
        ]

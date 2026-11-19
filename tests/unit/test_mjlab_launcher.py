"""Exercise the shipped launcher entry point without a training SDK."""

import importlib.metadata
import sys
from types import SimpleNamespace

import pytest

from apps.node.mjlab_launcher import main


@pytest.mark.parametrize("validation", [False, True], ids=["training", "validation"])
@pytest.mark.parametrize("usd", [True, False], ids=["usd", "scene-package"])
def test_launcher_composes_the_dispatched_usd_scene_before_environment_creation(
    monkeypatch, tmp_path, validation, usd,
):
    class EnvironmentConstructed(Exception):
        pass

    original_callback = object()
    composed_callback = object()
    scene = SimpleNamespace(num_envs=1, spec_fn=original_callback)
    env_cfg = SimpleNamespace(scene=scene, sim=SimpleNamespace(mujoco=SimpleNamespace()))
    stage = str(tmp_path / "frozen-scene.usda")

    def configure_mjlab_usd_scene(scene_cfg, source):
        assert scene_cfg is scene and source == stage
        assert scene_cfg.spec_fn is original_callback
        assert scene_cfg.num_envs == 8 and env_cfg.sim.mujoco.timestep == 0.005
        assert env_cfg.decimation == 4
        scene_cfg.spec_fn = composed_callback

    def construct_env(*, cfg, device, render_mode):
        assert cfg.scene.spec_fn is (composed_callback if usd else original_callback)
        assert device == "cpu" and render_mode is None
        raise EnvironmentConstructed

    def noop(*args):
        pass

    for name, module in {
        "torch": SimpleNamespace(manual_seed=noop),
        "mjlab": SimpleNamespace(),
        "mjlab.tasks": SimpleNamespace(),
        "mjlab.envs": SimpleNamespace(ManagerBasedRlEnv=construct_env),
        "mjlab.rl": SimpleNamespace(MjlabOnPolicyRunner=None, RslRlVecEnvWrapper=None),
        "mjlab.tasks.registry": SimpleNamespace(
            list_tasks=lambda: ["Fixture-v0"], load_env_cfg=lambda _: env_cfg,
            load_rl_cfg=lambda _: SimpleNamespace(), load_runner_cls=lambda _: None,
        ),
        "mjlab.utils.os": SimpleNamespace(dump_yaml=noop),
        "mjlab.utils.torch": SimpleNamespace(configure_torch_backends=noop),
        "lw_training_bridge": SimpleNamespace(install=noop, verify_trainer_versions=noop),
        "lw_usd_mjcf": SimpleNamespace(configure_mjlab_usd_scene=configure_mjlab_usd_scene) if usd else None,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **_: ())
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    monkeypatch.delenv("LW_SCENE_STAGE", raising=False)
    if usd:
        monkeypatch.setenv("LW_SCENE_STAGE", stage)
    argv = [
        "--task", "Fixture-v0", "--num_envs", "8", "--max_iterations", "10",
        "--seed", "42", "--device", "cpu", "--experiment_name", "fixture",
        "--external_callback", "lw_training_bridge.install", "--physics_dt", "0.005", "--decimation", "4",
    ]
    if validation:
        inputs = tmp_path / "inputs.json"
        inputs.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("LW_NODE_INPUTS", str(inputs))
        argv += ["--validation-iterations", "3"]

    with pytest.raises(EnvironmentConstructed):
        main(argv)

"""Run the validation profiler against real mjlab CPU substeps and episode recovery."""

import os
import subprocess
import sys
from pathlib import Path


def test_mjlab_validation_rejects_nonfinite_physics_before_automatic_reset(tmp_path):
    import pytest

    trainer = os.environ.get("SPATIAL_MJLAB_TEST_PYTHON")
    if not trainer:
        pytest.importorskip("mjlab")
        trainer = sys.executable
    root = Path(__file__).resolve().parents[2]
    env = os.environ | {
        "PYTHONPATH": str(root), "CUDA_VISIBLE_DEVICES": "", "MUJOCO_GL": "disable",
        "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "PYTHONDONTWRITEBYTECODE": "1",
    }
    script = str(Path(__file__).resolve())
    # The first CPU run can spend over 100 seconds compiling cold Warp kernels and running both cases.
    subprocess.run([trainer, script, str(root)], cwd=tmp_path, env=env, check=True, timeout=240)


def check_validation(root, inject_nan):
    import json
    import time
    from dataclasses import asdict
    from importlib import import_module

    from mjlab.envs import mdp
    from mjlab.managers.termination_manager import TerminationTermCfg
    from mjlab.rl import MjlabOnPolicyRunner, RslRlVecEnvWrapper

    from apps.node import usd_mjcf
    from apps.node.mjlab_launcher import profile_training

    stage = root / "library/scenes/microduck-arena/2/stage.usda"
    packages = {"robot/microduck@1": str(root / "library/assets/robot/microduck/1")}
    os.environ.update(LW_SCENE_STAGE=str(stage), LW_SCENE_PACKAGE=str(stage.parent),
                      LW_ASSET_PACKAGES=json.dumps(packages))
    sys.modules["lw_usd_mjcf"] = usd_mjcf
    sys.path.insert(0, str(root / "library/tasks/microduck-stand-up/1/package"))
    task = import_module("lw_microduck_stand_up.env_cfg")

    started = time.perf_counter()
    cfg = task.make_env_cfg()
    cfg.scene.num_envs = 2
    cfg.episode_length_s = cfg.sim.mujoco.timestep * cfg.decimation
    invalid_envs = []

    def observe_physics(env):
        bad = mdp.nan_detection(env)
        invalid_envs.append(bad.tolist())
        return bad

    cfg.terminations["probe_nonfinite"] = TerminationTermCfg(func=observe_physics)
    verify = usd_mjcf.configure_mjlab_usd_scene(cfg.scene, stage)
    env = task.MicroduckStandUpEnv(cfg, device="cpu", render_mode=None)
    try:
        verify(env.sim.mj_model)
        original = env.sim.step
        substeps = 0

        def inject_after_substep():
            nonlocal substeps
            original()
            substeps += 1
            if inject_nan and substeps == cfg.decimation:
                env.sim.data.qacc[0, 0] = float("nan")

        env.sim.step = inject_after_substep
        policy = task.make_rl_cfg()
        policy.num_steps_per_env = 2
        policy.algorithm.num_learning_epochs = 1
        policy.algorithm.num_mini_batches = 1
        policy.upload_model = False
        wrapped = RslRlVecEnvWrapper(env, clip_actions=policy.clip_actions)
        runner = MjlabOnPolicyRunner(wrapped, asdict(policy), None, device="cpu")
        kwargs = dict(device="cpu", num_envs=2, iterations=3, synchronize=lambda: None, setup_started=started)
        if inject_nan:
            try:
                profile_training(runner, wrapped, **kwargs)
            except ValueError as error:
                assert "non-finite physics" in str(error), error
            else:
                assert [row for row in invalid_envs if any(row)] == [[True, False]]
                raise AssertionError("Preflight accepted non-finite physics erased by auto-reset")
            assert [row for row in invalid_envs if any(row)] == [[True, False]]
            print("Rejected one environment with non-finite qacc even though auto-reset recovered it.")
        else:
            profile = profile_training(runner, wrapped, **kwargs)
            assert profile["numEnvs"] == 2 and len(profile["samplesSeconds"]) == 2
            assert invalid_envs == [[False, False]] * 6
            assert not env.episode_length_buf.any()
            print("Two environments completed three real PPO iterations with regular timeout resets.")
        assert env.sim.step is inject_after_substep
    finally:
        env.close()


if __name__ == "__main__":
    for inject_nan in (False, True):
        check_validation(Path(sys.argv[1]), inject_nan)

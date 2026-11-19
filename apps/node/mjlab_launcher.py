"""Standalone mjlab + rsl_rl PPO training entrypoint for isolated compute-node runs.

Copied into ``bridge/lw_mjlab_launcher.py`` by ``apps/node/runner.py``; keeps zero
imports from ``packages.*`` or ``apps.*`` so isolated trainer environments can run it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import time
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Sequence


def compiler_hash(directory=None, *, prefix=""):
    directory = directory or Path(__file__).parent
    digest = hashlib.sha256()
    for name in ("mjlab_launcher.py", "usd_mjcf.py"):
        digest.update((directory / (prefix + name)).read_bytes())
    return digest.hexdigest()


def build_mjlab_launcher_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lw_mjlab_launcher")
    parser.add_argument("--task", required=True)
    parser.add_argument("--num_envs", type=int, required=True)
    parser.add_argument("--max_iterations", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--logger", choices=("tensorboard",), default="tensorboard")
    parser.add_argument("--experiment_name", required=True)
    parser.add_argument("--external_callback", required=True)
    parser.add_argument("--physics_dt", type=float, default=None)
    parser.add_argument("--decimation", type=int, default=None)
    parser.add_argument("--learning_rate", type=float, default=None)
    parser.add_argument("--num_steps_per_env", type=int, default=None)
    parser.add_argument("--num_learning_epochs", type=int, default=None)
    parser.add_argument("--num_mini_batches", type=int, default=None)
    parser.add_argument("--save_interval", type=int, default=None)
    parser.add_argument("--actor_hidden_dims", default=None)
    parser.add_argument("--critic_hidden_dims", default=None)
    parser.add_argument("--activation", default=None)
    parser.add_argument("--obs_normalization", default=None)
    parser.add_argument("--validation-iterations", type=int, choices=(3,))
    return parser


def parse_mjlab_launcher_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_mjlab_launcher_parser().parse_args(list(argv) if argv is not None else None)


def _invoke_callback(spec: str):
    module_name, sep, attr_name = spec.rpartition(".")
    if not sep or not module_name or not attr_name:
        raise ValueError(f"Invalid external_callback specification: {spec!r}")
    module = importlib.import_module(module_name)
    callback = getattr(module, attr_name)
    callback()
    return module


def _seed_everything(seed: int, device: str) -> None:
    import random

    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device != "cpu" and torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _load_registered_mjlab_tasks(target_task: str) -> None:
    import mjlab.tasks  # noqa: F401
    from mjlab.tasks.registry import list_tasks

    for ep in importlib.metadata.entry_points(group="mjlab.tasks"):
        loaded = ep.load()
        if callable(loaded) and target_task not in list_tasks():
            loaded()
    if target_task not in list_tasks():
        raise ValueError(f"mjlab task {target_task!r} was not registered by any mjlab.tasks entry point")


def profile_training(runner, env, *, device, num_envs, iterations, synchronize, setup_started):
    import torch

    def check_batch(observations, rewards=None):
        if tuple(observations.batch_size) != (num_envs,) or (
            rewards is not None and tuple(rewards.shape) != (num_envs,)
        ):
            raise ValueError("Validation did not step the requested number of environments")
        tensors = list(observations.values(include_nested=True, leaves_only=True))
        if not tensors:
            raise ValueError("Validation received no observations")
        if rewards is not None:
            tensors.append(rewards)
        checks.extend(torch.isfinite(tensor).all() for tensor in tensors)

    def act(observations):
        nonlocal started
        if steps == 0:
            synchronize()
            if not samples:
                check_batch(observations)
                check_finite()
            started = time.perf_counter()
        return original_act(observations)

    def step(actions):
        nonlocal steps
        observations, rewards, dones, extras = original_step(actions)
        check_batch(observations, rewards)
        steps += 1
        return observations, rewards, dones, extras

    def check_finite():
        if checks and not bool(torch.stack(checks).all().item()):
            raise ValueError("Validation produced non-finite observations or rewards")
        checks.clear()

    def optimizer_gradients(optimizer, args, kwargs):
        if not updating:
            return
        gradients = [
            parameter.grad for group in optimizer.param_groups for parameter in group["params"]
            if parameter.grad is not None
        ]
        pending_gradient_steps[optimizer] = bool(gradients)
        update_checks.extend(torch.isfinite(gradient).all() for gradient in gradients)

    def optimizer_step(optimizer, args, kwargs):
        if updating and pending_gradient_steps.pop(optimizer, False):
            optimizer_steps[optimizer] += 1
            update_checks.extend(
                torch.isfinite(parameter).all()
                for group in optimizer.param_groups for parameter in group["params"]
            )

    def update():
        nonlocal updating, updated
        if updated or steps != runner.cfg["num_steps_per_env"] or steps < 1:
            raise ValueError("Validation did not complete a full PPO rollout")
        for optimizer in optimizer_steps:
            optimizer_steps[optimizer] = 0
        updating = True
        try:
            losses = original_update()
        finally:
            updating = False
        if any(count != expected_updates for count in optimizer_steps.values()):
            raise ValueError(
                f"Validation did not complete {expected_updates} gradient-bearing PPO optimizer steps"
            )
        updated = True
        return losses

    def log(**values):
        nonlocal steps, updated
        synchronize()
        elapsed = time.perf_counter() - started
        if not updated:
            raise ValueError("Validation did not complete PPO optimization before logging")
        check_finite()
        if not bool(torch.stack(update_checks).all().item()):
            raise ValueError("Validation produced non-finite PPO gradients or weights")
        update_checks.clear()
        losses = values["loss_dict"]
        if not losses or not all(math.isfinite(loss) for loss in losses.values()):
            raise ValueError("Validation produced invalid PPO losses")
        samples.append(elapsed)
        updated = False
        steps = 0

    alg = runner.alg
    expected_updates = alg.num_learning_epochs * alg.num_mini_batches
    if alg.num_learning_epochs < 1 or alg.num_mini_batches < 1:
        raise ValueError("Validation requires positive PPO optimizer epochs and mini-batches")
    optimizer_steps = {alg.optimizer: 0}
    if rnd_optimizer := getattr(alg, "rnd_optimizer", None):
        optimizer_steps[rnd_optimizer] = 0
    checks, update_checks, samples = [], [], []
    pending_gradient_steps = {}
    updating = updated = False
    steps, started = 0, 0.0
    original_act, original_step = alg.act, env.step
    original_update, original_log = alg.update, runner.logger.log
    with ExitStack() as hooks:
        for optimizer in optimizer_steps:
            hooks.callback(optimizer.register_step_pre_hook(optimizer_gradients).remove)
            hooks.callback(optimizer.register_step_post_hook(optimizer_step).remove)
        try:
            alg.act, env.step, alg.update, runner.logger.log = act, step, update, log
            synchronize()
            setup_seconds = time.perf_counter() - setup_started
            runner.learn(num_learning_iterations=iterations, init_at_random_ep_len=True)
        finally:
            alg.act, env.step, alg.update, runner.logger.log = (
                original_act, original_step, original_update, original_log,
            )
    if len(samples) != iterations or steps:
        raise ValueError("Validation did not complete the bounded PPO iterations")
    return {
        "numEnvs": num_envs, "setupSeconds": setup_seconds, "warmupSeconds": samples[0],
        "samplesSeconds": samples[1:],
        "peakMemoryMb": torch.cuda.max_memory_allocated(device) / (1024 * 1024) if device != "cpu" else None,
    }


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_mjlab_launcher_args(argv)
    device = args.device
    if device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    else:
        os.environ.setdefault("MUJOCO_GL", "egl")
        os.environ.setdefault("MUJOCO_EGL_DEVICE_ID", "0")

    validation = args.validation_iterations is not None
    if validation:
        from lw_training_bridge import verify_trainer_versions

        inputs = json.loads(Path(os.environ["LW_NODE_INPUTS"]).read_text(encoding="utf-8"))
        verify_trainer_versions(inputs, True)
        callback_module = None
    else:
        callback_module = _invoke_callback(args.external_callback)
    setup_started = time.perf_counter()
    _seed_everything(args.seed, device)
    _load_registered_mjlab_tasks(args.task)

    from mjlab.envs import ManagerBasedRlEnv
    from mjlab.rl import MjlabOnPolicyRunner, RslRlVecEnvWrapper
    from mjlab.tasks.registry import load_env_cfg, load_rl_cfg, load_runner_cls
    from mjlab.utils.os import dump_yaml
    from mjlab.utils.torch import configure_torch_backends

    configure_torch_backends()
    env_cfg = load_env_cfg(args.task)
    agent_cfg = load_rl_cfg(args.task)

    env_cfg.scene.num_envs = args.num_envs
    env_cfg.seed = args.seed
    if args.physics_dt is not None:
        env_cfg.sim.mujoco.timestep = args.physics_dt
    if args.decimation is not None:
        env_cfg.decimation = args.decimation

    agent_cfg.seed = args.seed
    agent_cfg.max_iterations = args.max_iterations
    agent_cfg.logger = args.logger
    agent_cfg.upload_model = False
    agent_cfg.experiment_name = args.experiment_name
    if args.num_steps_per_env is not None:
        agent_cfg.num_steps_per_env = args.num_steps_per_env
    if args.save_interval is not None:
        agent_cfg.save_interval = args.save_interval
    if args.learning_rate is not None:
        agent_cfg.algorithm.learning_rate = args.learning_rate
    if args.num_learning_epochs is not None:
        agent_cfg.algorithm.num_learning_epochs = args.num_learning_epochs
    if args.num_mini_batches is not None:
        agent_cfg.algorithm.num_mini_batches = args.num_mini_batches
    if args.actor_hidden_dims is not None:
        agent_cfg.actor.hidden_dims = tuple(int(v) for v in json.loads(args.actor_hidden_dims))
    if args.critic_hidden_dims is not None:
        agent_cfg.critic.hidden_dims = tuple(int(v) for v in json.loads(args.critic_hidden_dims))
    if args.activation is not None:
        agent_cfg.actor.activation = args.activation
        agent_cfg.critic.activation = args.activation
    if args.obs_normalization is not None:
        obs_norm = bool(json.loads(args.obs_normalization))
        agent_cfg.actor.obs_normalization = obs_norm
        agent_cfg.critic.obs_normalization = obs_norm

    stage = os.environ.get("LW_SCENE_STAGE")
    if stage is not None:
        from lw_usd_mjcf import configure_mjlab_usd_scene

        configure_mjlab_usd_scene(env_cfg.scene, stage)

    log_dir = (
        Path("logs")
        / "rsl_rl"
        / agent_cfg.experiment_name
        / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    )
    runner_cls = load_runner_cls(args.task) or MjlabOnPolicyRunner
    ensure_bridged = getattr(callback_module, "ensure_runner_bridged", None)
    if callable(ensure_bridged):
        ensure_bridged(runner_cls)
    env_cls = getattr(env_cfg, "class_type", None) or ManagerBasedRlEnv
    env = env_cls(cfg=env_cfg, device=device, render_mode=None)
    vec_env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
    try:
        agent_dict = asdict(agent_cfg)
        if validation:
            def synchronize():
                if device != "cpu":
                    import torch
                    import warp

                    warp.synchronize_device(device)
                    torch.cuda.synchronize(device)

            runner = runner_cls(vec_env, agent_dict, None, device=device)
            timing = profile_training(
                runner, vec_env, device=device, num_envs=args.num_envs, iterations=args.validation_iterations,
                synchronize=synchronize, setup_started=setup_started,
            )
            (Path(os.environ["LW_NODE_OUTPUT"]) / "validation.json").write_text(
                json.dumps(timing, allow_nan=False), encoding="utf-8"
            )
            return
        dump_yaml(log_dir / "params" / "env.yaml", asdict(env_cfg))
        dump_yaml(log_dir / "params" / "agent.yaml", agent_dict)
        runner = runner_cls(vec_env, agent_dict, str(log_dir), device=device)
        runner.learn(num_learning_iterations=agent_cfg.max_iterations, init_at_random_ep_len=True)
    finally:
        vec_env.close()


if __name__ == "__main__":
    main()

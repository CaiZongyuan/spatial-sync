"""Standalone mjlab + rsl_rl PPO training entrypoint for isolated compute-node runs.

Copied into ``bridge/lw_mjlab_launcher.py`` by ``apps/node/runner.py``; keeps zero
imports from ``packages.*`` or ``apps.*`` so isolated trainer environments can run it.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Sequence


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
    parser.add_argument("--presets", default=None)
    parser.add_argument("--learning_rate", type=float, default=None)
    parser.add_argument("--num_steps_per_env", type=int, default=None)
    parser.add_argument("--num_learning_epochs", type=int, default=None)
    parser.add_argument("--num_mini_batches", type=int, default=None)
    parser.add_argument("--save_interval", type=int, default=None)
    parser.add_argument("--actor_hidden_dims", default=None)
    parser.add_argument("--critic_hidden_dims", default=None)
    parser.add_argument("--activation", default=None)
    parser.add_argument("--obs_normalization", default=None)
    return parser


def parse_mjlab_launcher_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_mjlab_launcher_parser().parse_args(list(argv) if argv is not None else None)


def _invoke_callback(spec: str) -> None:
    module_name, sep, attr_name = spec.rpartition(".")
    if not sep or not module_name or not attr_name:
        raise ValueError(f"Invalid external_callback specification: {spec!r}")
    module = importlib.import_module(module_name)
    callback = getattr(module, attr_name)
    callback()


def _load_registered_mjlab_tasks(target_task: str) -> None:
    import mjlab.tasks  # noqa: F401
    from mjlab.tasks.registry import list_tasks

    for ep in importlib.metadata.entry_points(group="mjlab.tasks"):
        loaded = ep.load()
        if callable(loaded) and target_task not in list_tasks():
            loaded()
    if target_task not in list_tasks():
        raise ValueError(f"mjlab task {target_task!r} was not registered by any mjlab.tasks entry point")


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_mjlab_launcher_args(argv)
    device = args.device
    if device == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    else:
        os.environ.setdefault("MUJOCO_GL", "egl")
        os.environ.setdefault("MUJOCO_EGL_DEVICE_ID", "0")

    _invoke_callback(args.external_callback)
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

    log_dir = (
        Path("logs")
        / "rsl_rl"
        / agent_cfg.experiment_name
        / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    )
    runner_cls = load_runner_cls(args.task) or MjlabOnPolicyRunner
    env_cls = getattr(env_cfg, "class_type", None) or ManagerBasedRlEnv
    env = env_cls(cfg=env_cfg, device=device, render_mode=None)
    vec_env = RslRlVecEnvWrapper(env, clip_actions=agent_cfg.clip_actions)
    try:
        agent_dict = asdict(agent_cfg)
        dump_yaml(log_dir / "params" / "env.yaml", asdict(env_cfg))
        dump_yaml(log_dir / "params" / "agent.yaml", agent_dict)
        runner = runner_cls(vec_env, agent_dict, str(log_dir), device=device)
        runner.learn(num_learning_iterations=agent_cfg.max_iterations, init_at_random_ep_len=True)
    finally:
        vec_env.close()


if __name__ == "__main__":
    main()

"""Standalone Isaac Lab FR3 contact-grasp experiment, outside the platform runtime."""

import sys
from functools import partial


def register() -> list[str]:
    """Register the task through Isaac Lab's external callback entrypoint."""
    import gymnasium as gym

    if "Lab-FR3-Pick-Tube-v0" not in gym.registry:
        gym.register(
            id="Lab-FR3-Pick-Tube-v0",
            entry_point="isaaclab.envs:ManagerBasedRLEnv",
            disable_env_checker=True,
            kwargs={
                "env_cfg_entry_point": "fr3_contact.env_cfg:FR3TubeEnvCfg",
                "rsl_rl_cfg_entry_point": "fr3_contact.env_cfg:FR3PPORunnerCfg",
                "default_agent": "rsl_rl",
            },
        )
    return sys.argv[1:]


def _enable_gradient_checks() -> None:
    """Keep ordinary gradient clipping and fail before an optimizer consumes a nonfinite norm."""
    import torch

    torch.nn.utils.clip_grad_norm_ = partial(torch.nn.utils.clip_grad_norm_, error_if_nonfinite=True)


def register_training() -> list[str]:
    """Register the task and enable nonfinite-gradient rejection in this training process."""
    arguments = register()
    _enable_gradient_checks()
    return arguments

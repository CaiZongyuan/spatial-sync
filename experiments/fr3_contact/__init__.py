"""Standalone Isaac Lab FR3 contact-grasp experiment, outside the platform runtime."""

import sys


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

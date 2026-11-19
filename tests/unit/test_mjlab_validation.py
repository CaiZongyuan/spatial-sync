"""Exercise the profiling instrument without claiming real training performance."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from apps.node.mjlab_launcher import profile_training


@pytest.mark.parametrize("bad_output", [None, "observation", "reward", "loss", "short_rollout"])
def test_profile_runs_full_ppo_iterations_and_rejects_invalid_work(monkeypatch, bad_output):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(isfinite=np.isfinite, stack=np.stack))
    events = []
    num_envs = 8

    def observations(value=1.0):
        return SimpleNamespace(
            batch_size=(num_envs,),
            values=lambda **kwargs: [np.full((num_envs, 2), value)],
        )

    def step(actions):
        assert actions.shape == (num_envs, 2)
        events.append("step")
        obs = observations(float("inf") if bad_output == "observation" else 1.0)
        rewards = np.full((num_envs,), float("nan") if bad_output == "reward" else 1.0)
        return obs, rewards, None, {}

    env = SimpleNamespace(step=step)
    runner = SimpleNamespace(
        cfg={"num_steps_per_env": 2},
        alg=SimpleNamespace(act=lambda obs: np.full((num_envs, 2), 1.0)),
        logger=SimpleNamespace(log=None),
    )

    def learn(*, num_learning_iterations, init_at_random_ep_len):
        assert init_at_random_ep_len
        obs = observations()
        for _ in range(num_learning_iterations):
            for _ in range(1 if bad_output == "short_rollout" else 2):
                obs, *_ = env.step(runner.alg.act(obs))
            events.append("update")
            runner.logger.log(loss_dict={"value": float("inf") if bad_output == "loss" else 0.25})

    runner.learn = learn
    options = {"device": "cpu", "num_envs": num_envs, "iterations": 3,
               "synchronize": lambda: events.append("synchronize"), "setup_started": 0}

    if bad_output:
        with pytest.raises(ValueError, match="non-finite|invalid PPO losses|full PPO rollout"):
            profile_training(runner, env, **options)
        return

    result = profile_training(runner, env, **options)
    assert result["numEnvs"] == 8 and len(result["samplesSeconds"]) == 2
    assert events == ["synchronize"] + ["synchronize", "step", "step", "update", "synchronize"] * 3
    assert result["setupSeconds"] > 0 and result["warmupSeconds"] > 0

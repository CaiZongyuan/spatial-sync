"""Exercise the profiling instrument without claiming real training performance."""

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from apps.node.mjlab_launcher import profile_training


@pytest.mark.parametrize(
    "bad_output", [
        None, "observation", "reward", "loss", "short_rollout", "zero_updates", "partial_update",
        "last_gradient", "last_weight",
    ],
)
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
    pre_hooks, post_hooks = [], []
    class Parameter(np.ndarray):
        grad = np.array(1.0)

    parameter = np.array(1.0).view(Parameter)

    class Optimizer:
        param_groups = [{"params": [parameter]}]

    optimizer = Optimizer()

    def register_hooks(hooks):
        def register(hook):
            hooks.append(hook)
            return SimpleNamespace(remove=lambda: hooks.remove(hook))
        return register

    def optimizer_step(bad_weight):
        for hook in pre_hooks:
            hook(optimizer, (), {})
        if bad_weight:
            parameter[...] = float("nan")
        for hook in post_hooks:
            hook(optimizer, (), {})

    optimizer.register_step_pre_hook = register_hooks(pre_hooks)
    optimizer.register_step_post_hook = register_hooks(post_hooks)

    def update():
        updates = {"zero_updates": 0, "partial_update": 5}.get(bad_output, 6)
        for index in range(updates):
            last_step = events.count("update") == 2 and index == updates - 1
            if last_step and bad_output == "last_gradient":
                parameter.grad = np.array(float("nan"))
            optimizer_step(last_step and bad_output == "last_weight")
        events.append("update")
        return {"value": float("inf") if bad_output == "loss" else 0.25}

    runner = SimpleNamespace(
        cfg={"num_steps_per_env": 2},
        alg=SimpleNamespace(
            act=lambda obs: np.full((num_envs, 2), 1.0),
            optimizer=optimizer, num_learning_epochs=2, num_mini_batches=3, update=update,
        ),
        logger=SimpleNamespace(log=None),
    )

    def learn(*, num_learning_iterations, init_at_random_ep_len):
        assert init_at_random_ep_len
        obs = observations()
        for _ in range(num_learning_iterations):
            for _ in range(1 if bad_output == "short_rollout" else 2):
                obs, *_ = env.step(runner.alg.act(obs))
            runner.logger.log(loss_dict=runner.alg.update())

    runner.learn = learn
    options = {"device": "cpu", "num_envs": num_envs, "iterations": 3,
               "synchronize": lambda: events.append("synchronize"), "setup_started": 0}

    original_act = runner.alg.act
    if bad_output:
        with pytest.raises(ValueError, match="non-finite|invalid PPO losses|full PPO rollout|PPO optimizer"):
            profile_training(runner, env, **options)
    else:
        result = profile_training(runner, env, **options)
        assert result["numEnvs"] == 8 and len(result["samplesSeconds"]) == 2
        assert events == ["synchronize"] + ["synchronize", "step", "step", "update", "synchronize"] * 3
        assert result["setupSeconds"] > 0 and result["warmupSeconds"] > 0
    assert not pre_hooks and not post_hooks
    assert env.step is step and runner.alg.act is original_act and runner.alg.update is update
    assert runner.logger.log is None

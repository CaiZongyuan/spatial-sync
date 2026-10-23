"""Command compatibility with the pinned IsaacLab3 train entrypoint."""

import argparse
import pytest

from apps.node.runner import trainer_arguments
from tests.fixtures.training_task import task_document, training_plan


@pytest.mark.parametrize("preset", ["newton_mjwarp", "newton_kamino"])
def test_stock_isaac3_command_preserves_cpu_solver_agent_and_approved_configuration(preset):
    plan = training_plan(
        hyperparameters={"num_envs": 4, "max_iterations": 1, "device": "cpu", "learning_rate": 0.001}
    )
    task = task_document()
    task["isaaclab"]["presets"] = [preset]
    args = trainer_arguments(
        {
            "plan": plan,
            "task": task,
            "job": {"seed": 42, "jobId": "job-fixture", "executionDevice": "cpu"},
        },
        "lw_training_bridge.install",
    )
    # Snapshot of the stock f754f296 train parser's supported flags. Remaining args are Hydra overrides.
    parser = argparse.ArgumentParser()
    for name in (
        "rl_library",
        "task",
        "num_envs",
        "max_iterations",
        "seed",
        "device",
        "logger",
        "experiment_name",
        "external_callback",
    ):
        parser.add_argument("--" + name)
    assert args[0] == "train"
    known, overrides = parser.parse_known_args(args[1:])
    assert known.device == "cpu" and known.num_envs == "4" and known.max_iterations == "1"
    assert all("=" in argument and not argument.startswith("--") for argument in overrides)
    assert "agent.device=cpu" in overrides
    assert ("env.sim.physics.solver_cfg.use_mujoco_cpu=true" in overrides) == (preset == "newton_mjwarp")
    assert "agent.algorithm.learning_rate=0.001" in overrides
    assert f"presets={preset}" in overrides

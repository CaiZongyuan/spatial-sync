"""CPU counterexamples at the real Warp, manager, wrapper and PPO consumer seams."""

from types import SimpleNamespace

import numpy as np
import pytest
import torch
import warp as wp
from isaaclab.envs.mdp import time_out
from isaaclab.managers import TerminationManager, TerminationTermCfg
from isaaclab_rl.rsl_rl import RslRlVecEnvWrapper
from rsl_rl.algorithms.ppo import PPO
from rsl_rl.storage.rollout_storage import RolloutStorage

from fr3_contact import terms
from fr3_contact.physics_validity import NativeEpisodeObserver
from fr3_contact.test_contract import scene_env
from fr3_contact.validity import RANGE, TUBE_LOST, episode_validity
from fr3_contact.verify_resets import replay_preload


def raw_cpu_worlds():
    names = [f"fr3_joint{i}" for i in range(1, 8)] + ["fr3_finger_joint1", "fr3_finger_joint2"]
    model = SimpleNamespace(
        device=wp.get_device("cpu"),
        world_count=3,
        joint_world=wp.array(np.repeat(np.arange(3), 9), dtype=wp.int32, device="cpu"),
        joint_label=[f"/World/envs/env_{world}/{name}" for world in range(3) for name in names],
        joint_q_start=wp.array(
            [world * 12 + i + 2 for world in range(3) for i in range(9)], dtype=wp.int32, device="cpu"
        ),
        joint_qd_start=wp.array(
            [world * 12 + i + 1 for world in range(3) for i in range(9)], dtype=wp.int32, device="cpu"
        ),
        joint_parent=wp.array(np.repeat([0, 2, 4], 9), dtype=wp.int32, device="cpu"),
        joint_limit_lower=wp.zeros(36, device="cpu"),
        joint_limit_upper=wp.array(np.full(36, 0.04, dtype=np.float32), device="cpu"),
        body_world=wp.array([0, 0, 1, 1, 2, 2], dtype=wp.int32, device="cpu"),
        body_label=[
            f"/World/envs/env_{world}/{name}" for world in range(3) for name in ("fr3_link0", "Vial")
        ],
    )
    poses = np.array(
        [
            [world * 1.5, 0, 0, 0, 0, 0, 1] if role == 0 else [world * 1.5 + 0.45, -0.15, 0.035, 0, 0, 0, 1]
            for world in range(3)
            for role in range(2)
        ],
        dtype=np.float32,
    )
    state = SimpleNamespace(
        joint_q=wp.array(np.full(36, 0.039, dtype=np.float32), device="cpu"),
        joint_qd=wp.zeros(36, device="cpu"),
        body_q=wp.array(poses, dtype=wp.transform, device="cpu"),
        body_qd=wp.zeros(6, dtype=wp.spatial_vector, device="cpu"),
    )
    return NativeEpisodeObserver(model), state


def test_real_warp_eight_outputs_latch_out_and_back_and_invalid_initial_state():
    observer, state = raw_cpu_worlds()
    q = wp.to_torch(state.joint_q)
    q[22] = 0.041  # world1 finger2, through the explicit non-contiguous q map.
    observer.observe(state, output=False)
    assert observer.validity.reasons.tolist() == [0, RANGE, 0]
    q[22] = 0.039
    for output in range(8):
        observer.observe(state, output=False)
        q[10] = 0.052 if output == 3 else 0.039
        observer.observe(state, output=True)
    assert observer.validity.reasons.tolist() == [RANGE, RANGE, 0]
    assert observer.validity.transition_outputs.tolist() == [8, 8, 8]
    assert observer.validity.max_excess[0].item() == pytest.approx(0.012)
    assert q[10].item() == pytest.approx(0.039)
    assert observer.validity.first_sample[1].item() == 0


def test_real_warp_raw_lost_tube_and_selected_reset_preserve_terminal_snapshot():
    observer, state = raw_cpu_worlds()
    wp.to_torch(state.joint_q)[10] = 0.052
    wp.to_torch(state.body_q)[5, 0] = 4.0  # root-relative x=1, not world-origin x.
    observer.observe(state, output=True)
    snapshot = observer.validity.snapshot()
    assert observer.validity.reasons.tolist() == [RANGE, 0, TUBE_LOST]
    observer.validity.reset(torch.tensor([0]))
    assert observer.validity.reasons.tolist() == [0, 0, TUBE_LOST]
    assert snapshot["reasons"].tolist() == [RANGE, 0, TUBE_LOST]
    assert observer.validity.transition_outputs.tolist() == [1, 1, 1]


def failed_manager(*, failure_wins_timeout=True):
    env = scene_env()
    env.sim = SimpleNamespace(is_playing=lambda: True)
    env.max_episode_length = 600
    env.episode_length_buf[:] = 600
    manager = TerminationManager(
        {
            "invalid_episode": TerminationTermCfg(func=terms.EpisodeFailureTerm),
            "success": TerminationTermCfg(func=terms.PlacementHistoryTerm),
            "time_out": TerminationTermCfg(
                func=terms.time_out if failure_wins_timeout else time_out, time_out=True
            ),
        },
        env,
    )
    history = terms._history(env)
    history.grasped[:], history.lifted[:], history.inserted[:], history.stable_count[:] = True, True, True, 9
    episode_validity(env).mark(torch.tensor([RANGE]))
    env.termination_manager = manager
    return env, manager


def test_actual_manager_failure_dominates_simultaneous_success_and_positive_rewards():
    env, manager = failed_manager()
    assert bool(manager.compute()[0])
    assert not bool(manager.get_term("success")[0])
    assert not bool(manager.time_outs[0])
    assert not bool(terms._history(env).success[0])
    assert float(terms.success_bonus(env)[0]) == 0.0
    assert float(terms.held_goal_reward(env)[0]) == 0.0
    terminal = env._fr3_terminal_validity
    manager.reset(torch.tensor([0]))
    assert not bool(episode_validity(env).invalid[0])
    assert terminal["reasons"].tolist() == [RANGE]
    assert not bool(env._fr3_terminal_success[0])


def test_actual_manager_selected_reset_clears_only_live_failure_and_keeps_terminal_evidence():
    env = scene_env()
    env.num_envs = 3
    env.episode_length_buf = torch.zeros(3, dtype=torch.long)
    env.sim = SimpleNamespace(is_playing=lambda: True)
    for item in env.scene.values():
        for name, value in vars(item.data).items():
            if isinstance(value, torch.Tensor):
                setattr(item.data, name, value.repeat(3, *([1] * (value.ndim - 1))))
    for sensor in env.scene.sensors.values():
        sensor.data.force_matrix_w = sensor.data.force_matrix_w.repeat(3, 1, 1, 1)
    manager = TerminationManager(
        {
            "invalid_episode": TerminationTermCfg(func=terms.EpisodeFailureTerm),
            "success": TerminationTermCfg(func=terms.PlacementHistoryTerm),
        },
        env,
    )
    validity = episode_validity(env)
    validity.mark(torch.tensor([RANGE, RANGE, TUBE_LOST]))
    assert manager.compute().tolist() == [True, True, True]
    terminal = env._fr3_terminal_validity
    manager.reset(slice(1, 2))
    assert validity.reasons.tolist() == [RANGE, 0, TUBE_LOST]
    assert terminal["reasons"].tolist() == [RANGE, RANGE, TUBE_LOST]


def actual_ppo_recorded_reward(failure_wins_timeout):
    env, manager = failed_manager(failure_wins_timeout=failure_wins_timeout)
    manager.compute()
    env.cfg = SimpleNamespace(is_finite_horizon=False)
    wrapper = object.__new__(RslRlVecEnvWrapper)
    wrapper.clip_actions, wrapper.num_envs = None, 1
    wrapper.env = SimpleNamespace(
        unwrapped=env,
        step=lambda action: (
            {"policy": torch.zeros(1, 2)},
            torch.zeros(1),
            manager.terminated,
            manager.time_outs,
            {},
        ),
    )
    observations, rewards, dones, extras = wrapper.step(torch.zeros(1, 8))
    recorded = []
    actor = SimpleNamespace(update_normalization=lambda obs: None, reset=lambda done: None)
    algo = object.__new__(PPO)
    algo.actor, algo.critic, algo.rnd, algo.gamma, algo.device = actor, actor, None, 0.99, "cpu"
    algo.transition = RolloutStorage.Transition()
    algo.transition.values = torch.tensor([[10.0]])
    algo.storage = SimpleNamespace(
        add_transition=lambda transition: recorded.append(transition.rewards.clone())
    )
    algo.process_env_step(observations, rewards, dones, extras)
    return recorded[0].item()


def test_actual_pinned_wrapper_and_ppo_reproduce_then_prevent_failure_timeout_bootstrap():
    assert actual_ppo_recorded_reward(False) == pytest.approx(9.9)
    assert actual_ppo_recorded_reward(True) == 0.0


def test_loaded_row_replay_rejects_done_even_when_autoreset_restores_perfect_final_pose():
    class ReplayEnv:
        scene = {
            "robot": SimpleNamespace(data=SimpleNamespace(joint_pos=SimpleNamespace(torch=torch.zeros(2, 9))))
        }
        final_pose = torch.zeros(2, 3)
        step_count = 0

        def step(self, action):
            self.step_count += 1
            done = torch.tensor([self.step_count == 1, False])
            # The failed source row has already autoreset to a perfect pose and clean live state.
            self._fr3_terminal_validity = {
                "reasons": torch.tensor([RANGE if self.step_count == 1 else 0, 0]),
                "max_excess": torch.tensor([0.012 if self.step_count == 1 else 0, 0]),
            }
            return None, None, done, torch.zeros(2, dtype=torch.bool), {}

    env = ReplayEnv()
    traces = replay_preload(env, torch.zeros(2, 9), torch.tensor([0, 128]))
    assert env.step_count == 10
    assert env.final_pose.equal(torch.zeros(2, 3))
    assert traces[0]["uninterrupted"] is False
    assert traces[0]["first_terminal_step"] == 1
    assert traces[0]["first_terminal_invalid"]["reasons"] == RANGE
    assert traces[0]["first_terminal_invalid"]["max_excess"] == pytest.approx(0.012)
    assert traces[1] == {
        "source_row": 128,
        "uninterrupted": True,
        "first_terminal_step": None,
        "first_terminal_invalid": None,
    }

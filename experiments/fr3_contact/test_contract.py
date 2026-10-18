"""Independent counterexamples for the frozen FR3 physical success and exact audit."""

import json
from types import SimpleNamespace

import pytest
import torch
from isaaclab.managers import TerminationManager, TerminationTermCfg
from isaaclab_rl import rsl_rl

from fr3_contact import evaluation, terms
from fr3_contact.progress import ContactGraspProgress
from fr3_contact.reset import HOME, validate_audit_starts, validate_dataset


def scene_env(tube_z=0.035, contact=0.0):
    class Scene(dict):
        env_origins = torch.zeros(1, 3)

    class Robot:
        data = SimpleNamespace(
            joint_pos=torch.full((1, 9), 0.04),
            soft_joint_pos_limits=torch.tensor([[[0.0, 0.04]] * 9]),
        )

        def find_joints(self, name, preserve_order=True):
            assert name == "fr3_finger_joint1"
            return [7], [name]

    quat = torch.tensor([[0.0, 0.0, 0.0, 1.0]])
    tube = SimpleNamespace(
        data=SimpleNamespace(
            root_pos_w=torch.tensor([[0.45, 0.15, tube_z]]),
            root_quat_w=quat,
            root_lin_vel_w=torch.zeros(1, 3),
            root_ang_vel_w=torch.zeros(1, 3),
        )
    )
    rack = SimpleNamespace(
        data=SimpleNamespace(
            root_pos_w=torch.tensor([[0.45, 0.15, 0.02]]),
            root_quat_w=quat,
        )
    )
    scene = Scene(vial=tube, rack=rack, robot=Robot())
    scene.sensors = {
        name: SimpleNamespace(data=SimpleNamespace(force_matrix_w=torch.tensor([[[[force, 0.0, 0.0]]]])))
        for name, force in (
            ("fixed_jaw_contact", contact),
            ("moving_jaw_contact", contact),
            ("vial_rack_contact", 0.0),
        )
    }
    return SimpleNamespace(
        scene=scene,
        num_envs=1,
        device="cpu",
        extras={},
        episode_length_buf=torch.zeros(1, dtype=torch.long),
        step_dt=1 / 30,
    )


def verdicts(env, steps=10):
    oracle = terms.PlacementHistoryTerm(TerminationTermCfg(func=terms.PlacementHistoryTerm), env)
    result = []
    for index in range(steps):
        env.episode_length_buf[:] = index
        result.append(bool(oracle(env)[0]))
    return result


def test_seated_only_cannot_pass_contact_grasp_success():
    env = scene_env()
    assert verdicts(env) == [False] * 10
    assert bool(terms._history(env).placement_success[0])


def test_complete_physical_path_needs_ten_released_resting_steps():
    env = scene_env(tube_z=0.047, contact=3.0)
    env.scene["vial"].data.root_pos_w[:, 1] = -0.15
    oracle = terms.PlacementHistoryTerm(TerminationTermCfg(func=terms.PlacementHistoryTerm), env)
    assert not bool(oracle(env)[0])
    assert not bool(oracle(env)[0])
    env.scene["vial"].data.root_pos_w[:, 2] = 0.10
    assert not bool(oracle(env)[0])
    env.scene["vial"].data.root_pos_w[:] = torch.tensor([[0.45, 0.15, 0.050]])
    assert not bool(oracle(env)[0])
    env.scene["vial"].data.root_pos_w[:, 2] = 0.035
    for name in ("fixed_jaw_contact", "moving_jaw_contact"):
        env.scene.sensors[name].data.force_matrix_w[:] = 0
    for _ in range(9):
        assert not bool(oracle(env)[0])
    env.scene["vial"].data.root_lin_vel_w[:, 0] = 0.1
    assert not bool(oracle(env)[0])
    env.scene["vial"].data.root_lin_vel_w[:] = 0
    for _ in range(9):
        assert not bool(oracle(env)[0])
    assert bool(oracle(env)[0])


def test_curriculum_history_remains_available_but_is_marked_as_seeded():
    env = scene_env()
    env._fr3_reset_phase = torch.tensor([3])
    env._fr3_reset_grasped = torch.tensor([True])
    env._fr3_reset_lifted = torch.tensor([True])
    oracle = terms.PlacementHistoryTerm(TerminationTermCfg(func=terms.PlacementHistoryTerm), env)
    oracle.reset()
    assert not bool(oracle(env)[0])  # First observe insertion after the restored lift.
    for _ in range(9):
        assert not bool(oracle(env)[0])
    assert bool(oracle(env)[0])
    assert not bool(env._fr3_terminal_started_from_home[0])


def test_clearance_and_insertion_before_grasp_do_not_complete_the_path():
    progress = ContactGraspProgress(1, "cpu")
    yes = torch.tensor([True])
    no = ~yes
    step = torch.zeros(1, dtype=torch.long)
    progress.update(no, yes, yes, no, step)
    progress.update(yes, no, no, no, step)
    progress.update(yes, no, no, no, step)
    for _ in range(10):
        assert not bool(progress.update(no, no, no, yes, step)[0])
    assert not bool(progress.lifted[0])
    assert not bool(progress.inserted[0])


def test_sticky_grasp_does_not_reward_a_dropped_tube():
    env = scene_env(tube_z=0.10)
    terms._history(env).grasped[:] = True
    assert float(terms.held_goal_reward(env)[0]) == 0.0


@pytest.mark.parametrize("selector", [None, slice(None), torch.tensor([0])])
def test_actual_manager_reset_supports_full_and_indexed_selection(selector):
    env = scene_env()
    env.sim = SimpleNamespace(is_playing=lambda: True)
    manager = TerminationManager({"success": TerminationTermCfg(func=terms.PlacementHistoryTerm)}, env)
    history = terms._history(env)
    history.grasped[:] = True
    history.placement_success[:] = True
    manager.reset(selector)
    assert not bool(history.grasped[0])
    assert not bool(history.placement_success[0])
    assert not bool(manager.compute()[0])


@pytest.mark.parametrize("selector", [slice(1, 2), torch.tensor([1])])
def test_actual_manager_partial_reset_preserves_other_episode_history(selector):
    env = scene_env()
    env.num_envs = 3
    env.sim = SimpleNamespace(is_playing=lambda: True)
    manager = TerminationManager({"success": TerminationTermCfg(func=terms.PlacementHistoryTerm)}, env)
    history = terms._history(env)
    history.grasped[:] = True
    history.success[:] = True
    manager.reset(selector)
    assert history.grasped.tolist() == [True, False, True]
    assert history.success.tolist() == [True, False, True]


def test_tube_resting_on_rim_is_not_seated():
    # Rack rim worldz .042 + tube half-length .015 gives centerworldz .057.
    assert verdicts(scene_env(tube_z=0.057)) == [False] * 10


def test_held_tube_at_goal_is_not_released():
    assert verdicts(scene_env(contact=0.10)) == [False] * 10


def test_horizontal_tube_cannot_satisfy_upright_success():
    env = scene_env()
    env.scene["vial"].data.root_quat_w[:] = torch.tensor([[0, 2**-0.5, 0, 2**-0.5]])
    assert verdicts(env) == [False] * 10


def test_so101_reset_artifact_is_rejected():
    with pytest.raises(ValueError, match="FR3"):
        validate_dataset({"format": "so101-vial-reset-dataset"})


def audit_home_states():
    joints = torch.tensor([list(HOME) + [0.04, 0.04]]).repeat(128, 1)
    return {
        "phase": torch.zeros(128, dtype=torch.long),
        "joint_position": joints,
        "tube_pose": torch.tensor([[0.45, -0.15, 0.035, 0, 0, 0, 1.0]]).repeat(128, 1),
    }, [f"fr3_joint{i}" for i in range(1, 8)] + ["fr3_finger_joint1", "fr3_finger_joint2"]


def test_audit_accepts_only_128_open_home_starts_away_from_goal():
    states, names = audit_home_states()
    validate_audit_starts(states, names)
    states["tube_pose"][:, 1] = 0.15
    with pytest.raises(ValueError, match="source tabletop"):
        validate_audit_starts(states, names)


def test_audit_rejects_a_small_probe_dataset():
    states, names = audit_home_states()
    states = {key: value[:2] for key, value in states.items()}
    with pytest.raises(ValueError, match="128"):
        validate_audit_starts(states, names)


def test_exact_audit_does_not_count_repeated_fast_environment(monkeypatch, capsys):
    class Wrapper:
        num_envs = 1024

        def __init__(self):
            self.unwrapped = self
            self.iteration = 0
            success = torch.zeros(1024, dtype=torch.bool)
            success[0] = True
            self.termination_manager = SimpleNamespace(
                get_term=lambda name: success if name == "success" else ~success
            )
            self._fr3_terminal_progress = torch.zeros(1024, 4, dtype=torch.bool)
            self._fr3_terminal_progress[0, :3] = True
            self._fr3_terminal_raw_placement = torch.ones(1024, dtype=torch.bool)
            self._fr3_terminal_started_from_home = torch.ones(1024, dtype=torch.bool)
            self._fr3_terminal_max_rack_force = torch.zeros(1024)
            self._fr3_terminal_time_to_success_s = torch.ones(1024)

        def step(self, actions):
            self.iteration += 1
            done = torch.zeros(1024, dtype=torch.bool)
            done[0] = True
            if self.iteration == 3:
                done[:] = True
            return None, None, done, {}

    monkeypatch.setattr(rsl_rl, "RslRlVecEnvWrapper", Wrapper)
    monkeypatch.setattr(evaluation, "EXACT_EVALUATION_ACTIVE", False)
    evaluation._install_episode_counter(1024)
    wrapper = Wrapper()
    wrapper.step(None)
    wrapper.step(None)
    with pytest.raises(SystemExit) as stopped:
        wrapper.step(None)
    assert stopped.value.code == 0
    result = json.loads(capsys.readouterr().out.split("FR3_EVAL_RESULT=")[1])
    assert result["episodes"] == 1024
    assert result["successes"] == 1
    assert result["success_rate"] == 1 / 1024
    assert result["raw_placement_rate"] == 1.0
    assert result["passed_contact_grasp_threshold"] is False

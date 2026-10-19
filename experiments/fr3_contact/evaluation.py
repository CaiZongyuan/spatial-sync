# Adapted from IsaacLabTutorial c64024e, Apache-2.0; see LICENSE.
"""Exact episode accounting for Isaac Lab's ``play`` entrypoint.

Isaac Lab's play loop runs forever. Passing
``--external_callback fr3_contact.evaluation.install_episode_counter`` patches the RSL-RL wrapper.
Play stops after exactly :data:`EVALUATION_EPISODES` episodes, one per environment, and prints one
``FR3_EVAL_RESULT`` JSON line with the outcome statistics.
"""

from __future__ import annotations

import json
import sys

import torch

from .validity import REASONS

EVALUATION_EPISODES = 1024
"""Size of the acceptance audit: the 128 canonical home-pose starts, each played eight times."""

EXACT_EVALUATION_ACTIVE = False
"""Set before the task is constructed so :meth:`play_mode` keeps one environment per audited episode."""


def install_episode_counter() -> list[str]:
    """Stop after 1,024 canonical episodes and print the result."""
    from fr3_contact import register

    register()
    return _install_episode_counter(EVALUATION_EPISODES)


def _install_episode_counter(target: int) -> list[str]:
    """Count each environment's first episode once, then print ``FR3_EVAL_RESULT`` and exit."""
    global EXACT_EVALUATION_ACTIVE

    if target <= 0:
        raise ValueError("Evaluation episode count must be positive")
    EXACT_EVALUATION_ACTIVE = True
    from isaaclab_rl.rsl_rl import RslRlVecEnvWrapper

    original_step = RslRlVecEnvWrapper.step
    counts = dict.fromkeys(
        (
            "episodes",
            "successes",
            "raw_placements",
            "grasped",
            "lifted",
            "inserted",
            "vial_lost",
            "timed_out",
            "unsafe_rack_contact",
        ),
        0,
    )
    sums = {"peak_rack_force": 0.0, "time_to_success": 0.0}
    max_rack_force = 0.0
    counted: torch.Tensor | None = None
    invalid_counts = dict.fromkeys(REASONS, 0)
    invalid_episodes = 0
    max_excess = 0.0

    def counted_step(self, actions):
        nonlocal counted, max_rack_force, invalid_episodes, max_excess
        if self.num_envs != target:
            raise RuntimeError(f"The audit requires one episode per environment: use --num_envs {target}.")
        result = original_step(self, actions)
        dones = result[2].bool()
        if counted is None:
            counted = torch.zeros_like(dones)
        # Count only each environment's first episode while slower peers finish.
        done_ids = dones.nonzero(as_tuple=False).squeeze(-1)
        done_ids = done_ids[~counted[done_ids]]
        if done_ids.numel() == 0:
            return result
        counted[done_ids] = True

        env = self.unwrapped
        terminal = env._fr3_terminal_validity
        reasons = terminal["reasons"][done_ids]
        success = env._fr3_terminal_success[done_ids] & (reasons == 0)
        progress = env._fr3_terminal_progress[done_ids]
        if not bool(env._fr3_terminal_started_from_home[done_ids].all()):
            raise RuntimeError("Audit episodes must start from home without seeded grasp/lift history")
        success = success & progress[:, :3].all(dim=-1)
        peak_force = env._fr3_terminal_max_rack_force[done_ids]
        counts["episodes"] += int(done_ids.numel())
        counts["successes"] += int(success.sum())
        invalid_episodes += int((reasons != 0).sum())
        for name, flag in REASONS.items():
            invalid_counts[name] += int(((reasons & flag) != 0).sum())
        max_excess = max(max_excess, float(terminal["max_excess"][done_ids].max()))
        counts["raw_placements"] += int(env._fr3_terminal_raw_placement[done_ids].sum())
        counts["grasped"] += int(progress[:, 0].sum())
        counts["lifted"] += int(progress[:, 1].sum())
        counts["inserted"] += int(progress[:, 2].sum())
        counts["unsafe_rack_contact"] += int(progress[:, 3].sum())
        counts["vial_lost"] += int(env.termination_manager.get_term("vial_lost")[done_ids].sum())
        counts["timed_out"] += int(env.termination_manager.get_term("time_out")[done_ids].sum())
        sums["peak_rack_force"] += float(peak_force.sum())
        sums["time_to_success"] += float(env._fr3_terminal_time_to_success_s[done_ids][success].sum())
        max_rack_force = max(max_rack_force, float(peak_force.max()))

        if counts["episodes"] >= target:
            episodes = counts["episodes"]
            successes = counts["successes"]
            summary = {
                "episodes": episodes,
                "successes": successes,
                "invalid_episodes": invalid_episodes,
                "invalid_by_reason": invalid_counts,
                "raw_max_joint_excess_m_or_rad": max_excess,
                "success_rate": successes / episodes,
                "contact_grasp_successes": successes,
                "contact_grasp_success_rate": successes / episodes,
                "raw_placement_rate": counts["raw_placements"] / episodes,
                "home_unseeded_episodes": episodes,
                "passed_contact_grasp_threshold": successes / episodes >= 0.90,
                "grasp_rate": counts["grasped"] / episodes,
                "lift_rate": counts["lifted"] / episodes,
                "insertion_rate": counts["inserted"] / episodes,
                "vial_lost_rate": counts["vial_lost"] / episodes,
                "timeout_rate": counts["timed_out"] / episodes,
                "unsafe_rack_contact_rate": counts["unsafe_rack_contact"] / episodes,
                "mean_peak_rack_contact_force_n": sums["peak_rack_force"] / episodes,
                "max_rack_contact_force_n": max_rack_force,
                "mean_time_to_success_s": sums["time_to_success"] / successes if successes else None,
            }
            print("FR3_EVAL_RESULT=" + json.dumps(summary, sort_keys=True), flush=True)
            raise SystemExit(0)
        return result

    RslRlVecEnvWrapper.step = counted_step
    return sys.argv[1:]

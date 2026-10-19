"""Physically replay one demonstrated reset row per phase with its stored drive preload."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import torch
from isaaclab.app import add_launcher_args, launch_simulation

from . import terms
from .env_cfg import FR3TubeEnvCfg
from .environment import FR3ContactEnv
from .reset import PHASES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    add_launcher_args(parser)
    args = parser.parse_args()
    cfg = FR3TubeEnvCfg()
    cfg.scene.num_envs = len(PHASES)
    cfg.sim.device = args.device
    cfg.seed = 42
    cfg.events.reset_from_dataset.params["dataset_path"] = str(args.dataset.resolve())
    records = []
    with launch_simulation(cfg, args) as physics_cfg:
        cfg.sim.physics = physics_cfg
        env = FR3ContactEnv(cfg)
        try:
            with torch.inference_mode():
                env.reset()
                reset_term = env.event_manager.get_term_cfg("reset_from_dataset").func
                rows = torch.stack(
                    [
                        (reset_term.states["phase"] == phase).nonzero().flatten()[0]
                        for phase in range(len(PHASES))
                    ]
                )
                ids = torch.arange(env.num_envs, device=env.device)
                reset_term.write_rows(env, ids, rows)
                env.termination_manager.reset()
                env.reward_manager.reset()
                env.action_manager.reset()
                history = terms._history(env)
                phases = torch.arange(len(PHASES), device=env.device)
                if not torch.equal(history.grasped, phases >= 2) or not torch.equal(
                    history.lifted, phases >= 3
                ):
                    raise AssertionError("Loaded curriculum history did not reach the termination manager")
                target = reset_term.states["joint_target"][rows]
                robot = env.scene["robot"]
                expected_pose = reset_term.states["tube_pose"][rows]
                for _ in range(10):
                    measured = robot.data.joint_pos.torch
                    action = torch.cat(
                        (
                            (target[:, :7] - measured[:, :7]) / 0.033,
                            (target[:, 7:8] - measured[:, 7:8]) / 0.003,
                        ),
                        dim=-1,
                    ).clamp(-1, 1)
                    env.step(action)
                tube_position = env.scene["vial"].data.root_pos_w.torch - env.scene.env_origins
                drift = torch.linalg.vector_norm(tube_position - expected_pose[:, :3], dim=-1)
                contact = terms.bilateral_contact(env)
                local, alignment, speed, angular_speed, released, placed = terms._placement_values(env)
                finite = torch.isfinite(robot.data.joint_pos.torch).all(dim=-1)
                finite &= torch.isfinite(tube_position).all(dim=-1)
                for phase, name in enumerate(PHASES):
                    held_phase = 2 <= phase <= 6
                    valid = bool(finite[phase]) and float(drift[phase]) < 0.01
                    if held_phase:
                        valid &= bool(contact[phase])
                    if phase == 7:
                        valid &= bool(released[phase] & placed[phase])
                        valid &= float(alignment[phase]) > 0.90
                        valid &= float(speed[phase]) < 0.06 and float(angular_speed[phase]) < 0.8
                    records.append(
                        {
                            "phase": name,
                            "source_row": int(rows[phase]),
                            "valid": valid,
                            "tube_position_m": tube_position[phase].tolist(),
                            "pose_drift_m": float(drift[phase]),
                            "bilateral_contact": bool(contact[phase]),
                            "rack_local_center_m": local[phase].tolist(),
                            "raw_placement": bool(history.placement_success[phase]),
                            "contact_grasp_success": bool(history.success[phase]),
                            "started_from_home": bool(env._fr3_terminal_started_from_home[phase]),
                        }
                    )
        finally:
            env.close()
    report = {
        "dataset": str(args.dataset.resolve()),
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "steps_per_loaded_row": 10,
        "rows": records,
        "passed": all(row["valid"] for row in records),
        "scope": "representative reset replay with stored preload; not a learned policy audit",
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("FR3_RESET_REPLAY=" + json.dumps(report), flush=True)
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

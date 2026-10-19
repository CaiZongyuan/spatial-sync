"""Execute connected physical grasp/place trajectories and admit only validated reset rows."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("PXR_WORK_THREAD_LIMIT", "1")

import torch
from isaaclab.app import add_launcher_args, launch_simulation
from isaaclab.utils.math import quat_apply

from . import terms
from .env_cfg import TCP_OFFSET, FR3GeneratorEnvCfg
from .environment import FR3ContactEnv
from .reset import PHASES, validate_dataset
from .validity import REASONS


class ProbeComplete(Exception):
    """A requested diagnostic stage finished without admitting a dataset."""


def generate(env, quota: int, max_batches: int, probe_until: str | None = None) -> tuple[dict, list[dict]]:
    """Move only through robot targets after reset; never move the tube directly."""
    robot = env.scene["robot"]
    tube = env.scene["vial"]
    body_id = robot.find_bodies("fr3_link7")[0][0]
    rows = {"joint_position": [], "joint_target": [], "tube_pose": [], "phase": []}
    diagnostics = []
    accepted = 0
    # FR3's hand is authored at -45 degrees to link7; align its closing axis with rack Y.
    closed_quat = torch.tensor((0.9238795, -0.3826834, 0.0, 0.0), device=env.device).expand(env.num_envs, 4)

    def tcp_pose():
        quaternion = robot.data.body_quat_w.torch[:, body_id]
        position = robot.data.body_pos_w.torch[:, body_id] + quat_apply(
            quaternion, quaternion.new_tensor(TCP_OFFSET).expand(env.num_envs, -1)
        )
        return position - env.scene.env_origins, quaternion

    def move(target_position, jaw, steps, target_quaternion=closed_quat):
        start_pos, start_quat = (value.clone() for value in tcp_pose())
        jaw_target = robot.data.joint_pos.torch[:, -2:-1].clone()
        desired_jaw = 0.039 if jaw > 0 else 0.0
        for step in range(steps):
            fraction = min(1.0, (step + 1) / max(1, steps - 15))
            position = torch.lerp(start_pos, target_position, fraction)
            quaternion = torch.lerp(start_quat, target_quaternion, fraction)
            quaternion = quaternion / torch.linalg.vector_norm(quaternion, dim=-1, keepdim=True)
            jaw_target += (desired_jaw - jaw_target).clamp(-0.0005, 0.0005)
            command = torch.cat((position, quaternion, jaw_target), dim=-1)
            env.step(command)

    def capture(phase):
        pose = torch.cat((tube.data.root_pos_w.torch, tube.data.root_quat_w.torch), dim=-1).clone()
        pose[:, :3] -= env.scene.env_origins
        print(
            "FR3_PHASE="
            + json.dumps(
                {
                    "phase": PHASES[phase],
                    "raw_invalid_by_reason": {
                        name: int(((env._fr3_validity.reasons & flag) != 0).sum())
                        for name, flag in REASONS.items()
                    },
                    "raw_max_excess": float(env._fr3_validity.max_excess.max()),
                    "raw_first_sample_min": int(env._fr3_validity.first_sample.min()),
                    "raw_first_sample_max": int(env._fr3_validity.first_sample.max()),
                    "raw_max_joint_speed": float(env._fr3_validity.max_speed.max()),
                    "tcp_mean_m": tcp_pose()[0].mean(0).tolist(),
                    "tube_mean_m": pose[:, :3].mean(0).tolist(),
                    "bilateral_contacts": int(terms.bilateral_contact(env).sum()),
                    "max_abs_joint_velocity": float(robot.data.joint_vel.torch.abs().max()),
                    "finger_positions_m": robot.data.joint_pos.torch[:, -2:].tolist(),
                    "fixed_contact_force_n": terms._contact_magnitude(env, "fixed_jaw_contact").tolist(),
                    "moving_contact_force_n": terms._contact_magnitude(env, "moving_jaw_contact").tolist(),
                }
            ),
            flush=True,
        )
        if PHASES[phase] == probe_until:
            raise ProbeComplete(PHASES[phase])
        return {
            "joint_position": robot.data.joint_pos.torch.clone(),
            "joint_target": robot.data.joint_pos_target.torch.clone(),
            "tube_pose": pose,
            "phase": torch.full((env.num_envs,), phase, dtype=torch.long, device=env.device),
        }

    with torch.inference_mode():
        for batch in range(max_batches):
            env.reset()
            home_pos, home_quat = tcp_pose()
            move(home_pos.clone(), 1.0, 30, home_quat.clone())
            initial_tube = tube.data.root_pos_w.torch.clone() - env.scene.env_origins
            captured = [capture(0)]
            approach = initial_tube.clone()
            approach[:, 2] += 0.10
            move(approach, 1.0, 100)
            move(initial_tube, 1.0, 90)
            captured.append(capture(1))
            move(initial_tube, -1.0, 60)
            proof = initial_tube.clone()
            proof[:, 2] += 0.012
            move(proof, -1.0, 60)
            grasped = terms.bilateral_contact(env) & (terms.vial_height(env) > 0.041)
            captured.append(capture(2))
            lifted_target = initial_tube.clone()
            lifted_target[:, 2] += 0.10
            move(lifted_target, -1.0, 100)
            lifted = terms.bilateral_contact(env) & (
                terms.vial_lowest_height_in_rack(env) > terms.RACK_CLEARANCE_HEIGHT
            )
            env._fr3_validity.lifted.copy_(lifted)
            captured.append(capture(3))
            # Axially symmetric upright tubes need no roll; hold the proved grasp before transport.
            move(lifted_target, -1.0, 25)
            captured.append(capture(4))
            rack = env.scene["rack"].data.root_pos_w.torch - env.scene.env_origins
            above_rack = rack.clone()
            above_rack[:, 2] += 0.115
            move(above_rack, -1.0, 140)
            transported = terms.bilateral_contact(env)
            captured.append(capture(5))
            insert = rack.clone()
            insert[:, 2] += 0.015
            move(insert, -1.0, 120)
            inserted = terms.vial_inserted(env)
            captured.append(capture(6))
            move(insert, 1.0, 60)
            stable_steps = torch.zeros(env.num_envs, dtype=torch.long, device=env.device)
            for _ in range(10):
                move(insert, 1.0, 1)
                local, alignment, speed, angular_speed, released, placed = terms._placement_values(env)
                resting = released & placed & (alignment > 0.90) & (speed < 0.06) & (angular_speed < 0.8)
                stable_steps = torch.where(resting, stable_steps + 1, torch.zeros_like(stable_steps))
            seated = stable_steps >= 10
            captured.append(capture(7))
            valid = grasped & lifted & transported & inserted & seated & ~env._fr3_validity.invalid
            available = valid.nonzero().flatten()[: quota - accepted]
            count = len(available)
            for state in captured:
                for key in rows:
                    rows[key].append(state[key][available].cpu())
            accepted += count
            record = {
                "batch": batch,
                "environments": env.num_envs,
                "grasped": int(grasped.sum()),
                "lifted": int(lifted.sum()),
                "transported": int(transported.sum()),
                "inserted": int(inserted.sum()),
                "seated": int(seated.sum()),
                "admitted_connected": count,
                "accepted_total": accepted,
                "final_tube_local_mean": local.mean(0).tolist(),
                "tcp_local_mean": tcp_pose()[0].mean(0).tolist(),
                "joint_names": robot.joint_names,
                "joint_position_mean": robot.data.joint_pos.torch.mean(0).tolist(),
            }
            diagnostics.append(record)
            print("FR3_RESET_BATCH=" + json.dumps(record), flush=True)
            if accepted >= quota:
                artifact = {
                    "format": "fr3-contact-reset-v1",
                    "phase_names": PHASES,
                    "joint_names": robot.joint_names,
                    "states": {key: torch.cat(parts) for key, parts in rows.items()},
                    "validation": {
                        "connected_physical_trajectory": True,
                        "object_state_writes_after_reset": False,
                        "physics": "newton_mjwarp",
                        "canonical_starts": accepted,
                        "episode_validity": "raw-episode-v1",
                        "batch_diagnostics": diagnostics,
                    },
                }
                validate_dataset(artifact)
                return artifact, diagnostics
    raise RuntimeError(f"Only {accepted}/{quota} complete physical trajectories; diagnostics={diagnostics}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--num_envs", type=int, default=128)
    parser.add_argument("--poses_per_phase", type=int, default=128)
    parser.add_argument("--max_batches", type=int, default=4)
    parser.add_argument("--probe_until", choices=PHASES)
    add_launcher_args(parser)
    args = parser.parse_args()
    cfg = FR3GeneratorEnvCfg()
    cfg.scene.num_envs = args.num_envs
    cfg.sim.device = args.device
    cfg.seed = 42
    started = time.perf_counter()
    with launch_simulation(cfg, args) as physics_cfg:
        cfg.sim.physics = physics_cfg
        env = FR3ContactEnv(cfg)
        try:
            artifact, diagnostics = generate(env, args.poses_per_phase, args.max_batches, args.probe_until)
        except ProbeComplete as completed:
            print(
                "FR3_PROBE_RESULT="
                + json.dumps(
                    {
                        "stopped_after": str(completed),
                        "dataset_written": False,
                        "elapsed_s": time.perf_counter() - started,
                    }
                ),
                flush=True,
            )
            return
        finally:
            env.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(args.output)
    torch.save(artifact, args.output)
    print(
        "FR3_RESET_RESULT="
        + json.dumps(
            {
                "output": str(args.output.resolve()),
                "rows": len(artifact["states"]["phase"]),
                "elapsed_s": time.perf_counter() - started,
                "batches": diagnostics,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()

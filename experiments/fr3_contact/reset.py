"""FR3-specific reset replay; object state is written only at reset."""

from __future__ import annotations

from pathlib import Path

import torch
from isaaclab.managers import EventTermCfg, ManagerTermBase

from .validity import episode_validity

PHASES = ("home", "pregrasp", "grasp", "lift", "reorient", "transport", "insert", "release")
HOME = (0.0, -0.4, 0.0, -2.0, 0.0, 1.6, 0.7)


def validate_dataset(artifact: dict) -> dict[str, torch.Tensor]:
    """Reject SO-101 data, malformed rows, and missing physical validation evidence."""
    if artifact.get("format") != "fr3-contact-reset-v1" or artifact.get("phase_names") != PHASES:
        raise ValueError("Expected an FR3 contact-reset dataset with the declared eight phases")
    states = artifact["states"]
    if states["phase"].dtype != torch.long:
        raise ValueError("Reset phase IDs must be int64")
    count = states["phase"].numel()
    shapes = {"joint_position": (count, 9), "joint_target": (count, 9), "tube_pose": (count, 7)}
    if count == 0 or states["phase"].shape != (count,):
        raise ValueError("Reset phases must be a nonempty vector")
    for key, shape in shapes.items():
        if states[key].shape != shape or not torch.isfinite(states[key]).all():
            raise ValueError(f"Invalid finite reset tensor {key}: expected {shape}")
    if set(states["phase"].tolist()) != set(range(len(PHASES))):
        raise ValueError("Every physical phase must have reset rows")
    phase_counts = torch.bincount(states["phase"])
    if not torch.all(phase_counts == phase_counts[0]):
        raise ValueError("Training reset phases must have equal quotas")
    norms = torch.linalg.vector_norm(states["tube_pose"][:, 3:], dim=-1)
    if not torch.allclose(norms, torch.ones_like(norms), atol=0.002, rtol=0):
        raise ValueError("Reset tube quaternions must be normalized XYZW")
    if not artifact.get("validation", {}).get("connected_physical_trajectory"):
        raise ValueError(
            "Reset rows require a connected physical trajectory, not independently teleported grasps"
        )
    if artifact.get("validation", {}).get("episode_validity") != "raw-episode-v1":
        raise ValueError("Reset data must come from raw-valid connected physical trajectories")
    return states


def validate_audit_starts(states: dict[str, torch.Tensor], joint_names: list[str]) -> None:
    """Admit exactly 128 unheld home starts on the source tabletop, never a curriculum phase."""
    home = states["phase"] == 0
    if int(home.sum()) != 128:
        raise ValueError("The 1024-episode audit requires 128 canonical home starts, repeated eight times")
    joints = states["joint_position"][home]
    arm_ids = [joint_names.index(f"fr3_joint{i}") for i in range(1, 8)]
    finger_ids = [joint_names.index(f"fr3_finger_joint{i}") for i in (1, 2)]
    if not torch.all((joints[:, arm_ids] - joints.new_tensor(HOME)).abs() < 0.02):
        raise ValueError("Audit starts must use the FR3 home arm pose")
    if not torch.all((joints[:, finger_ids] >= 0.035) & (joints[:, finger_ids] <= 0.04)):
        raise ValueError("Audit starts must have open fingers")
    pose = states["tube_pose"][home]
    if not torch.all((pose[:, :2] - pose.new_tensor((0.45, -0.15))).abs() <= 0.007):
        raise ValueError("Audit tubes must start on the source tabletop, away from the goal")
    if not torch.all((pose[:, 2] - 0.035).abs() <= 0.001):
        raise ValueError("Audit tubes must rest on the source tabletop")
    if not torch.all(1.0 - 2.0 * pose[:, 3:5].square().sum(-1) > 0.999):
        raise ValueError("Audit tubes must start upright")


def clear_progress(env, env_ids) -> None:
    """Initialize milestone history from an ordinary home reset."""
    if not hasattr(env, "_fr3_reset_phase"):
        env._fr3_reset_phase = torch.zeros(env.num_envs, dtype=torch.long, device=env.device)
        env._fr3_reset_grasped = torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
        env._fr3_reset_lifted = torch.zeros(env.num_envs, dtype=torch.bool, device=env.device)
    env._fr3_reset_phase[env_ids] = 0
    env._fr3_reset_grasped[env_ids] = False
    env._fr3_reset_lifted[env_ids] = False
    episode_validity(env).reset(env_ids)


class ResetFromDataset(ManagerTermBase):
    """Sample all phases for training; repeat canonical home rows for the exact audit."""

    def __init__(self, cfg: EventTermCfg, env):
        super().__init__(cfg, env)
        artifact = torch.load(Path(cfg.params["dataset_path"]), map_location="cpu", weights_only=True)
        states = validate_dataset(artifact)
        from . import evaluation

        if evaluation.EXACT_EVALUATION_ACTIVE:
            validate_audit_starts(states, artifact["joint_names"])
        if artifact["joint_names"] != env.scene["robot"].joint_names:
            raise ValueError("Reset joint order differs from the converted FR3 articulation")
        limits = env.scene["robot"].data.joint_pos_limits.torch[0].cpu()
        positions = states["joint_position"]
        if not torch.all((positions >= limits[:, 0]) & (positions <= limits[:, 1])):
            raise ValueError("Reset states violate the original model joint ranges")
        self.states = {key: value.to(env.device) for key, value in states.items()}
        self._cursor = 0

    def __call__(self, env, env_ids, dataset_path: str, sequential: bool = False) -> None:
        del dataset_path
        ids = torch.as_tensor(env_ids, device=env.device, dtype=torch.long)
        if sequential:
            home = (self.states["phase"] == 0).nonzero().flatten()
            rows = home[(torch.arange(len(ids), device=env.device) + self._cursor) % len(home)]
            self._cursor += len(ids)
        else:
            # The generator writes equal quotas for all eight phases.
            rows = torch.randint(len(self.states["phase"]), (len(ids),), device=env.device)
        self.write_rows(env, ids, rows)

    def write_rows(self, env, ids: torch.Tensor, rows: torch.Tensor) -> None:
        """Replay explicit rows at reset; also used by the representative-row physics check."""
        robot = env.scene["robot"]
        position = self.states["joint_position"][rows]
        robot.write_joint_position_to_sim_index(position=position, env_ids=ids)
        robot.write_joint_velocity_to_sim_index(velocity=torch.zeros_like(position), env_ids=ids)
        robot.set_joint_position_target_index(target=self.states["joint_target"][rows], env_ids=ids)
        tube_pose = self.states["tube_pose"][rows].clone()
        tube_pose[:, :3] += env.scene.env_origins[ids]
        env.scene["vial"].write_root_pose_to_sim_index(root_pose=tube_pose, env_ids=ids)
        env.scene["vial"].write_root_velocity_to_sim_index(
            root_velocity=torch.zeros((len(ids), 6), device=env.device), env_ids=ids
        )
        clear_progress(env, ids)
        phase = self.states["phase"][rows]
        env._fr3_reset_phase[ids] = phase
        env._fr3_reset_grasped[ids] = phase >= 2
        env._fr3_reset_lifted[ids] = phase >= 3

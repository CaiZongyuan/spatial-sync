"""USD-derived mjlab forward bow and upright return task for Microduck bow."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from typing import Any

import numpy as np
import torch

from mjlab.actuator import XmlActuatorCfg
from mjlab.entity import EntityArticulationInfoCfg, EntityCfg
from mjlab.envs import ManagerBasedRlEnv, ManagerBasedRlEnvCfg
from mjlab.envs import mdp as envs_mdp
from mjlab.envs.mdp import dr
from mjlab.envs.mdp.actions import JointPositionActionCfg
from mjlab.managers.command_manager import CommandTerm, CommandTermCfg
from mjlab.managers.event_manager import EventTermCfg
from mjlab.managers.observation_manager import ObservationGroupCfg, ObservationTermCfg
from mjlab.managers.reward_manager import RewardTermCfg
from mjlab.managers.scene_entity_config import SceneEntityCfg
from mjlab.managers.termination_manager import TerminationTermCfg
from mjlab.rl import MjlabOnPolicyRunner, RslRlModelCfg, RslRlOnPolicyRunnerCfg, RslRlPpoAlgorithmCfg
from mjlab.scene import SceneCfg
from mjlab.sensor import ContactMatch, ContactSensorCfg
from mjlab.sim import MujocoCfg, SimulationCfg
from mjlab.tasks.registry import list_tasks, register_mjlab_task
from mjlab.tasks.velocity import mdp as vel_mdp
from mjlab.terrains import TerrainEntityCfg
from mjlab.viewer import ViewerConfig

import lw_usd_mjcf

TASK_ID = "Lab-Microduck-Bow-v0"
ROBOT_PRIM = "/World/Lab/Microduck"
BASE_BODY_KEY = "/World/Lab/Microduck/trunk_base"
ACTION_SCALE = 0.35
STAND_Z = 0.1156


class MicroduckBowEnv(ManagerBasedRlEnv):
    """ManagerBasedRlEnv with native physical state provider for qualification."""

    def lw_policy_observation_state(self, indices: list[int]) -> list[dict[str, Any]]:
        robot = self.scene["robot"]
        joint_names = list(robot.joint_names)
        pos_all = robot.data.root_link_pos_w
        quat_all = robot.data.root_link_quat_w
        lin_all = robot.data.root_link_lin_vel_w
        ang_all = robot.data.root_link_ang_vel_w
        jpos_all = robot.data.joint_pos
        jvel_all = robot.data.joint_vel
        cmd_all = self.command_manager.get_command("bow_pitch")

        rows: list[dict[str, Any]] = []
        for env_idx in indices:
            pos = [float(v) for v in pos_all[env_idx].detach().cpu().tolist()]
            qwxyz = np.asarray(quat_all[env_idx].detach().cpu().tolist(), dtype=np.float64)
            qnorm = float(np.linalg.norm(qwxyz))
            if not math.isfinite(qnorm) or qnorm < 1e-8:
                qw, qx, qy, qz = 1.0, 0.0, 0.0, 0.0
            else:
                qw, qx, qy, qz = (qwxyz / qnorm).tolist()
            lin_v = [float(v) for v in lin_all[env_idx].detach().cpu().tolist()]
            ang_w = [float(v) for v in ang_all[env_idx].detach().cpu().tolist()]
            jq = jpos_all[env_idx].detach().cpu().tolist()
            jqd = jvel_all[env_idx].detach().cpu().tolist()
            cmd = [float(v) for v in cmd_all[env_idx].detach().cpu().tolist()]
            joints = {
                f"{ROBOT_PRIM}/joints/{name}": {"q": float(q_val), "qd": float(qd_val)}
                for name, q_val, qd_val in zip(joint_names, jq, jqd, strict=True)
            }
            rows.append(
                {
                    "state": {
                        "bodies": {
                            BASE_BODY_KEY: {
                                "p": pos,
                                "q": [float(qx), float(qy), float(qz), float(qw)],
                                "v": lin_v,
                                "w": ang_w,
                            }
                        },
                        "joints": joints,
                        "sites": {},
                    },
                    "command": cmd,
                }
            )
        return rows


class BowPitchCommand(CommandTerm):
    """1D forward pitch command matching BipedPolicyProgram settle -> bow -> upright return."""

    cfg: BowPitchCommandCfg

    def __init__(self, cfg: BowPitchCommandCfg, env: ManagerBasedRlEnv):
        super().__init__(cfg, env)
        self.pitch_cmd = torch.zeros(self.num_envs, 1, device=self.device)
        self.target_bow = torch.full((self.num_envs, 1), 0.314159, device=self.device)
        self.start_steps = torch.zeros((self.num_envs,), dtype=torch.long, device=self.device)
        self.end_steps = torch.full((self.num_envs,), 50, dtype=torch.long, device=self.device)

    @property
    def command(self) -> torch.Tensor:
        return self.pitch_cmd

    def _update_metrics(self) -> None:
        pass

    def _resample_command(self, env_ids: torch.Tensor) -> None:
        n = len(env_ids)
        if n == 0:
            return
        bow_rad = torch.empty(n, 1, device=self.device).uniform_(0.26, 0.36)
        # Cover both immediate start (0s) and pre-action floor settling (0.05..0.50s)
        delay_s = torch.where(
            torch.rand(n, device=self.device) < 0.25,
            torch.zeros(n, device=self.device),
            torch.empty(n, device=self.device).uniform_(0.02, 0.50),
        )
        hold_s = torch.empty(n, device=self.device).uniform_(0.65, 1.50)
        s_steps = torch.clamp((delay_s / self._env.step_dt).long(), min=0)
        h_steps = torch.clamp((hold_s / self._env.step_dt).long(), min=20)
        self.target_bow[env_ids] = bow_rad
        self.start_steps[env_ids] = s_steps
        self.end_steps[env_ids] = s_steps + h_steps
        ep_step = self._env.episode_length_buf[env_ids]
        in_bow = ((ep_step >= s_steps) & (ep_step < s_steps + h_steps)).unsqueeze(-1)
        self.pitch_cmd[env_ids] = torch.where(in_bow, bow_rad, torch.zeros_like(bow_rad))

    def _update_command(self) -> None:
        ep_step = self._env.episode_length_buf
        in_bow = ((ep_step >= self.start_steps) & (ep_step < self.end_steps)).unsqueeze(-1)
        self.pitch_cmd[:] = torch.where(in_bow, self.target_bow, torch.zeros_like(self.target_bow))


@dataclass(kw_only=True)
class BowPitchCommandCfg(CommandTermCfg):
    resampling_time_range: tuple[float, float] = (10.0, 12.0)

    def build(self, env: ManagerBasedRlEnv) -> BowPitchCommand:
        return BowPitchCommand(self, env)


def _safe(tensor: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(tensor, nan=0.0, posinf=0.0, neginf=0.0)


def _pitch_and_roll_rad(quat_wxyz: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    qw, qx, qy, qz = quat_wxyz.unbind(-1)
    sin_pitch = torch.clamp(2.0 * (qw * qy - qz * qx), -0.999999, 0.999999)
    pitch = torch.asin(sin_pitch)
    roll = torch.atan2(2.0 * (qw * qx + qy * qz), 1.0 - 2.0 * (qx * qx + qy * qy))
    return pitch, roll


def robot_state_is_nan(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    bad = torch.isnan(robot.data.joint_pos).any(dim=-1)
    bad = bad | torch.isnan(robot.data.joint_vel).any(dim=-1)
    bad = bad | torch.isnan(robot.data.root_link_pos_w).any(dim=-1)
    bad = bad | torch.isnan(robot.data.root_link_quat_w).any(dim=-1)
    return bad


def fell_or_collapsed(env: ManagerBasedRlEnv, max_tilt_rad: float = 0.72, min_z: float = 0.086) -> torch.Tensor:
    robot = env.scene["robot"]
    gz = robot.data.projected_gravity_b[:, 2]
    z = robot.data.root_link_pos_w[:, 2]
    return ((-gz) < math.cos(max_tilt_rad)) | (z < min_z)


def track_bow_pitch(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    cmd = env.command_manager.get_command("bow_pitch")[:, 0]
    pitch, _ = _pitch_and_roll_rad(robot.data.root_link_quat_w)
    # Natural STAND2 resting pitch is -0.04 rad (-2.3 deg), well inside |pitchDeg| < 9 deg.
    desired = torch.where(cmd > 0.05, cmd, torch.full_like(cmd, -0.04))
    err = pitch - desired
    return _safe(
        2.2 * torch.exp(-torch.square(err) / (0.055 * 0.055))
        + 0.8 * torch.exp(-torch.square(err) / (0.18 * 0.18))
    )


def level_roll_and_yaw_rate(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    _, roll = _pitch_and_roll_rad(robot.data.root_link_quat_w)
    wz = robot.data.root_link_ang_vel_b[:, 2]
    wx = robot.data.root_link_ang_vel_b[:, 0]
    return _safe(torch.exp(-torch.square(roll) / (0.065 * 0.065)) * torch.exp(-(wz * wz + 0.6 * wx * wx) / 0.12))


def planted_in_place(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    sensor = env.scene.sensors["feet_ground_contact"]
    vxy = torch.sum(torch.square(robot.data.root_link_lin_vel_b[:, :2]), dim=-1)
    both_feet = (torch.sum((sensor.data.found > 0).float(), dim=-1) >= 2.0).float()
    return _safe(0.65 * torch.exp(-vxy / 0.008) + 0.35 * both_feet)


def bow_height_reward(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    cmd = env.command_manager.get_command("bow_pitch")[:, 0]
    z = robot.data.root_link_pos_w[:, 2]
    target_z = torch.where(cmd > 0.05, torch.full_like(z, 0.109), torch.full_like(z, STAND_Z))
    return _safe(torch.exp(-torch.square(z - target_z) / (0.014 * 0.014)))


def pitch_rate_damping(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    wy = robot.data.root_link_ang_vel_b[:, 1]
    return _safe(-torch.square(wy))


def upright_return_and_symmetry(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    cmd = env.command_manager.get_command("bow_pitch")[:, 0]
    q = robot.data.joint_pos
    q0 = robot.data.default_joint_pos
    diff = q - q0
    is_upright = (cmd <= 0.05).float()
    is_bow = 1.0 - is_upright

    wy = robot.data.root_link_ang_vel_b[:, 1]
    upright_score = torch.exp(-torch.mean(torch.square(diff), dim=-1) / 0.020) * torch.exp(-(wy * wy) / 0.18)

    # During bow: keep hip_yaw, hip_roll, head_yaw, head_roll, and knees near default (bow via hip_pitch + neck/head_pitch + ankle balance!)
    lat_and_knee = [0, 1, 3, 7, 8, 9, 10, 12]
    lat_err = torch.mean(torch.square(diff[:, lat_and_knee]), dim=-1)
    sym_err = (
        torch.square(diff[:, 2] + diff[:, 11])
        + torch.square(diff[:, 3] + diff[:, 12])
        + torch.square(diff[:, 4] + diff[:, 13])
    ) / 3.0
    bow_score = torch.exp(-(5.0 * lat_err + 3.0 * sym_err))
    return _safe(1.6 * is_upright * upright_score + 1.0 * is_bow * bow_score)


def foot_contact_forces_obs(env: ManagerBasedRlEnv) -> torch.Tensor:
    sensor = env.scene.sensors["feet_ground_contact"]
    forces = sensor.data.force
    if forces is None:
        return torch.zeros(env.num_envs, 6, device=env.device)
    return _safe(forces.reshape(env.num_envs, -1) * 0.1)


def make_env_cfg() -> ManagerBasedRlEnvCfg:
    stage_path = os.environ.get("LW_SCENE_STAGE")
    if not stage_path:
        raise RuntimeError("LW_SCENE_STAGE must point to the pinned USD scene stage")
    derived = lw_usd_mjcf.derive_usd_mjcf(
        stage_path,
        prim_path=ROBOT_PRIM,
        include_ground_plane=False,
        include_static_colliders=False,
    )
    joint_names = tuple(j.name for j in derived.joints)
    joint_defaults = {j.name: float(j.default_position) for j in derived.joints}
    joint_clips = {j.name: (float(j.lower), float(j.upper)) for j in derived.joints}

    robot_cfg = EntityCfg(
        init_state=EntityCfg.InitialStateCfg(
            pos=(0.0, 0.0, 0.1178),
            rot=(0.999045, 0.0, -0.043699, 0.0),
            joint_pos=joint_defaults,
            joint_vel={".*": 0.0},
        ),
        collisions=(),
        spec_fn=lambda: derived.to_mjspec(),
        articulation=EntityArticulationInfoCfg(
            actuators=(XmlActuatorCfg(target_names_expr=(r".*",)),),
            soft_joint_pos_limit_factor=0.95,
        ),
    )

    feet_ground_cfg = ContactSensorCfg(
        name="feet_ground_contact",
        primary=ContactMatch(
            mode="geom",
            pattern=r"^(ankle_left_collision_0|ankle_right_collision_0)$",
            entity="robot",
        ),
        secondary=ContactMatch(mode="body", pattern="terrain"),
        fields=("found", "force"),
        reduce="netforce",
        num_slots=1,
        track_air_time=False,
    )
    self_collision_cfg = ContactSensorCfg(
        name="self_collision",
        primary=ContactMatch(mode="subtree", pattern="trunk_base", entity="robot"),
        secondary=ContactMatch(mode="subtree", pattern="trunk_base", entity="robot"),
        fields=("found",),
        reduce="none",
        num_slots=1,
    )

    ordered_joints = SceneEntityCfg("robot", joint_names=joint_names, preserve_order=True)

    actor_terms = {
        "command": ObservationTermCfg(
            func=envs_mdp.generated_commands,
            params={"command_name": "bow_pitch"},
        ),
        "base_ang_vel": ObservationTermCfg(func=envs_mdp.base_ang_vel),
        "projected_gravity": ObservationTermCfg(func=envs_mdp.projected_gravity),
        "joint_pos": ObservationTermCfg(
            func=envs_mdp.joint_pos_rel,
            params={"biased": False, "asset_cfg": ordered_joints},
        ),
        "joint_vel": ObservationTermCfg(
            func=envs_mdp.joint_vel_rel,
            params={"asset_cfg": ordered_joints},
        ),
        "previous_action": ObservationTermCfg(func=envs_mdp.last_action),
    }
    critic_terms = {
        **actor_terms,
        "base_lin_vel": ObservationTermCfg(func=envs_mdp.base_lin_vel),
        "foot_forces": ObservationTermCfg(func=foot_contact_forces_obs),
    }

    observations = {
        "actor": ObservationGroupCfg(
            terms=actor_terms,
            concatenate_terms=True,
            enable_corruption=False,
        ),
        "critic": ObservationGroupCfg(
            terms=critic_terms,
            concatenate_terms=True,
            enable_corruption=False,
        ),
    }

    actions = {
        "joint_pos": JointPositionActionCfg(
            entity_name="robot",
            actuator_names=joint_names,
            scale=ACTION_SCALE,
            use_default_offset=True,
            preserve_order=True,
            clip=joint_clips,
        )
    }

    commands = {
        "bow_pitch": BowPitchCommandCfg(resampling_time_range=(10.0, 12.0)),
    }

    events = {
        "reset_base": EventTermCfg(
            func=envs_mdp.reset_root_state_uniform,
            mode="reset",
            params={
                "pose_range": {
                    "x": (-0.15, 0.15),
                    "y": (-0.15, 0.15),
                    "z": (-0.004, 0.004),
                    "roll": (-0.08, 0.08),
                    "pitch": (-0.10, 0.10),
                    "yaw": (-math.pi, math.pi),
                },
                "velocity_range": {
                    "x": (-0.08, 0.08),
                    "y": (-0.08, 0.08),
                    "z": (-0.05, 0.05),
                    "roll": (-0.35, 0.35),
                    "pitch": (-0.35, 0.35),
                    "yaw": (-0.35, 0.35),
                },
            },
        ),
        "reset_joints": EventTermCfg(
            func=envs_mdp.reset_joints_by_offset,
            mode="reset",
            params={
                "position_range": (-0.08, 0.08),
                "velocity_range": (-0.55, 0.55),
            },
        ),
        "foot_friction": EventTermCfg(
            func=dr.geom_friction,
            mode="reset",
            params={
                "asset_cfg": SceneEntityCfg(
                    "robot", geom_names=("ankle_left_collision_0", "ankle_right_collision_0")
                ),
                "operation": "abs",
                "ranges": (0.75, 1.25),
            },
        ),
        "trunk_com": EventTermCfg(
            func=dr.body_ipos,
            mode="reset",
            params={
                "asset_cfg": SceneEntityCfg("robot", body_names=("trunk_base",)),
                "operation": "add",
                "ranges": (-0.003, 0.003),
            },
        ),
    }

    rewards = {
        "track_bow": RewardTermCfg(func=track_bow_pitch, weight=2.0),
        "level_roll_yaw": RewardTermCfg(func=level_roll_and_yaw_rate, weight=1.5),
        "planted": RewardTermCfg(func=planted_in_place, weight=1.8),
        "height": RewardTermCfg(func=bow_height_reward, weight=2.0),
        "return_and_sym": RewardTermCfg(func=upright_return_and_symmetry, weight=2.2),
        "pitch_damping": RewardTermCfg(func=pitch_rate_damping, weight=0.08),
        "dof_pos_limits": RewardTermCfg(func=vel_mdp.joint_pos_limits, weight=-1.0),
        "self_collisions": RewardTermCfg(
            func=vel_mdp.self_collision_cost,
            weight=-1.0,
            params={"sensor_name": "self_collision"},
        ),
        "action_rate_l2": RewardTermCfg(func=vel_mdp.action_rate_l2, weight=-0.20),
        "termination_penalty": RewardTermCfg(func=vel_mdp.is_terminated, weight=-50.0),
    }

    terminations = {
        "time_out": TerminationTermCfg(func=vel_mdp.time_out, time_out=True),
        "fell_over": TerminationTermCfg(func=fell_or_collapsed, time_out=False),
        "nan_state": TerminationTermCfg(func=robot_state_is_nan, time_out=False),
    }

    cfg = ManagerBasedRlEnvCfg(
        scene=SceneCfg(
            terrain=TerrainEntityCfg(terrain_type="plane"),
            entities={"robot": robot_cfg},
            sensors=(feet_ground_cfg, self_collision_cfg),
            num_envs=2048,
            env_spacing=1.5,
        ),
        observations=observations,
        actions=actions,
        commands=commands,
        events=events,
        rewards=rewards,
        terminations=terminations,
        curriculum={},
        metrics={},
        viewer=ViewerConfig(
            origin_type=ViewerConfig.OriginType.ASSET_BODY,
            entity_name="robot",
            body_name="trunk_base",
            distance=0.8,
            elevation=-15.0,
            azimuth=90.0,
        ),
        sim=SimulationCfg(
            nconmax=120,
            njmax=350,
            mujoco=MujocoCfg(
                timestep=0.002,
                integrator="implicitfast",
                cone="pyramidal",
                iterations=20,
                ls_iterations=10,
            ),
        ),
        decimation=10,
        episode_length_s=5.0,
    )
    cfg.class_type = MicroduckBowEnv
    return cfg


def make_rl_cfg() -> RslRlOnPolicyRunnerCfg:
    return RslRlOnPolicyRunnerCfg(
        actor=RslRlModelCfg(
            hidden_dims=(256, 128),
            activation="elu",
            obs_normalization=True,
            distribution_cfg=dict(
                class_name="GaussianDistribution",
                init_std=1.0,
                std_type="scalar",
            ),
        ),
        critic=RslRlModelCfg(
            hidden_dims=(256, 128),
            activation="elu",
            obs_normalization=True,
        ),
        algorithm=RslRlPpoAlgorithmCfg(
            value_loss_coef=1.0,
            use_clipped_value_loss=True,
            clip_param=0.2,
            entropy_coef=0.008,
            num_learning_epochs=5,
            num_mini_batches=4,
            learning_rate=3.0e-4,
            schedule="adaptive",
            gamma=0.99,
            lam=0.95,
            desired_kl=0.01,
            max_grad_norm=1.0,
        ),
        experiment_name="microduck_bow",
        save_interval=200,
        num_steps_per_env=24,
        max_iterations=600,
        clip_actions=None,
        logger="tensorboard",
        upload_model=False,
    )


def register() -> None:
    if TASK_ID in list_tasks():
        return
    register_mjlab_task(
        task_id=TASK_ID,
        env_cfg=make_env_cfg(),
        play_env_cfg=make_env_cfg(),
        rl_cfg=make_rl_cfg(),
        runner_cls=MjlabOnPolicyRunner,
    )

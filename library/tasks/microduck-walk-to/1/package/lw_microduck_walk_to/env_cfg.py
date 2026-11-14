"""USD-derived mjlab velocity locomotion task for Microduck walkTo."""

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

TASK_ID = "Lab-Microduck-WalkTo-v0"
ROBOT_PRIM = "/World/Lab/Microduck"
BASE_BODY_KEY = "/World/Lab/Microduck/trunk_base"
ACTION_SCALE = 1.0
STAND_Z = 0.1156


class MicroduckWalkToEnv(ManagerBasedRlEnv):
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
        cmd_all = self.command_manager.get_command("twist")

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


class OmnidirectionalWalkCommand(CommandTerm):
    """Planar twist command covering standing, pure yaw, cardinal, and omnidirectional walks."""

    cfg: OmnidirectionalWalkCommandCfg

    def __init__(self, cfg: OmnidirectionalWalkCommandCfg, env: ManagerBasedRlEnv):
        super().__init__(cfg, env)
        self.vel_command_b = torch.zeros(self.num_envs, 3, device=self.device)

    @property
    def command(self) -> torch.Tensor:
        return self.vel_command_b

    def _update_metrics(self) -> None:
        pass

    def _resample_command(self, env_ids: torch.Tensor) -> None:
        n = len(env_ids)
        if n == 0:
            return
        u = torch.rand(n, device=self.device)
        cmd = torch.zeros(n, 3, device=self.device)

        # 4% exact zero stand-still (variable_posture std_standing teaches calm dwell without survival bias)
        stand_mask = u < 0.04
        # 18% pure spin-in-place (turn_left / turn_right / inspect_turn / final yaw alignment)
        spin_mask = (u >= 0.04) & (u < 0.22)
        # 33% axial + gentle turning [vx, vy, wz]
        axial_mask = (u >= 0.22) & (u < 0.55)
        # 45% full omnidirectional + turning [vx, vy, wz]
        omni_mask = u >= 0.55

        if spin_mask.any():
            m = int(spin_mask.sum().item())
            mag = torch.empty(m, device=self.device).uniform_(0.20, 1.05)
            sign = torch.where(torch.rand(m, device=self.device) < 0.5, -1.0, 1.0)
            cmd[spin_mask, 2] = mag * sign

        if axial_mask.any():
            m = int(axial_mask.sum().item())
            cmd[axial_mask, 0] = torch.empty(m, device=self.device).uniform_(-0.26, 0.32)
            cmd[axial_mask, 1] = torch.empty(m, device=self.device).uniform_(-0.06, 0.06)
            cmd[axial_mask, 2] = torch.empty(m, device=self.device).uniform_(-0.60, 0.60)

        if omni_mask.any():
            m = int(omni_mask.sum().item())
            cmd[omni_mask, 0] = torch.empty(m, device=self.device).uniform_(-0.26, 0.32)
            cmd[omni_mask, 1] = torch.empty(m, device=self.device).uniform_(-0.22, 0.22)
            cmd[omni_mask, 2] = torch.empty(m, device=self.device).uniform_(-1.05, 1.05)

        cmd[stand_mask] = 0.0
        self.vel_command_b[env_ids] = cmd

    def _update_command(self) -> None:
        pass


@dataclass(kw_only=True)
class OmnidirectionalWalkCommandCfg(CommandTermCfg):
    resampling_time_range: tuple[float, float] = (3.0, 6.0)

    def build(self, env: ManagerBasedRlEnv) -> OmnidirectionalWalkCommand:
        return OmnidirectionalWalkCommand(self, env)


def _safe(tensor: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(tensor, nan=0.0, posinf=0.0, neginf=0.0)


def robot_state_is_nan(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    bad = torch.isnan(robot.data.joint_pos).any(dim=-1)
    bad = bad | torch.isnan(robot.data.joint_vel).any(dim=-1)
    bad = bad | torch.isnan(robot.data.root_link_pos_w).any(dim=-1)
    bad = bad | torch.isnan(robot.data.root_link_quat_w).any(dim=-1)
    return bad


def fell_or_collapsed(env: ManagerBasedRlEnv, max_tilt_rad: float = 1.05, min_z: float = 0.060) -> torch.Tensor:
    robot = env.scene["robot"]
    gz = robot.data.projected_gravity_b[:, 2]
    z = robot.data.root_link_pos_w[:, 2]
    cos_limit = math.cos(max_tilt_rad)
    return ((-gz) < cos_limit) | (z < min_z)


def head_pose_home(env: ManagerBasedRlEnv, head_joint_ids: tuple[int, ...], std: float = 0.50) -> torch.Tensor:
    """Keep the 4 neck/head joints near HOME with wide std=0.50 so natural stepping oscillation isn't taxed."""
    robot = env.scene["robot"]
    q = robot.data.joint_pos[:, list(head_joint_ids)]
    q0 = robot.data.default_joint_pos[:, list(head_joint_ids)]
    return _safe(torch.mean(torch.exp(-torch.square((q - q0) / std)), dim=-1))


def track_lin_vel_biped(env: ManagerBasedRlEnv, command_name: str = "twist") -> torch.Tensor:
    """Two-scale Gaussian scaled to miniature biped speeds (~0.25 m/s) so standing still cannot farm velocity reward."""
    robot = env.scene["robot"]
    cmd = env.command_manager.get_command(command_name)
    actual = robot.data.root_link_lin_vel_b
    xy_err = torch.sum(torch.square(cmd[:, :2] - actual[:, :2]), dim=1)
    z_err = 0.5 * torch.square(actual[:, 2])
    err = xy_err + z_err
    return _safe(0.25 * torch.exp(-err / 0.06) + 0.75 * torch.exp(-err / 0.012))


def track_ang_vel_biped(env: ManagerBasedRlEnv, command_name: str = "twist") -> torch.Tensor:
    """Two-scale yaw-rate Gaussian with sharp peak so spin-in-place and heading alignment track crisply."""
    robot = env.scene["robot"]
    cmd = env.command_manager.get_command(command_name)
    actual = robot.data.root_link_ang_vel_b
    z_err = torch.square(cmd[:, 2] - actual[:, 2])
    xy_err = 0.25 * torch.sum(torch.square(actual[:, :2]), dim=1)
    err = z_err + xy_err
    return _safe(0.25 * torch.exp(-err / 0.30) + 0.75 * torch.exp(-err / 0.06))


def smooth_feet_air_time(
    env: ManagerBasedRlEnv,
    sensor_name: str = "feet_ground_contact",
    ramp_start: float = 0.025,
    plateau_min: float = 0.115,
    plateau_max: float = 0.285,
    command_name: str = "twist",
    command_threshold: float = 0.01,
) -> torch.Tensor:
    """Continuous air-time reward with smooth ramp [25ms..115ms] -> plateau [115ms..285ms] eliminating zero-gradient cliff."""
    sensor = env.scene.sensors[sensor_name]
    air = sensor.data.current_air_time
    if air is None:
        return torch.zeros(env.num_envs, device=env.device)
    cmd = env.command_manager.get_command(command_name)
    active = ((torch.norm(cmd[:, :2], dim=1) + torch.abs(cmd[:, 2])) > command_threshold).float()
    ramp = torch.clamp((air - ramp_start) / (plateau_min - ramp_start), min=0.0, max=1.0)
    within_cap = (air < plateau_max).float()
    per_foot = ramp * within_cap
    in_air = air > 0.0
    env.extras["log"]["Metrics/air_time_mean"] = torch.sum(air * in_air.float()) / torch.clamp(
        torch.sum(in_air.float()), min=1.0
    )
    return _safe(torch.sum(per_foot, dim=1) * active)


def flat_foot_clearance(
    env: ManagerBasedRlEnv,
    foot_site_ids: tuple[int, int],
    target_height: float = 0.02,
    command_name: str = "twist",
    command_threshold: float = 0.01,
) -> torch.Tensor:
    """Penalize dragging a moving foot below target_height on flat z=0 ground (velocity-weighted)."""
    robot = env.scene["robot"]
    cmd = env.command_manager.get_command(command_name)
    active = ((torch.norm(cmd[:, :2], dim=1) + torch.abs(cmd[:, 2])) > command_threshold).float()
    sids = list(foot_site_ids)
    foot_z = robot.data.site_pos_w[:, sids, 2]
    vel_norm = torch.norm(robot.data.site_lin_vel_w[:, sids, :2], dim=-1)
    cost = torch.sum(torch.abs(foot_z - target_height) * vel_norm, dim=1) * active
    return _safe(cost)


class flat_foot_swing_height:
    """Penalize landing with peak swing height far from target_height on flat z=0 ground."""

    def __init__(self, cfg: RewardTermCfg, env: ManagerBasedRlEnv):
        num_feet = len(cfg.params["foot_site_ids"])
        self.peak_heights = torch.zeros((env.num_envs, num_feet), device=env.device, dtype=torch.float32)
        self.step_dt = env.step_dt

    def reset(self, env_ids: torch.Tensor | slice | None = None) -> None:
        if env_ids is None:
            self.peak_heights.zero_()
        else:
            self.peak_heights[env_ids] = 0.0

    def __call__(
        self,
        env: ManagerBasedRlEnv,
        sensor_name: str,
        foot_site_ids: tuple[int, int],
        target_height: float = 0.02,
        command_name: str = "twist",
        command_threshold: float = 0.01,
    ) -> torch.Tensor:
        robot = env.scene["robot"]
        sensor = env.scene.sensors[sensor_name]
        cmd = env.command_manager.get_command(command_name)
        active = ((torch.norm(cmd[:, :2], dim=1) + torch.abs(cmd[:, 2])) > command_threshold).float()
        foot_heights = robot.data.site_pos_w[:, list(foot_site_ids), 2]
        in_air = sensor.data.found == 0
        self.peak_heights = torch.where(
            in_air,
            torch.maximum(self.peak_heights, foot_heights),
            self.peak_heights,
        )
        first_contact = sensor.compute_first_contact(dt=self.step_dt)
        error = self.peak_heights / target_height - 1.0
        cost = torch.sum(torch.square(error) * first_contact.float(), dim=1) * active
        self.peak_heights = torch.where(
            first_contact,
            torch.zeros_like(self.peak_heights),
            self.peak_heights,
        )
        return _safe(cost)


def foot_height_obs(env: ManagerBasedRlEnv, foot_site_ids: tuple[int, int]) -> torch.Tensor:
    robot = env.scene["robot"]
    return _safe(robot.data.site_pos_w[:, list(foot_site_ids), 2])


def foot_air_time_obs(env: ManagerBasedRlEnv) -> torch.Tensor:
    sensor = env.scene.sensors["feet_ground_contact"]
    air = sensor.data.current_air_time
    if air is None:
        return torch.zeros(env.num_envs, 2, device=env.device)
    return _safe(air)


def foot_contact_obs(env: ManagerBasedRlEnv) -> torch.Tensor:
    sensor = env.scene.sensors["feet_ground_contact"]
    found = sensor.data.found
    if found is None:
        return torch.zeros(env.num_envs, 2, device=env.device)
    return _safe((found > 0).float())


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
    site_list = list(derived.sites)
    foot_site_ids = (site_list.index("left_foot"), site_list.index("right_foot"))
    head_joint_ids = tuple(
        idx for idx, name in enumerate(joint_names) if ("neck" in name or "head" in name)
    )

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
        track_air_time=True,
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
            params={"command_name": "twist"},
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
        "foot_height": ObservationTermCfg(
            func=foot_height_obs,
            params={"foot_site_ids": foot_site_ids},
        ),
        "foot_air_time": ObservationTermCfg(func=foot_air_time_obs),
        "foot_contact": ObservationTermCfg(func=foot_contact_obs),
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
        "twist": OmnidirectionalWalkCommandCfg(resampling_time_range=(3.0, 6.0)),
    }

    events = {
        "reset_base": EventTermCfg(
            func=envs_mdp.reset_root_state_uniform,
            mode="reset",
            params={
                "pose_range": {
                    "x": (-0.25, 0.25),
                    "y": (-0.25, 0.25),
                    "z": (0.0, 0.008),
                    "roll": (-0.04, 0.04),
                    "pitch": (-0.05, 0.05),
                    "yaw": (-math.pi, math.pi),
                },
                "velocity_range": {
                    "x": (-0.03, 0.03),
                    "y": (-0.03, 0.03),
                    "z": (0.0, 0.0),
                    "roll": (-0.10, 0.10),
                    "pitch": (-0.10, 0.10),
                    "yaw": (-0.10, 0.10),
                },
            },
        ),
        "reset_joints": EventTermCfg(
            func=envs_mdp.reset_joints_by_offset,
            mode="reset",
            params={
                "position_range": (-0.03, 0.03),
                "velocity_range": (0.0, 0.0),
            },
        ),
        "push_robot": EventTermCfg(
            func=envs_mdp.push_by_setting_velocity,
            mode="interval",
            interval_range_s=(3.5, 6.0),
            params={
                "velocity_range": {
                    "x": (-0.15, 0.15),
                    "y": (-0.15, 0.15),
                },
                "asset_cfg": SceneEntityCfg("robot"),
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
                "ranges": (0.70, 1.30),
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
        "armature": EventTermCfg(
            func=dr.joint_armature,
            mode="reset",
            params={
                "asset_cfg": SceneEntityCfg("robot", joint_names=(r".*",)),
                "operation": "scale",
                "ranges": (0.92, 1.08),
            },
        ),
    }

    std_standing = {
        r".*hip_yaw.*": 0.10,
        r".*hip_roll.*": 0.05,
        r".*hip_pitch.*": 0.15,
        r".*knee.*": 0.15,
        r".*ankle.*": 0.10,
    }
    std_walking = {
        r".*hip_yaw.*": 0.30,
        r".*hip_roll.*": 0.05,
        r".*hip_pitch.*": 0.40,
        r".*knee.*": 0.40,
        r".*ankle.*": 0.25,
    }

    rewards = {
        "track_linear_velocity": RewardTermCfg(
            func=track_lin_vel_biped,
            weight=2.8,
            params={"command_name": "twist"},
        ),
        "track_angular_velocity": RewardTermCfg(
            func=track_ang_vel_biped,
            weight=2.5,
            params={"command_name": "twist"},
        ),
        "upright": RewardTermCfg(
            func=vel_mdp.upright,
            weight=2.0,
            params={
                "std": math.sqrt(0.05),
                "asset_cfg": SceneEntityCfg("robot", body_names=("trunk_base",)),
            },
        ),
        "pose": RewardTermCfg(
            func=vel_mdp.variable_posture,
            weight=1.0,
            params={
                "asset_cfg": SceneEntityCfg("robot", joint_names=(r"^(?!.*neck.*|.*head.*).*",)),
                "command_name": "twist",
                "walking_threshold": 0.01,
                "running_threshold": 1.5,
                "std_standing": std_standing,
                "std_walking": std_walking,
                "std_running": std_walking,
            },
        ),
        "head_pose": RewardTermCfg(
            func=head_pose_home,
            weight=1.5,
            params={"head_joint_ids": head_joint_ids, "std": 0.50},
        ),
        "air_time": RewardTermCfg(
            func=smooth_feet_air_time,
            weight=3.2,
            params={
                "sensor_name": "feet_ground_contact",
                "ramp_start": 0.025,
                "plateau_min": 0.115,
                "plateau_max": 0.285,
                "command_name": "twist",
                "command_threshold": 0.01,
            },
        ),
        "foot_clearance": RewardTermCfg(
            func=flat_foot_clearance,
            weight=-2.0,
            params={
                "foot_site_ids": foot_site_ids,
                "target_height": 0.02,
                "command_name": "twist",
                "command_threshold": 0.01,
            },
        ),
        "foot_swing_height": RewardTermCfg(
            func=flat_foot_swing_height,
            weight=-0.25,
            params={
                "sensor_name": "feet_ground_contact",
                "foot_site_ids": foot_site_ids,
                "target_height": 0.02,
                "command_name": "twist",
                "command_threshold": 0.01,
            },
        ),
        "foot_slip": RewardTermCfg(
            func=vel_mdp.feet_slip,
            weight=-0.10,
            params={
                "sensor_name": "feet_ground_contact",
                "command_name": "twist",
                "command_threshold": 0.01,
                "asset_cfg": SceneEntityCfg("robot", site_names=("left_foot", "right_foot")),
            },
        ),
        "body_ang_vel": RewardTermCfg(
            func=vel_mdp.body_angular_velocity_penalty,
            weight=-0.05,
            params={"asset_cfg": SceneEntityCfg("robot", body_names=("trunk_base",))},
        ),
        "dof_pos_limits": RewardTermCfg(func=vel_mdp.joint_pos_limits, weight=-1.0),
        "self_collisions": RewardTermCfg(
            func=vel_mdp.self_collision_cost,
            weight=-1.0,
            params={"sensor_name": "self_collision"},
        ),
        "action_rate_l2": RewardTermCfg(func=vel_mdp.action_rate_l2, weight=-0.12),
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
        episode_length_s=16.0,
    )
    cfg.class_type = MicroduckWalkToEnv
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
            entropy_coef=0.01,
            num_learning_epochs=5,
            num_mini_batches=4,
            learning_rate=1.0e-3,
            schedule="adaptive",
            gamma=0.99,
            lam=0.95,
            desired_kl=0.01,
            max_grad_norm=1.0,
        ),
        experiment_name="microduck_walk_to",
        save_interval=200,
        num_steps_per_env=24,
        max_iterations=800,
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

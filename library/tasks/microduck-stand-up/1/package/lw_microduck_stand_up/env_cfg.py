"""USD-derived mjlab multi-directional fall-recovery task for Microduck standUp."""

from __future__ import annotations

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

TASK_ID = "Lab-Microduck-StandUp-v0"
ROBOT_PRIM = "/World/Lab/Microduck"
BASE_BODY_KEY = "/World/Lab/Microduck/trunk_base"
ACTION_SCALE = 1.0
STAND_Z = 0.1156

MEASURED_FALL_BANK = [{'direction': 'front', 'stage': 'handoff', 'z': 0.03266, 'quat_wxyz': [0.765995, 0.009159, 0.642664, -0.01228], 'joint_q': [0.019125, -0.066832, 0.465263, -0.475479, -0.611666, 1.033755, 1.146342, -0.005202, -0.002528, -0.024092, 0.081879, -0.465284, 0.47502, 0.611182], 'ang_vel_w': [-0.00535, 0.05338, 0.00739], 'joint_qd': [-0.05961, -0.00082, -0.00251, 0.00246, 0.00121, 0.00047, -0.10602, -0.0177, -0.01059, 0.02844, -0.00115, 0.00218, -0.00233, -0.00106]}, {'direction': 'front', 'stage': 'settled_1s_later', 'z': 0.03204, 'quat_wxyz': [0.760759, 0.00996, 0.648839, -0.012452], 'joint_q': [0.002922, -0.07451, 0.463544, -0.473397, -0.610632, 1.028802, 1.10301, -0.005673, -0.003546, -0.000781, 0.080591, -0.46357, 0.473055, 0.610278], 'ang_vel_w': [0.0, 0.0, 0.0], 'joint_qd': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}, {'direction': 'back', 'stage': 'handoff', 'z': 0.04576, 'quat_wxyz': [0.705964, 0.033259, -0.707315, 0.014629], 'joint_q': [-0.060178, -0.073825, -1.023281, -0.020707, 0.902027, -0.239408, -0.774903, 0.000666, 0.000599, 0.052182, 0.079046, 1.023612, 0.020811, -0.90196], 'ang_vel_w': [0.32253, -0.10287, -0.00392], 'joint_qd': [-0.09341, -0.15416, 0.38336, -0.20732, 0.01191, -0.32246, 0.55661, -0.00048, -0.00025, 0.16367, 0.05046, -0.39968, 0.20367, -0.01067]}, {'direction': 'back', 'stage': 'settled_1s_later', 'z': 0.04597, 'quat_wxyz': [0.699788, 0.025778, -0.713515, 0.023011], 'joint_q': [-0.060032, -0.082304, -1.014212, -0.027807, 0.903419, -0.213146, -0.756468, -0.000664, -0.000105, 0.058231, 0.079718, 1.012415, 0.028221, -0.903366], 'ang_vel_w': [0.0, 0.0, 0.0], 'joint_qd': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}, {'direction': 'left', 'stage': 'handoff', 'z': 0.05084, 'quat_wxyz': [0.485436, -0.622945, -0.247921, -0.561094], 'joint_q': [-0.033282, 0.365864, -1.034173, 1.210074, 0.794065, 0.52868, 0.221722, 0.102612, 0.433359, 0.047644, 0.382348, 0.1549, 0.029356, -0.206738], 'ang_vel_w': [-1.36659, -1.93952, 0.26964], 'joint_qd': [-0.33099, -0.17927, -0.13065, -0.00665, 0.00533, -0.19187, 0.00552, -0.67837, 0.30145, 0.02062, -0.1023, -0.10252, 0.06317, 0.0002]}, {'direction': 'left', 'stage': 'settled_1s_later', 'z': 0.04869, 'quat_wxyz': [0.533996, -0.661958, -0.19177, -0.48978], 'joint_q': [0.002064, 0.383979, -1.039006, 1.211097, 0.795537, 0.444499, 0.253676, 0.07164, 0.436334, 0.042449, 0.383975, 0.144861, 0.034015, -0.207043], 'ang_vel_w': [0.0, 0.0, 0.0], 'joint_qd': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}, {'direction': 'right', 'stage': 'handoff', 'z': 0.04787, 'quat_wxyz': [0.608026, 0.652423, -0.178189, 0.41581], 'joint_q': [-0.055, -0.383964, -0.139315, -0.043471, 0.208467, 0.515881, 0.210802, -0.069863, -0.436215, 0.015783, -0.382958, 1.075031, -1.21495, -0.793582], 'ang_vel_w': [0.19213, -0.24675, 0.06047], 'joint_qd': [0.01668, 0.00818, -0.16795, 0.03824, -0.00051, -0.15867, 0.10981, -0.45847, 0.02624, -0.41064, 0.0888, 0.00782, 0.00402, -0.0025]}, {'direction': 'right', 'stage': 'settled_1s_later', 'z': 0.04572, 'quat_wxyz': [0.604643, 0.668572, -0.179262, 0.39406], 'joint_q': [-0.053926, -0.383977, -0.146983, -0.040748, 0.208, 0.445697, 0.267291, -0.116981, -0.436333, -0.073365, -0.383977, 1.076563, -1.213684, -0.795322], 'ang_vel_w': [0.0, 0.0, 0.0], 'joint_qd': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}]


class MicroduckStandUpEnv(ManagerBasedRlEnv):
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
                }
            )
        return rows


def _quat_mul_wxyz(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    aw, ax, ay, az = a.unbind(-1)
    bw, bx, by, bz = b.unbind(-1)
    return torch.stack(
        (
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ),
        dim=-1,
    )


def _axis_angle_wxyz(axis: tuple[float, float, float], angle: torch.Tensor) -> torch.Tensor:
    half = 0.5 * angle
    s = torch.sin(half)
    return torch.stack((torch.cos(half), axis[0] * s, axis[1] * s, axis[2] * s), dim=-1)


def reset_fallen_and_bridge_states(env: ManagerBasedRlEnv, env_ids: torch.Tensor) -> None:
    n = len(env_ids)
    if n == 0:
        return
    robot = env.scene["robot"]
    device = env.device
    origins = env.scene.env_origins[env_ids]
    default_q = robot.data.default_joint_pos[env_ids].clone()
    soft_limits = robot.data.soft_joint_pos_limits[env_ids]
    j_lo = soft_limits[:, :, 0]
    j_hi = soft_limits[:, :, 1]

    bank_z = torch.tensor([item["z"] for item in MEASURED_FALL_BANK], device=device, dtype=torch.float32)
    bank_quat = torch.tensor(
        [item["quat_wxyz"] for item in MEASURED_FALL_BANK], device=device, dtype=torch.float32
    )
    bank_jq = torch.tensor(
        [item["joint_q"] for item in MEASURED_FALL_BANK], device=device, dtype=torch.float32
    )
    bank_ang = torch.tensor(
        [item["ang_vel_w"] for item in MEASURED_FALL_BANK], device=device, dtype=torch.float32
    )
    bank_jqd = torch.tensor(
        [item["joint_qd"] for item in MEASURED_FALL_BANK], device=device, dtype=torch.float32
    )

    pos = torch.zeros(n, 3, device=device)
    pos[:, 0] = origins[:, 0] + torch.empty(n, device=device).uniform_(-0.15, 0.15)
    pos[:, 1] = origins[:, 1] + torch.empty(n, device=device).uniform_(-0.15, 0.15)
    quat = torch.zeros(n, 4, device=device)
    quat[:, 0] = 1.0
    jq = default_q.clone()
    vel = torch.zeros(n, 6, device=device)
    vel[:, :2] = torch.empty(n, 2, device=device).uniform_(-0.08, 0.08)
    vel[:, 3:6] = torch.empty(n, 3, device=device).uniform_(-0.85, 0.85)
    jvel = torch.empty(n, default_q.shape[1], device=device).uniform_(-0.55, 0.55)

    u = torch.rand(n, device=device)
    # 76% from the 8 measured fallen floor states (front/back/left/right x handoff/settled)
    fall_mask = u < 0.76
    # 12% crouched/tilted mid-rise bridge states for fast value propagation
    crouch_mask = (u >= 0.76) & (u < 0.88)
    # 12% near-upright standing stabilization states
    stand_mask = u >= 0.88

    # Random global compass yaw across [-pi, pi] for every environment
    yaw = torch.empty(n, device=device).uniform_(-math.pi, math.pi)

    if fall_mask.any():
        m = int(fall_mask.sum().item())
        idx = torch.randint(0, len(MEASURED_FALL_BANK), (m,), device=device)
        z_sel = bank_z[idx] + torch.empty(m, device=device).uniform_(0.0, 0.008)
        q_sel = bank_quat[idx]
        jq_sel = bank_jq[idx] + torch.empty(m, default_q.shape[1], device=device).uniform_(-0.10, 0.10)

        # Rotate measured world angular velocity by yaw + add domain randomization
        ang_base = bank_ang[idx]
        cy = torch.cos(yaw[fall_mask])
        sy = torch.sin(yaw[fall_mask])
        ang_rot = torch.stack(
            (
                cy * ang_base[:, 0] - sy * ang_base[:, 1],
                sy * ang_base[:, 0] + cy * ang_base[:, 1],
                ang_base[:, 2],
            ),
            dim=-1,
        )
        vel[fall_mask, 3:6] = ang_rot + torch.empty(m, 3, device=device).uniform_(-0.75, 0.75)
        jvel[fall_mask] = bank_jqd[idx] + torch.empty(
            m, default_q.shape[1], device=device
        ).uniform_(-0.45, 0.45)

        # For half of back spawns (idx 2 or 3), add partial roll perturbation (-80..+80 deg)
        # to create a smooth roll-over bridge out of flat supine!
        is_back = (idx == 2) | (idx == 3)
        roll_bridge = is_back & (torch.rand(m, device=device) < 0.45)
        extra_roll = torch.empty(m, device=device).uniform_(-0.14, 0.14)
        if roll_bridge.any():
            rb_n = int(roll_bridge.sum().item())
            extra_roll[roll_bridge] = torch.empty(rb_n, device=device).uniform_(-1.40, 1.40)
            z_sel[roll_bridge] = torch.clamp(z_sel[roll_bridge] + 0.012, max=0.075)
        extra_pitch = torch.empty(m, device=device).uniform_(-0.14, 0.14)
        q_local_jit = _quat_mul_wxyz(
            _axis_angle_wxyz((0.0, 1.0, 0.0), extra_pitch),
            _axis_angle_wxyz((1.0, 0.0, 0.0), extra_roll),
        )
        q_sel = _quat_mul_wxyz(q_sel, q_local_jit)
        pos[fall_mask, 2] = origins[fall_mask, 2] + z_sel
        quat[fall_mask] = q_sel
        jq[fall_mask] = jq_sel

    if crouch_mask.any():
        m = int(crouch_mask.sum().item())
        beak_bridge = torch.rand(m, device=device) < 0.40
        z_c = torch.where(
            beak_bridge,
            torch.empty(m, device=device).uniform_(0.098, 0.116),
            torch.empty(m, device=device).uniform_(0.060, 0.098),
        )
        pos[crouch_mask, 2] = origins[crouch_mask, 2] + z_c
        pitch = torch.where(
            beak_bridge,
            torch.empty(m, device=device).uniform_(0.30, 0.65),
            torch.empty(m, device=device).uniform_(-0.65, 0.65),
        )
        roll = torch.where(
            beak_bridge,
            torch.empty(m, device=device).uniform_(-0.20, 0.20),
            torch.empty(m, device=device).uniform_(-0.55, 0.55),
        )
        quat[crouch_mask] = _quat_mul_wxyz(
            _axis_angle_wxyz((0.0, 1.0, 0.0), pitch),
            _axis_angle_wxyz((1.0, 0.0, 0.0), roll),
        )
        crouch_q = default_q[crouch_mask].clone()
        crouch_q[:, 3] += torch.where(
            beak_bridge,
            torch.empty(m, device=device).uniform_(-0.10, 0.35),
            torch.empty(m, device=device).uniform_(0.3, 1.1),
        )
        crouch_q[:, 12] -= torch.where(
            beak_bridge,
            torch.empty(m, device=device).uniform_(-0.10, 0.35),
            torch.empty(m, device=device).uniform_(0.3, 1.1),
        )
        crouch_q[:, 5] = torch.where(
            beak_bridge,
            torch.empty(m, device=device).uniform_(-1.55, 0.10),
            crouch_q[:, 5],
        )
        crouch_q += torch.empty(m, default_q.shape[1], device=device).uniform_(-0.15, 0.15)
        jq[crouch_mask] = crouch_q

    if stand_mask.any():
        m = int(stand_mask.sum().item())
        pos[stand_mask, 2] = origins[stand_mask, 2] + torch.empty(m, device=device).uniform_(0.108, 0.120)
        pitch = torch.empty(m, device=device).uniform_(-0.22, 0.22)
        roll = torch.empty(m, device=device).uniform_(-0.18, 0.18)
        quat[stand_mask] = _quat_mul_wxyz(
            _axis_angle_wxyz((0.0, 1.0, 0.0), pitch),
            _axis_angle_wxyz((1.0, 0.0, 0.0), roll),
        )
        jq[stand_mask] = default_q[stand_mask] + torch.empty(
            m, default_q.shape[1], device=device
        ).uniform_(-0.08, 0.08)

    quat = _quat_mul_wxyz(_axis_angle_wxyz((0.0, 0.0, 1.0), yaw), quat)
    quat = quat / torch.clamp(torch.linalg.norm(quat, dim=-1, keepdim=True), min=1e-6)
    jq = torch.clamp(jq, j_lo, j_hi)

    root_state = torch.cat([pos, quat, vel], dim=-1)
    robot.write_root_state_to_sim(root_state, env_ids=env_ids)
    robot.write_joint_state_to_sim(jq, jvel, env_ids=env_ids)


def _safe(tensor: torch.Tensor) -> torch.Tensor:
    return torch.nan_to_num(tensor, nan=0.0, posinf=0.0, neginf=0.0)


def _smoothstep(x: torch.Tensor, lo: float, hi: float) -> torch.Tensor:
    t = torch.clamp((x - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def robot_state_is_nan(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    bad = torch.isnan(robot.data.joint_pos).any(dim=-1)
    bad = bad | torch.isnan(robot.data.joint_vel).any(dim=-1)
    bad = bad | torch.isnan(robot.data.root_link_pos_w).any(dim=-1)
    bad = bad | torch.isnan(robot.data.root_link_quat_w).any(dim=-1)
    return bad


def _upper_ground_hit(env: ManagerBasedRlEnv) -> torch.Tensor:
    sensor = env.scene["upper_ground_contact"]
    found = sensor.data.found
    if found is None:
        return torch.zeros(env.num_envs, device=env.device)
    return (found > 0).any(dim=-1).float()


def _both_feet_planted(env: ManagerBasedRlEnv) -> torch.Tensor:
    sensor = env.scene["feet_ground_contact"]
    found = sensor.data.found
    if found is None:
        return torch.ones(env.num_envs, device=env.device)
    return (found > 0).all(dim=-1).float()


def upright_linear(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    cos_tilt = torch.clamp(-robot.data.projected_gravity_b[:, 2], -1.0, 1.0)
    return _safe(0.5 * (1.0 + cos_tilt))


def upright_sharp_at_height(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    z = robot.data.root_link_pos_w[:, 2]
    gxy_sq = torch.sum(torch.square(robot.data.projected_gravity_b[:, :2]), dim=-1)
    gate = _smoothstep(z, 0.055, 0.108)
    term = 0.40 * torch.exp(-gxy_sq / (0.45 * 0.45)) + 0.60 * torch.exp(-gxy_sq / (0.16 * 0.16))
    return _safe(gate * term)


def height_gaussian(env: ManagerBasedRlEnv, std: float) -> torch.Tensor:
    robot = env.scene["robot"]
    dz = robot.data.root_link_pos_w[:, 2] - STAND_Z
    return _safe(torch.exp(-torch.square(dz) / (std * std)))


def height_sharp_upright(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    z = robot.data.root_link_pos_w[:, 2]
    cos_tilt = torch.clamp(-robot.data.projected_gravity_b[:, 2], -1.0, 1.0)
    no_upper = 1.0 - _upper_ground_hit(env)
    dz = z - STAND_Z
    return _safe(no_upper * _smoothstep(cos_tilt, math.cos(0.50), math.cos(0.22)) * torch.exp(-torch.square(dz) / (0.016 * 0.016)))


def height_l1_cost(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    return _safe(-torch.abs(robot.data.root_link_pos_w[:, 2] - STAND_Z))


def upward_rise_velocity(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    z = robot.data.root_link_pos_w[:, 2]
    vz = robot.data.root_link_lin_vel_w[:, 2]
    below = (z < 0.120).float()
    return _safe(below * torch.clamp(vz, min=0.0, max=0.60))


def beak_prop_at_height_cost(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    z = robot.data.root_link_pos_w[:, 2]
    return _safe(_smoothstep(z, 0.084, 0.104) * _upper_ground_hit(env))


def head_neck_retract_at_height(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    z = robot.data.root_link_pos_w[:, 2]
    cos_tilt = torch.clamp(-robot.data.projected_gravity_b[:, 2], -1.0, 1.0)
    gate = _smoothstep(z, 0.086, 0.106) * _smoothstep(cos_tilt, math.cos(0.95), math.cos(0.45))
    hn_err = torch.mean(torch.square(robot.data.joint_pos[:, 5:9] - robot.data.default_joint_pos[:, 5:9]), dim=-1)
    return _safe(gate * (0.40 * torch.exp(-hn_err / (0.80 * 0.80)) + 0.60 * torch.exp(-hn_err / (0.25 * 0.25))))


def upright_pose_convergence(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    z = robot.data.root_link_pos_w[:, 2]
    cos_tilt = torch.clamp(-robot.data.projected_gravity_b[:, 2], -1.0, 1.0)
    gate = _smoothstep(z, 0.075, 0.108) * _smoothstep(cos_tilt, math.cos(0.85), math.cos(0.25))
    q_err = torch.mean(torch.square(robot.data.joint_pos - robot.data.default_joint_pos), dim=-1)
    return _safe(gate * (0.40 * torch.exp(-q_err / (0.55 * 0.55)) + 0.60 * torch.exp(-q_err / (0.22 * 0.22))))


def calm_standing_bonus(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    z = robot.data.root_link_pos_w[:, 2]
    gxy_sq = torch.sum(torch.square(robot.data.projected_gravity_b[:, :2]), dim=-1)
    no_upper = 1.0 - _upper_ground_hit(env)
    both_feet = _both_feet_planted(env)
    gate = _smoothstep(z, 0.092, 0.110) * no_upper * (0.30 + 0.70 * both_feet)
    tilt_term = 0.35 * torch.exp(-gxy_sq / (0.38 * 0.38)) + 0.65 * torch.exp(-gxy_sq / (0.14 * 0.14))
    q_err = torch.mean(torch.square(robot.data.joint_pos - robot.data.default_joint_pos), dim=-1)
    ang_sq = torch.sum(torch.square(robot.data.root_link_ang_vel_b), dim=-1)
    lin_sq = torch.sum(torch.square(robot.data.root_link_lin_vel_b), dim=-1)
    calm = (0.35 + 0.65 * torch.exp(-q_err / 0.08)) * (0.35 + 0.65 * torch.exp(-(ang_sq + 4.0 * lin_sq) / 0.35))
    return _safe(gate * tilt_term * calm)


def body_ang_vel_l2(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    return _safe(-torch.sum(torch.square(robot.data.root_link_ang_vel_b), dim=-1))


def foot_contact_forces_obs(env: ManagerBasedRlEnv) -> torch.Tensor:
    sensor = env.scene.sensors["feet_ground_contact"]
    forces = sensor.data.force
    if forces is None:
        return torch.zeros(env.num_envs, 6, device=env.device)
    return _safe(forces.reshape(env.num_envs, -1) * 0.1)


def root_height_obs(env: ManagerBasedRlEnv) -> torch.Tensor:
    robot = env.scene["robot"]
    return _safe((robot.data.root_link_pos_w[:, 2:3] - STAND_Z) * 10.0)


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
            pos=(0.0, 0.0, 0.116),
            rot=(1.0, 0.0, 0.0, 0.0),
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
    upper_ground_cfg = ContactSensorCfg(
        name="upper_ground_contact",
        primary=ContactMatch(
            mode="geom",
            pattern=r"^(trunk_base_collision_0|neck_collision_0|neck_pitch_collision_0|yaw_roll_motion_collision_0|jaw_soft_collision_0|jaw_soft_collision_1|jaw_soft_collision_2)$",
            entity="robot",
        ),
        secondary=ContactMatch(mode="body", pattern="terrain"),
        fields=("found",),
        reduce="none",
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
        "root_height": ObservationTermCfg(func=root_height_obs),
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

    events = {
        "set_fallen_state": EventTermCfg(
            func=reset_fallen_and_bridge_states,
            mode="reset",
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

    rewards = {
        "upright_linear": RewardTermCfg(func=upright_linear, weight=1.8),
        "upright_sharp": RewardTermCfg(func=upright_sharp_at_height, weight=2.5),
        "height_wide": RewardTermCfg(func=height_gaussian, weight=1.5, params={"std": 0.045}),
        "height_sharp": RewardTermCfg(func=height_sharp_upright, weight=2.5),
        "height_l1": RewardTermCfg(func=height_l1_cost, weight=4.0),
        "upward_vel": RewardTermCfg(func=upward_rise_velocity, weight=1.0),
        "beak_prop_cost": RewardTermCfg(func=beak_prop_at_height_cost, weight=-4.0),
        "head_neck_upright": RewardTermCfg(func=head_neck_retract_at_height, weight=3.0),
        "upright_pose": RewardTermCfg(func=upright_pose_convergence, weight=3.0),
        "calm_standing": RewardTermCfg(func=calm_standing_bonus, weight=7.5),
        "ang_vel_l2": RewardTermCfg(func=body_ang_vel_l2, weight=0.02),
        "dof_pos_limits": RewardTermCfg(func=vel_mdp.joint_pos_limits, weight=-0.5),
        "self_collisions": RewardTermCfg(
            func=vel_mdp.self_collision_cost,
            weight=-0.5,
            params={"sensor_name": "self_collision"},
        ),
        "action_rate_l2": RewardTermCfg(func=vel_mdp.action_rate_l2, weight=-0.08),
    }

    terminations = {
        "time_out": TerminationTermCfg(func=vel_mdp.time_out, time_out=True),
        "nan_state": TerminationTermCfg(func=robot_state_is_nan, time_out=False),
    }

    cfg = ManagerBasedRlEnvCfg(
        scene=SceneCfg(
            terrain=TerrainEntityCfg(terrain_type="plane"),
            entities={"robot": robot_cfg},
            sensors=(feet_ground_cfg, upper_ground_cfg, self_collision_cfg),
            num_envs=2048,
            env_spacing=1.5,
        ),
        observations=observations,
        actions=actions,
        commands={},
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
            nconmax=140,
            njmax=400,
            mujoco=MujocoCfg(
                timestep=0.002,
                integrator="implicitfast",
                cone="pyramidal",
                iterations=20,
                ls_iterations=10,
            ),
        ),
        decimation=10,
        episode_length_s=7.0,
    )
    cfg.class_type = MicroduckStandUpEnv
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
            entropy_coef=0.010,
            num_learning_epochs=5,
            num_mini_batches=4,
            learning_rate=3.0e-4,
            schedule="adaptive",
            gamma=0.99,
            lam=0.95,
            desired_kl=0.01,
            max_grad_norm=1.0,
        ),
        experiment_name="microduck_stand_up",
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

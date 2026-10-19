"""FR3 + 16 mm × 30 mm tube and physical rack, Isaac Lab 3 / Newton MJWarp."""

from __future__ import annotations

import json
import os
from pathlib import Path

import isaaclab.envs.mdp as base_mdp
import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg, AssetBaseCfg, RigidObjectCfg
from isaaclab.controllers import DifferentialIKControllerCfg
from isaaclab.envs import ManagerBasedRLEnvCfg
from isaaclab.envs.mdp.actions import (
    DifferentialInverseKinematicsActionCfg,
    JointPositionActionCfg,
    RelativeJointPositionActionCfg,
)
from isaaclab.managers import EventTermCfg as EventTerm
from isaaclab.managers import ObservationGroupCfg as ObsGroup
from isaaclab.managers import ObservationTermCfg as ObsTerm
from isaaclab.managers import RewardTermCfg as RewTerm
from isaaclab.managers import SceneEntityCfg
from isaaclab.managers import TerminationTermCfg as DoneTerm
from isaaclab.scene import InteractiveSceneCfg
from isaaclab.sensors import ContactSensorCfg
from isaaclab.utils.configclass import configclass
from isaaclab_tutorial.tasks.place_vial.config.so101.agents.rsl_rl_ppo_cfg import SO101StatePPORunnerCfg
from isaaclab_tutorial.tasks.place_vial.config.so101.env_cfg import PhysicsCfg
from isaaclab_tutorial.tasks.place_vial.mdp.actions import SoftLimitRelativeGripperActionCfg

from . import reset, terms

ASSET_DIR = Path(os.environ["FR3_CONTACT_ASSETS"])
ROBOT_USD = json.loads((ASSET_DIR / "manifest.json").read_text(encoding="utf-8"))["robot_usd"]
DATASET = Path(os.environ.get("FR3_CONTACT_RESETS", str(ASSET_DIR / "reset_poses.pt")))
ARM_JOINTS = [f"fr3_joint{i}" for i in range(1, 8)]
FINGER_PATH = (
    "{ENV_REGEX_NS}/Robot/Geometry/base/fr3_link1/fr3_link2/fr3_link3/"
    "fr3_link4/fr3_link5/fr3_link6/fr3_link7/"
)
HOME = reset.HOME
TCP_OFFSET = (0.0, 0.0, 0.21065)


@configclass
class FR3SceneCfg(InteractiveSceneCfg):
    robot = ArticulationCfg(
        prim_path="{ENV_REGEX_NS}/Robot",
        spawn=sim_utils.UsdFileCfg(
            usd_path=ROBOT_USD,
            activate_contact_sensors=True,
        ),
        init_state=ArticulationCfg.InitialStateCfg(
            joint_pos={**dict(zip(ARM_JOINTS, HOME, strict=True)), "fr3_finger_joint.*": 0.039}
        ),
        actuators={
            "arm": ImplicitActuatorCfg(
                joint_names_expr=["fr3_joint[1-7]"],
                joint_effort_limit={"fr3_joint[1-4]": 87.0, "fr3_joint[5-7]": 12.0},
                joint_velocity_limit={"fr3_joint[1-4]": 2.62, "fr3_joint[5-7]": 4.18},
                stiffness=dict(zip(ARM_JOINTS, (24000, 24000, 20000, 16000, 10000, 8000, 4000), strict=True)),
                damping=dict(zip(ARM_JOINTS, (240, 240, 200, 160, 100, 80, 40), strict=True)),
                armature=0.01,
            ),
            "hand": ImplicitActuatorCfg(
                joint_names_expr=["fr3_finger_joint1"],
                joint_effort_limit=70.0,
                joint_velocity_limit=0.2,
                stiffness=2000.0,
                damping=20.0,
            ),
            "mimic": ImplicitActuatorCfg(
                joint_names_expr=["fr3_finger_joint2"],
                joint_effort_limit=1.0,
                stiffness=0.0,
                damping=0.0,
            ),
        },
        soft_joint_pos_limit_factor=0.98,
    )
    vial = RigidObjectCfg(
        prim_path="{ENV_REGEX_NS}/Vial",
        spawn=sim_utils.UsdFileCfg(usd_path=str(ASSET_DIR / "Tube.usda")),
        init_state=RigidObjectCfg.InitialStateCfg(pos=(0.45, -0.15, 0.035)),
    )
    rack = RigidObjectCfg(
        prim_path="{ENV_REGEX_NS}/Rack",
        spawn=sim_utils.UsdFileCfg(usd_path=str(ASSET_DIR / "Rack.usda")),
        init_state=RigidObjectCfg.InitialStateCfg(pos=(0.45, 0.15, 0.02)),
    )
    table = AssetBaseCfg(
        prim_path="{ENV_REGEX_NS}/Table", spawn=sim_utils.UsdFileCfg(usd_path=str(ASSET_DIR / "Table.usda"))
    )
    fixed_jaw_contact = ContactSensorCfg(
        prim_path=FINGER_PATH + "fr3_leftfinger",
        filter_prim_paths_expr=["{ENV_REGEX_NS}/Vial"],
        history_length=4,
    )
    moving_jaw_contact = ContactSensorCfg(
        prim_path=FINGER_PATH + "fr3_rightfinger",
        filter_prim_paths_expr=["{ENV_REGEX_NS}/Vial"],
        history_length=4,
    )
    vial_rack_contact = ContactSensorCfg(
        prim_path="{ENV_REGEX_NS}/Vial", filter_prim_paths_expr=["{ENV_REGEX_NS}/Rack"], history_length=4
    )


@configclass
class ActionsCfg:
    arm_action = RelativeJointPositionActionCfg(
        asset_name="robot", joint_names=ARM_JOINTS, preserve_order=True, scale=0.033, use_zero_offset=True
    )
    gripper_action = SoftLimitRelativeGripperActionCfg(
        asset_name="robot", joint_names=["fr3_finger_joint1"], scale=0.003, use_zero_offset=True
    )


@configclass
class GeneratorActionsCfg:
    arm_action = DifferentialInverseKinematicsActionCfg(
        asset_name="robot",
        joint_names=ARM_JOINTS,
        body_name="fr3_link7",
        body_offset=DifferentialInverseKinematicsActionCfg.OffsetCfg(pos=TCP_OFFSET),
        controller=DifferentialIKControllerCfg(command_type="pose", use_relative_mode=False, ik_method="dls"),
    )
    gripper_action = JointPositionActionCfg(
        asset_name="robot",
        joint_names=["fr3_finger_joint1"],
        scale=1.0,
        use_default_offset=False,
    )


@configclass
class PolicyStateCfg(ObsGroup):
    joint_pos = ObsTerm(func=terms.joint_pos)
    joint_vel = ObsTerm(func=terms.joint_vel)
    joint_target = ObsTerm(func=terms.joint_target)
    previous_action = ObsTerm(func=terms.last_action)
    end_effector = ObsTerm(
        func=terms.body_state, params={"asset_cfg": SceneEntityCfg("robot", body_names="fr3_link7")}
    )
    vial = ObsTerm(func=terms.rigid_object_state, params={"asset_cfg": SceneEntityCfg("vial")})
    rack_target = ObsTerm(func=terms.rack_relative_target)
    placement = ObsTerm(func=terms.placement_features)
    progress = ObsTerm(func=terms.progress_flags)

    def __post_init__(self):
        self.enable_corruption = False
        self.concatenate_terms = True


@configclass
class CriticStateCfg(PolicyStateCfg):
    contact = ObsTerm(func=terms.contact_state)


@configclass
class ObservationsCfg:
    policy = PolicyStateCfg()
    critic = CriticStateCfg()


@configclass
class DatasetEventsCfg:
    reset_from_dataset = EventTerm(
        func=reset.ResetFromDataset, mode="reset", params={"dataset_path": str(DATASET), "sequential": False}
    )


@configclass
class RawEventsCfg:
    reset_robot = EventTerm(
        func=base_mdp.reset_joints_by_offset,
        mode="reset",
        params={"position_range": (0.0, 0.0), "velocity_range": (0.0, 0.0)},
    )
    reset_tube = EventTerm(
        func=base_mdp.reset_root_state_uniform,
        mode="reset",
        params={
            "asset_cfg": SceneEntityCfg("vial"),
            "pose_range": {"x": (-0.006, 0.006), "y": (-0.006, 0.006)},
            "velocity_range": {},
        },
    )
    clear_progress = EventTerm(func=reset.clear_progress, mode="reset")


@configclass
class RewardsCfg:
    approach_progress = RewTerm(func=terms.ApproachProgressReward, weight=1.0)
    held_goal = RewTerm(func=terms.held_goal_reward, weight=0.1)
    milestones = RewTerm(func=terms.PhysicalMilestoneReward, weight=10.0)
    success = RewTerm(func=terms.success_bonus, weight=200.0)
    vial_lost = RewTerm(func=terms.vial_lost, weight=-50.0)
    action_rate = RewTerm(func=terms.action_rate_l2, weight=-0.002)
    joint_velocity = RewTerm(func=terms.joint_velocity_l2, weight=-0.0002)


@configclass
class TerminationsCfg:
    invalid_episode = DoneTerm(func=terms.EpisodeFailureTerm)
    success = DoneTerm(func=terms.PlacementHistoryTerm)
    vial_lost = DoneTerm(func=terms.vial_lost)
    unstable_robot = DoneTerm(func=terms.unstable_robot)
    time_out = DoneTerm(func=terms.time_out, time_out=True)


@configclass
class FR3TubeEnvCfg(ManagerBasedRLEnvCfg):
    scene = FR3SceneCfg(num_envs=2048, env_spacing=1.5, replicate_physics=True)
    actions = ActionsCfg()
    observations = ObservationsCfg()
    events = DatasetEventsCfg()
    rewards = RewardsCfg()
    terminations = TerminationsCfg()

    def __post_init__(self):
        self.decimation = 4
        self.episode_length_s = 20.0
        self.sim.dt = 1.0 / 120.0
        self.sim.render_interval = self.decimation
        self.sim.physics = PhysicsCfg()

    def play_mode(self):
        requested = self.scene.num_envs
        super().play_mode()
        self.scene.num_envs = requested
        self.events.reset_from_dataset.params["sequential"] = True


@configclass
class FR3GeneratorEnvCfg(FR3TubeEnvCfg):
    scene = FR3SceneCfg(num_envs=128, env_spacing=1.5, replicate_physics=True)
    actions = GeneratorActionsCfg()
    events = RawEventsCfg()
    rewards = None
    terminations = None


@configclass
class FR3PPORunnerCfg(SO101StatePPORunnerCfg):
    experiment_name = "fr3_tube_contact"

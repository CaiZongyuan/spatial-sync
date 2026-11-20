"""Platform-owned Layer A environment and native state provider for mjlab 1.3."""

import math
import re
from dataclasses import dataclass, field

import torch
from mjlab.envs import ManagerBasedRlEnv, ManagerBasedRlEnvCfg, mdp
from mjlab.envs.mdp.actions import JointPositionAction, JointPositionActionCfg
from mjlab.managers.curriculum_manager import CurriculumTermCfg
from mjlab.managers.event_manager import EventTermCfg
from mjlab.managers.observation_manager import ObservationGroupCfg, ObservationTermCfg
from mjlab.managers.reward_manager import RewardTermCfg
from mjlab.managers.termination_manager import TerminationTermCfg
from mjlab.rl import MjlabOnPolicyRunner, RslRlModelCfg, RslRlOnPolicyRunnerCfg, RslRlPpoAlgorithmCfg
from mjlab.scene import SceneCfg
from mjlab.sim import MujocoCfg, SimulationCfg
from mjlab.terrains import TerrainEntityCfg

if __package__:
    from apps.node import usd_mjcf as usd
    from apps.node.declarative_scene import baked_collision_layout, episode_layout
    from packages.policies.action_contract import action_delta_limit, command_endpoints
    from packages.policies.motion import motion_observations
    from packages.training.reward_state import compile_expression, evaluate, validate_scene_references
else:
    import lw_usd_mjcf as usd
    from lw_action_contract import action_delta_limit, command_endpoints
    from lw_declarative_scene import baked_collision_layout, episode_layout
    from lw_motion import motion_observations
    from lw_reward_state import compile_expression, evaluate, validate_scene_references


def _inverse_rotate(quaternion, vector):
    w, xyz = quaternion[..., :1], -quaternion[..., 1:]
    cross = 2 * torch.linalg.cross(xyz, vector)
    return vector + w * cross + torch.linalg.cross(xyz, cross)


@dataclass(kw_only=True)
class ActionConfig(JointPositionActionCfg):
    action_spec: dict
    physical_limits: list

    def build(self, env):
        return PositionAction(self, env)


class PositionAction(JointPositionAction):
    def __init__(self, cfg, env):
        super().__init__(cfg, env)
        self.spec = cfg.action_spec
        self.scale_vector = torch.tensor(self.spec["scale"], device=self.device)
        self.default = torch.tensor(self.spec.get("defaultPosition") or [], device=self.device)
        limit = action_delta_limit(self.spec)
        self.limit = torch.tensor(
            limit if limit is not None else [math.inf] * self.action_dim, device=self.device
        )
        self.lower = torch.tensor(self.spec["lower"], device=self.device)
        self.upper = torch.tensor(self.spec["upper"], device=self.device)
        self.physical = torch.tensor(cfg.physical_limits, device=self.device)

    def process_actions(self, actions):
        self._raw_actions[:] = actions
        current = self._entity.data.joint_pos[:, self._target_ids]
        scaled = actions * self.scale_vector
        delta = scaled if self.spec["space"] == "joint_position_delta" else self.default + scaled - current
        declared = (current + delta.clamp(-self.limit, self.limit)).clamp(self.lower, self.upper)
        self._processed_actions = declared.clamp(self.physical[:, 0], self.physical[:, 1])


@dataclass(kw_only=True)
class EnvironmentConfig(ManagerBasedRlEnvCfg):
    contract: dict = field(default_factory=dict)


class DeclarativeEnvironment(ManagerBasedRlEnv):
    def __init__(self, cfg, device, **kwargs):
        self.contract = cfg.contract
        self._indices = {}
        self._motion_tables = {}
        self.validate_reward_state = False
        self.level = torch.zeros(cfg.scene.num_envs, device=device, dtype=torch.long)
        self.target = torch.zeros((cfg.scene.num_envs, 3), device=device)
        self._random = torch.Generator(device=device).manual_seed(cfg.seed or 0)
        self.command = torch.zeros((cfg.scene.num_envs, 3), device=device, dtype=torch.float32)
        command = cfg.contract["declarative"]["command"]
        if command:
            self.command_bounds = self.command.new_tensor(command_endpoints(command))
        super().__init__(cfg, device, **kwargs)

    def enable_reward_validation(self):
        self.validate_reward_state = True
        omitted = tuple(
            term.params["expression"] for term in self.cfg.rewards.values()
            if term.func is expression_term and term.weight == 0.0
        )
        if omitted:
            compute = self.reward_manager.compute

            def validate_and_compute(dt):
                # Use the same post-step state as the reward terms, before auto-reset.
                for expression in omitted:
                    expression_term(self, expression)
                return compute(dt)

            self.reward_manager.compute = validate_and_compute

    def indices(self, kind, keys):
        cache_key = (kind, tuple(keys))
        if cache_key not in self._indices:
            entity = self.scene["robot"]
            available = getattr(entity, kind + "_names")
            mapping = self.contract[kind]
            names = [mapping[key] for key in keys]
            self._indices[cache_key] = [available.index(name) for name in names]
        return self._indices[cache_key]

    def pose(self, key, *, site=False):
        (index,) = self.indices("site" if site else "body", [key])
        data = self.scene["robot"].data
        if site:
            return data.site_pos_w[:, index] - self.scene.env_origins, data.site_quat_w[:, index]
        return data.body_link_pos_w[:, index] - self.scene.env_origins, data.body_link_quat_w[:, index]

    def motion_values(self, source):
        if source not in self._motion_tables:
            count = math.ceil(self.contract["declarative"]["episodeSeconds"] / self.step_dt) + 1
            rows = [
                motion_observations(self.contract["motion"], i * self.step_dt)[source] for i in range(count)
            ]
            self._motion_tables[source] = torch.tensor(rows, device=self.device)
        table = self._motion_tables[source]
        return table[self.episode_length_buf.clamp(max=len(table) - 1)]

    def episode_command(self):
        layout = self.contract["layout"]
        if self.contract["declarative"]["command"] and (layout["targets"] or layout["levels"]):
            position, rotation = self.pose(self.contract["declarative"]["baseBody"])
            displacement = _inverse_rotate(rotation, self.target - position)
            heading = torch.atan2(displacement[:, 1], displacement[:, 0])
            target = torch.cat((displacement[:, :2], heading[:, None]), dim=1)
            low, high = self.command_bounds.T
            return target.clamp(low, high)
        return self.command

    def reward_state(self, name, key):
        if name == "command":
            return self.episode_command()
        if name == "episode_time":
            return self.episode_length_buf.to(self.target.dtype) * self.step_dt
        if name == "target_position_world":
            return self.target
        if name.startswith("prop_"):
            entity = self.scene[self.contract["layout"]["props"][key][0]]
            if name == "prop_position_world":
                return entity.data.root_link_pos_w - self.scene.env_origins
            return entity.data.root_link_quat_w[:, [1, 2, 3, 0]]
        base = self.contract["declarative"]["baseBody"]
        (index,) = self.indices("body", [base])
        data = self.scene["robot"].data
        position, quaternion = self.pose(base)
        if name == "base_position_world":
            return position
        if name == "base_orientation_world":
            return quaternion[:, [1, 2, 3, 0]]
        velocity = (data.body_link_lin_vel_w if "linear" in name else data.body_link_ang_vel_w)[:, index]
        return _inverse_rotate(quaternion, velocity) if name.endswith("_body") else velocity

    def lw_policy_observation_state(self, indices):
        """Read physical state directly at the observer's step, independent of actor tensor assembly."""

        def read(values):
            return values[indices].detach().cpu().tolist()

        entity = self.scene["robot"]
        states = [{"joints": {}, "bodies": {}, "sites": {}} for _ in indices]
        for key in self.contract["joint"]:
            (index,) = self.indices("joint", [key])
            for row, q, qd in zip(
                states,
                read(entity.data.joint_pos[:, index]),
                read(entity.data.joint_vel[:, index]),
                strict=True,
            ):
                row["joints"][key] = {"q": q, "qd": qd}
        for kind in ("body", "site"):
            for key in self.contract[kind]:
                (index,) = self.indices(kind, [key])
                if kind == "body":
                    pos, quat = entity.data.body_link_pos_w[:, index], entity.data.body_link_quat_w[:, index]
                    angular = read(entity.data.body_link_ang_vel_w[:, index])
                else:
                    pos, quat = entity.data.site_pos_w[:, index], entity.data.site_quat_w[:, index]
                    angular = [None] * len(indices)
                for row, p, q, w in zip(
                    states, read(pos - self.scene.env_origins), read(quat), angular, strict=True
                ):
                    value = {"p": p, "q": [q[1], q[2], q[3], q[0]]}
                    if w is not None:
                        value["w"] = w
                    row["bodies" if kind == "body" else "sites"][key] = value
        if self.contract["motion"] is not None:
            for row, steps in zip(states, read(self.episode_length_buf), strict=True):
                row.update(motionStep=steps, motionElapsed=steps * self.step_dt)
        commands = (
            read(self.episode_command()) if self.contract["declarative"]["command"] else [None] * len(indices)
        )
        return [
            {"state": state, **({"command": command} if command is not None else {})}
            for state, command in zip(states, commands, strict=True)
        ]


def observe(env, item):
    source, key, frame = item["source"], item.get("key"), item.get("frame")
    data = env.scene["robot"].data
    if source == "previous_action":
        value = env.action_manager.action
    elif source == "command":
        value = env.episode_command()
    elif source.startswith("joint_"):
        columns = env.indices("joint", item["keys"])
        value = (data.joint_pos if source == "joint_position" else data.joint_vel)[:, columns]
    elif source in ("motion_phase", "reference_motion_frame"):
        value = env.motion_values(source)
    elif source == "body_angular_velocity":
        (index,) = env.indices("body", [key])
        value = data.body_link_ang_vel_w[:, index]
        if frame != "world":
            value = _inverse_rotate(env.pose(key if frame == "body" else frame)[1], value)
    elif source == "projected_gravity":
        quaternion = env.pose(key)[1]
        vector = torch.tensor([0.0, 0.0, -1.0], device=env.device).expand(len(quaternion), 3)
        value = _inverse_rotate(quaternion, vector)
    else:
        position, quaternion = env.pose(key, site=source.startswith("site_"))
        if source.endswith("_orientation"):
            value = quaternion[:, [1, 2, 3, 0]]
        elif frame == "world":
            value = position
        else:
            origin, rotation = env.pose(frame)
            value = _inverse_rotate(rotation, position - origin)
    return (value - value.new_tensor(item["offset"])) * value.new_tensor(item["scale"])


def _upright(env):
    _, q = env.pose(env.contract["declarative"]["baseBody"])
    return 1 - 2 * (q[:, 1].square() + q[:, 2].square())


def reward(env, term):
    kind = term["kind"]
    if kind == "upright":
        return _upright(env)
    if kind == "height":
        return -(env.pose(env.contract["declarative"]["baseBody"])[0][:, 2] - term["target"]).square()
    if kind == "joint_tracking":
        motion = env.contract["motion"]
        prefix = env.contract["robot"]
        keys = [prefix + path.removeprefix("/Asset") for path in motion["jointKeys"]]
        positions = env.scene["robot"].data.joint_pos[:, env.indices("joint", keys)]
        return -(positions - env.motion_values("reference_motion_frame")[:, 7:]).square().sum(dim=1)
    if kind == "velocity_tracking":
        key = env.contract["declarative"]["baseBody"]
        (index,) = env.indices("body", [key])
        data = env.scene["robot"].data
        q = data.body_link_quat_w[:, index]
        linear = _inverse_rotate(q, data.body_link_lin_vel_w[:, index])
        angular = _inverse_rotate(q, data.body_link_ang_vel_w[:, index])
        actual = torch.cat((linear[:, :2], angular[:, 2:3]), dim=1)
        return -(actual - env.episode_command()).square().sum(dim=1)
    if kind == "action_rate":
        return -(env.action_manager.action - env.action_manager.prev_action).square().sum(dim=1)
    raise ValueError("Unknown Layer A reward")


def terminate(env, term):
    if term["kind"] == "tilt":
        return _upright(env) < math.cos(math.radians(term["threshold"]))
    if term["kind"] == "fall_height":
        return env.pose(env.contract["declarative"]["baseBody"])[0][:, 2] < term["threshold"]
    raise ValueError("Unknown Layer A termination")


def expression_term(env, expression):
    return evaluate(
        expression, env.reward_state, env.target[:, 0], reject_nonfinite=env.validate_reward_state
    )


def _add_yaw(state, yaw):
    w, x, y, z = state[:, 3:7].unbind(dim=-1)
    c, s = (yaw / 2).cos(), (yaw / 2).sin()
    state[:, 3:7] = torch.stack((c * w - s * z, c * x - s * y, c * y + s * x, c * z + s * w), dim=-1)


def _reset_layout(env, env_ids, levels):
    layout = env.contract["layout"]
    if levels:
        robot = env.scene["robot"]
        choices = env.level[env_ids]
        positions = env.target.new_tensor([entry[0][0] for entry in levels])
        angles = env.target.new_tensor([entry[0][1] for entry in levels])
        state = robot.data.default_root_state[env_ids].clone()
        state[:, :2] = positions[choices, :2] + env.scene.env_origins[env_ids, :2]
        state[:, 2] += positions[choices, 2] + env.scene.env_origins[env_ids, 2]
        _add_yaw(state, angles[choices])
        robot.write_root_state_to_sim(state, env_ids=env_ids)
        env.target[env_ids] = env.target.new_tensor([entry[1] for entry in levels])[choices]
    elif targets := layout["targets"]:
        draws = torch.randint(len(targets), (len(env_ids),), device=env.device, generator=env._random)
        env.target[env_ids] = env.target.new_tensor(targets)[draws]
    for key, bounds in layout["resets"].items():
        entity = env.scene[layout["props"][key][0]]
        low, high = env.target.new_tensor([*bounds["translation"], bounds["yaw"]]).T
        samples = torch.rand((len(env_ids), 4), device=env.device, generator=env._random)
        delta = torch.lerp(low, high, samples)
        state = entity.data.default_root_state[env_ids].clone()
        state[:, :3] += delta[:, :3] + env.scene.env_origins[env_ids]
        _add_yaw(state, delta[:, 3])
        entity.write_root_state_to_sim(state, env_ids=env_ids)


def advance_terrain_level(env, env_ids):
    levels = env.contract["layout"]["levels"]
    settings = env.contract["declarative"]["curriculum"]
    previous = env.episode_length_buf[env_ids] > 0
    position = env.scene["robot"].data.root_link_pos_w[env_ids] - env.scene.env_origins[env_ids]
    distance = torch.linalg.vector_norm(position - env.target[env_ids], dim=-1)
    move = (distance <= settings["successDistance"]).long() - (distance > settings["demoteDistance"]).long()
    env.level[env_ids] = (env.level[env_ids] + previous * move).clamp(0, len(levels) - 1)
    return env.level.float().mean()


def reset(env, env_ids):
    declaration = env.contract["declarative"]
    levels = env.contract["layout"]["levels"]
    mdp.reset_scene_to_default(env, env_ids)
    _reset_layout(env, env_ids, levels)
    if declaration["command"] and not (levels or env.contract["layout"]["targets"]):
        low, high = env.command_bounds.T
        weight = torch.rand((len(env_ids), 3), device=env.device, generator=env._random)
        env.command[env_ids] = torch.lerp(low.double(), high.double(), weight.double()).to(env.command.dtype)
    offsets = declaration["reset"]["jointPosition"]
    velocity = declaration["reset"]["jointVelocity"]
    if offsets or velocity:
        action = env.contract["action"]
        joint_ids = torch.tensor(env.indices("joint", action["jointKeys"]), device=env.device)
        robot = env.scene["robot"]
        shape = (len(env_ids), len(joint_ids))
        position = robot.data.default_joint_pos[env_ids[:, None], joint_ids]
        position += (2 * torch.rand(shape, device=env.device, generator=env._random) - 1) * (
            position.new_tensor(offsets) if offsets else 0
        )
        position = position.clamp(position.new_tensor(action["lower"]), position.new_tensor(action["upper"]))
        speed = (2 * torch.rand(shape, device=env.device, generator=env._random) - 1) * velocity
        robot.write_joint_state_to_sim(position, speed, joint_ids=joint_ids, env_ids=env_ids)


def compile_environment(task, spec, stage_path):
    settings, robot = task["declarative"], task["robot"]
    stage, _ = usd._open_usd_stage(stage_path, None)
    derived = usd.derive_usd_mjcf(
        stage, prim_path=robot, include_ground_plane=False, include_static_colliders=False
    )
    usd.verify_against_observation_spec(derived, spec, entity_key=robot)
    action = spec["action"]
    by_joint = {joint.prim_path: joint for joint in derived.joints}
    joints = [by_joint[key] for key in action["jointKeys"]]
    physical = [(joint.lower, joint.upper) if joint.limited else (-math.inf, math.inf) for joint in joints]
    motion = task.get("referenceMotion")
    if motion:
        for index, key in enumerate(motion["jointKeys"]):
            joint = by_joint[robot + key.removeprefix("/Asset")]
            if joint.limited and any(
                not joint.lower <= row["joints"][index] <= joint.upper for row in motion["frames"]
            ):
                raise ValueError("Reference Motion joint target exceeds actual physical limits")

    from pxr import Usd

    declared_sites = {item["key"] for item in spec["observations"] if item["source"].startswith("site_")}
    prims = list(Usd.PrimRange(stage.GetPrimAtPath(robot), Usd.TraverseInstanceProxies()))
    sites = {}
    for path in declared_sites:
        prim = stage.GetPrimAtPath(path)
        name = prim.GetName() if prim else None
        if name not in derived.sites or sum(p.GetName() == name for p in prims) != 1:
            raise ValueError(f"Site {path} has no unique derived identity")
        sites[path] = name
    bodies = {settings["baseBody"]}
    for item in spec["observations"]:
        if item["source"].startswith("body_") or item["source"] == "projected_gravity":
            bodies.add(item["key"])
        if str(item.get("frame")).startswith("/"):
            bodies.add(item["frame"])
    by_body = {key: derived.body_names_by_path[key] for key in bodies}
    entities, compose, props = baked_collision_layout(stage, robot, derived)
    layout = episode_layout(stage, settings, props)
    if layout["levels"] and derived.fixed_base:
        raise ValueError("curriculum requires a floating robot")
    layer_b = settings.get("layerB") or {}
    expressions = [
        compile_expression(row["expression"], path=f"layerB.rewards[{index}].expression")
        for index, row in enumerate(layer_b.get("rewards", ()))
    ]
    stop_expressions = [
        compile_expression(row, "boolean", f"layerB.terminations[{index}]")
        for index, row in enumerate(layer_b.get("terminations", ()))
    ]
    for expression in (*expressions, *stop_expressions):
        validate_scene_references(expression, props)
    group = ObservationGroupCfg(
        terms={
            item["name"]: ObservationTermCfg(func=observe, params={"item": item})
            for item in spec["observations"]
        },
        concatenate_terms=True,
        enable_corruption=False,
    )
    backend = task["mjlab"]
    if settings["episodeSeconds"] / (backend["physics_dt"] * backend["decimation"]) > 100_000:
        raise ValueError("Episode exceeds managed control-step budget")
    cfg = EnvironmentConfig(
        contract={
            "declarative": settings,
            "action": action,
            "motion": motion,
            "body": by_body,
            "site": sites,
            "joint": {key: value.name for key, value in by_joint.items()},
            "robot": robot,
            "layout": layout,
            "bakedLayout": compose is not None,
        },
        scene=SceneCfg(
            terrain=TerrainEntityCfg(terrain_type="plane") if compose is None else None,
            entities=entities,
            spec_fn=compose,
        ),
        observations={"actor": group, "critic": group},
        actions={
            "joint_pos": ActionConfig(
                entity_name="robot",
                actuator_names=[re.escape(j.name) for j in joints],
                preserve_order=True,
                use_default_offset=False,
                action_spec=action,
                physical_limits=physical,
            )
        },
        rewards={
            **{
                f"{term['kind']}-{index}": RewardTermCfg(
                    func=reward, weight=term["weight"], params={"term": term}
                )
                for index, term in enumerate(settings["rewards"])
            },
            **{
                f"expression-{index}": RewardTermCfg(
                    func=expression_term,
                    weight=layer_b["rewards"][index]["weight"],
                    params={"expression": expression},
                )
                for index, expression in enumerate(expressions)
            },
        },
        terminations={
            "timeout": TerminationTermCfg(func=mdp.time_out, time_out=True),
            **{
                f"expression-{index}": TerminationTermCfg(
                    func=expression_term,
                    params={"expression": expression},
                )
                for index, expression in enumerate(stop_expressions)
            },
            **{
                f"{term['kind']}-{index}": TerminationTermCfg(func=terminate, params={"term": term})
                for index, term in enumerate(settings["terminations"])
            },
        },
        events={"reset": EventTermCfg(func=reset, mode="reset")},
        curriculum={"terrain": CurriculumTermCfg(func=advance_terrain_level)} if layout["levels"] else {},
        decimation=backend["decimation"],
        episode_length_s=settings["episodeSeconds"],
        sim=SimulationCfg(mujoco=MujocoCfg(timestep=backend["physics_dt"], integrator="implicitfast")),
    )
    cfg.class_type = DeclarativeEnvironment
    agent = RslRlOnPolicyRunnerCfg(
        actor=RslRlModelCfg(
            obs_normalization=True,
            distribution_cfg={
                "class_name": "GaussianDistribution",
                "init_std": 1.0,
                "std_type": "scalar",
            },
        ),
        critic=RslRlModelCfg(obs_normalization=True),
        algorithm=RslRlPpoAlgorithmCfg(),
        experiment_name=task["name"],
        clip_actions=None,
        upload_model=False,
    )
    return cfg, agent, MjlabOnPolicyRunner

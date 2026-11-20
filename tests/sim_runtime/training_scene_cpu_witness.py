"""Run with the pinned Linux mjlab Python and PYTHONPATH set to the repository root."""

import os
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

import mujoco
import torch
import yaml

from apps.node.declarative_mjlab import compile_environment, expression_term
from packages.library.common import content_hash
from packages.library.scene_drafts import SceneDraft, compose
from packages.training.declarative import LayerA
from packages.training.reward_state import compile_expression
from tests.fixtures.training_scene import draft


class Assets:
    def __init__(self, path):
        self.path = path

    def resolve(self, key):
        from types import SimpleNamespace

        assert key == "robot/microduck@1"
        return SimpleNamespace(
            path=self.path,
            content_hash=content_hash(self.path),
            metadata={"stage": "asset.usdc"},
        )


def main():
    root = Path(__file__).resolve().parents[2]
    pkg = root / "library/tasks/microduck-stand-up/1/package"
    task = yaml.safe_load((pkg / "task.yaml").read_bytes())
    spec = yaml.safe_load((pkg / "obs_spec.yaml").read_bytes())
    data = draft()
    asset = root / "library/assets/robot/microduck/1"
    data["assets"] = [{"name": "Microduck", "asset": "robot/microduck@1", "contentHash": content_hash(asset)}]
    data["waypoints"]["upper"] = {"p": [1.5, 0, 0.2], "yaw": 0.0}
    data["sites"].append({"name": "Near", "position": [0, 0, 0.22]})
    ball_position = {"state": "prop_position_world", "key": "/World/Lab/Ball"}
    settings = LayerA.model_validate(
        {
            "baseBody": task["robot"] + "/trunk_base",
            "episodeSeconds": 3.0,
            "rewards": [],
            "rigidReset": [{"key": "/World/Lab/Ball", "translation": [[0.15, 0.15], [0, 0], [0, 0]]}],
            "curriculum": {
                "levels": [
                    {"spawn": "/World/Lab/Waypoints/spawn", "target": "/World/Lab/Sites/Near"},
                    {"spawn": "/World/Lab/Waypoints/upper", "target": "/World/Lab/Sites/Goal"},
                ],
                "successDistance": 0.25,
                "demoteDistance": 0.5,
            },
            "layerB": {
                "state": "lw.reward-state/1",
                "rewards": [{"weight": 1.0, "expression": {"op": "component", "args": [ball_position, 0]}}],
            },
        }
    )
    task["declarative"] = settings.model_dump(mode="json", by_alias=True)
    os.environ["LW_ASSET_PACKAGES"] = (
        '{"robot/microduck@1":"' + str(root / "library/assets/robot/microduck/1") + '"}'
    )
    with TemporaryDirectory() as temporary:
        stage = Path(temporary) / "stage.usda"
        compose(stage, SceneDraft.model_validate(data), Assets(asset))
        from apps.node.usd_mjcf import _open_usd_stage

        no_sites, anchors = _open_usd_stage(stage, None)
        assert anchors
        no_sites.RemovePrim("/World/Lab/Sites")
        without_goals = dict(task)
        without_goals["declarative"] = {
            key: value for key, value in task["declarative"].items() if key != "curriculum"
        }
        old_cfg, _, _ = compile_environment(without_goals, spec, no_sites)
        assert old_cfg.contract["layout"]["targets"] == []

        invalid = deepcopy(task)
        invalid["declarative"]["layerB"]["rewards"][0]["expression"] = {
            "op": "add",
            "args": [
                1,
                {
                    "op": "component",
                    "args": [
                        {"state": "prop_position_world", "key": "/World/Lab/Missing"},
                        0,
                    ],
                },
            ],
        }
        try:
            compile_environment(invalid, spec, stage)
        except ValueError as error:
            assert str(error).startswith("layerB.rewards[0].expression.args[1].args[0]: ")
            print(str(error))
        else:
            raise AssertionError("A missing nested rigid reference was compiled")
        cfg, _, _ = compile_environment(task, spec, stage)
        cfg.scene.num_envs = 2
        cfg.seed = 123
        env = cfg.class_type(cfg=cfg, device="cpu")
        try:
            env.reset()
            model = env.sim.mj_model
            step = model.geom("layout/Step2")
            assert step.type[0] == mujoco.mjtGeom.mjGEOM_BOX
            assert abs(step.pos[2] + step.size[2] - 0.20) < 1e-6
            assert abs(float(model.body("prop0/Ball").mass[0]) - 0.25) < 1e-6
            assert tuple(model.site("layout/site/Goal").pos) == (1.5, 0.0, 0.2)
            ball = env.scene["prop0"].data.root_link_pos_w
            torch.testing.assert_close(ball[:, 0], torch.tensor([0.65, 0.65]))
            result = expression_term(env, compile_expression({"op": "component", "args": [ball_position, 0]}))
            torch.testing.assert_close(result, torch.tensor([0.65, 0.65]))
            fault = compile_expression({"op": "div", "args": [1, 0]})
            torch.testing.assert_close(expression_term(env, fault), torch.full((2,), 10000.0))
            env.validate_reward_state = True
            try:
                expression_term(env, fault)
            except ValueError as error:
                assert str(error) == "reward: div produced a non-finite value"
            else:
                raise AssertionError("Plan Validation did not report a Layer B fault")
            env.step(torch.zeros((2, len(spec["action"]["jointKeys"]))))
            print("2 CPU worlds: Step2 top=0.20 m, Ball mass=0.25 kg, reset/reward=[0.65,0.65]")
            robot = env.scene["robot"]
            state = robot.data.default_root_state[[0, 1]].clone()
            state[0, :3] = env.target[0]
            state[1, 0] = -0.8
            robot.write_root_state_to_sim(state, env_ids=torch.tensor([0, 1], dtype=torch.int32))
            env.sim.forward()
            env.scene.update(env.step_dt)
            env.reset()
            assert env.level.tolist() == [1, 0]
            torch.testing.assert_close(env.target[:, 0], torch.tensor([1.5, 0.0]))
            torch.testing.assert_close(robot.data.root_link_pos_w[:, 0], torch.tensor([1.5, 0.0]))
            state[0, 0] = -0.8
            robot.write_root_state_to_sim(state, env_ids=torch.tensor([0, 1], dtype=torch.int32))
            env.sim.forward()
            env.scene.update(env.step_dt)
            env.step(torch.zeros((2, len(spec["action"]["jointKeys"]))))
            env.reset()
            assert env.level.tolist() == [0, 0]
            print("Division reports its AST path; physical reach promotes one world and distance demotes it")
        finally:
            env.close()


if __name__ == "__main__":
    main()

"""Gripper engineering Suite for permission integration, separate from tube-transfer quality."""

from copy import deepcopy

import yaml

from packages.library.suites import SuiteLibrary


def gripper_promotion_suite(manager, scene):
    root = manager.library_assets.roots["user"] / "suites/script-deployment-engineering/1"
    (root / "private").mkdir(parents=True)
    params = {"from": "rack.A1", "to": "centrifuge.slot1"}
    goal = {"entity": "/World/Lab/RobotA", "property": "gripperWidth", "lte": 0.02}
    metadata = {
        "id": "suite/script-deployment-engineering",
        "title": "Gripper permission engineering",
        "layer": "embodied",
        "scene": scene.id,
        "sceneContentHash": scene.content_hash,
        "source": "newton",
        "episodes": [
            {"id": "train-one", "split": "train", "params": params},
            {"id": "heldout-one", "split": "test", "params": deepcopy(params)},
        ],
    }
    task = {
        "name": "script-deployment-engineering",
        "scene": scene.id,
        "robot": "/World/Lab/RobotA",
        "implements": "robot-arm/v1.pickPlace",
        "timeoutSeconds": 45.0,
        "success": goal,
        "milestones": [{"id": "close", "when": deepcopy(goal)}],
    }
    for name, value in (
        ("suite.yaml", metadata),
        ("task.yaml", task),
        ("private/seeds.yaml", {"train-one": 7, "heldout-one": 9}),
    ):
        (root / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    suites = SuiteLibrary(manager.library_assets.roots)
    suites.refresh()
    return suites.resolve(metadata["id"] + "@1")

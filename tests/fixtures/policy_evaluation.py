"""Fixed native Suite fixture; its numerical policy has no trained quality claim."""

from copy import deepcopy

import yaml

from packages.library.suites import SuiteLibrary


def native_suite(manager, scene, *, name="policy-probe", timeout=5.0):
    root = manager.library_assets.roots["user"] / "suites" / name / "1"
    (root / "private").mkdir(parents=True)
    metadata = {
        "id": "suite/" + name,
        "title": "CPU policy engineering probe",
        "layer": "embodied",
        "scene": scene.id,
        "sceneContentHash": scene.content_hash,
        "source": "newton",
        "episodes": [
            {"id": "train-one", "split": "train", "params": {"from": "rack.A1", "to": "centrifuge.slot1"}}
        ],
    }
    goal = {
        "entity": "/World/Lab/Tubes/Tube01",
        "property": "location",
        "equals": "/World/Lab/Centrifuge/Rotor/Slot1",
    }
    task = {
        "name": name,
        "scene": scene.id,
        "robot": "/World/Lab/RobotA",
        "implements": "robot-arm/v1.pickPlace",
        "timeoutSeconds": timeout,
        "success": goal,
        "milestones": [{"id": "place", "when": deepcopy(goal)}],
    }
    for name, value in (
        ("suite.yaml", metadata),
        ("task.yaml", task),
        ("private/seeds.yaml", {"train-one": 7}),
    ):
        (root / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    suites = SuiteLibrary(manager.library_assets.roots)
    suites.refresh()
    return suites.resolve(metadata["id"] + "@1")

"""Fixed native Suite fixture; its numerical policy has no trained quality claim."""

from copy import deepcopy

import yaml

from packages.library.suites import SuiteLibrary
from tests.fixtures.policy import observation_spec
from tests.fixtures.training_task import task_document


def finger_probe_inputs():
    """A finite controller with an intentionally insufficient training goal."""
    spec = observation_spec()
    finger = "/World/Lab/RobotA/fr3/fr3_finger_joint1"
    spec["observations"][0].update(keys=[finger], unit="m", scale=[1.0])
    spec["action"].update(
        jointKeys=[finger], units=["m"], scale=[-1.0], lower=[0.0], upper=[0.04], maxDelta=[0.005]
    )
    task = task_document()
    task["success"] = {"property": "gripperWidth", "lte": 0.06}
    return spec, task


def native_suite(manager, scene, *, name="policy-probe", timeout=5.0, heldout=False):
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
    seeds = {"train-one": 7}
    if heldout:
        metadata["episodes"].append({"id": "test-one", "split": "test",
                                     "params": {"from": "rack.A2", "to": "centrifuge.slot2"}})
        seeds["test-one"] = 11
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
        ("private/seeds.yaml", seeds),
    ):
        (root / name).write_text(yaml.safe_dump(value), encoding="utf-8")
    suites = SuiteLibrary(manager.library_assets.roots)
    suites.refresh()
    return suites.resolve(metadata["id"] + "@1")

"""Fixed native Suite fixture; its numerical policy has no trained quality claim."""

from tests.fixtures.embodied_evaluation import native_suite as native_suite
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

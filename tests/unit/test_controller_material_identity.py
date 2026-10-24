"""A runtime implementation alone is not an immutable uploaded Device Program."""

import pytest
from pydantic import ValidationError

from packages.library.robot_resources import ControllerVersion
from packages.programs.metadata import ProgramIntent

BASE = {
    "controller": "fr3-arm", "version": 1, "embodiment": "fr3-sim", "robotModel": "fr3-taught-motion",
    "policy": None, "program": "robot_arm.newton", "action": {"space": "taught", "hz": None},
    "observation": None, "task": None, "scene": "robot-and-tubes@2", "evaluationId": None,
}
ARTIFACT = {"id": "program/robot_arm.script.probe@1", "contentHash": "a" * 64}


@pytest.mark.parametrize("program,policy", [
    ("robot_arm.newton", None), ("robot_arm.policy", "policy/probe@1")
])
def test_older_controller_documents_remain_readable_without_a_script_artifact(program, policy):
    value = ControllerVersion.model_validate(BASE | {"program": program, "policy": policy})
    assert value.programArtifact is None


def test_script_controller_keeps_runtime_and_exact_program_material_separate():
    value = ControllerVersion.model_validate(
        BASE | {"program": "robot_arm.script", "programArtifact": ARTIFACT}
    )
    assert isinstance(value.programArtifact, ProgramIntent)
    assert value.program == "robot_arm.script" and value.programArtifact.model_dump() == ARTIFACT
    with pytest.raises(ValidationError):
        ControllerVersion.model_validate(BASE | {"program": "robot_arm.script"})
    with pytest.raises(ValidationError):
        ControllerVersion.model_validate(BASE | {"programArtifact": ARTIFACT})
    with pytest.raises(ValidationError):
        ControllerVersion.model_validate(BASE | {"program": "robot_arm.script", "programArtifact": ARTIFACT,
                                               "policy": "policy/probe@1"})

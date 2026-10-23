"""Shipped Task conditions must agree with completed placement feedback."""

import struct
from pathlib import Path

import pytest
import yaml

from packages.info.conditions import condition_state, validate_conditions
from packages.library.common import TYPE_ROOT, document
from tests.fixtures.training_task import task_document

ROBOT = "/World/Lab/RobotA"
TUBE = "/World/Lab/Tubes/Tube01"
SLOT = "/World/Lab/Centrifuge/Rotor/Slot1"


@pytest.mark.parametrize("example", ["package", "specification"])
def test_shipped_success_accepts_placed_open_feedback_and_rejects_incomplete_work(example):
    if example == "package":
        task = task_document()
    else:
        specification = Path(__file__).resolve().parents[2] / "docs/spec/LAB_TRAINING_SPEC_V1.md"
        source = specification.read_text(encoding="utf-8").split("```yaml\n", 1)[1].split("```", 1)[0]
        task = yaml.safe_load(source)
    types = {key: document(TYPE_ROOT / filename)["properties"] for key, filename in (
        (ROBOT, "robot-arm.v1.yaml"), (TUBE, "tube.v1.yaml"),
    )}
    compiled = validate_conditions([task["success"]], ROBOT, lambda key, prop: types[key][prop]["schema"])
    opened = struct.unpack("f", struct.pack("f", 0.08))[0]
    completed = {ROBOT: {"gripperWidth": opened}, TUBE: {"location": SLOT}}
    assert condition_state(compiled, ROBOT, completed) is True
    assert condition_state(compiled, ROBOT, {**completed, ROBOT: {"gripperWidth": 0.04}}) is False
    assert condition_state(compiled, ROBOT, {**completed, TUBE: {"location": "held"}}) is False
    assert condition_state(compiled, ROBOT, {**completed, TUBE: {"location": None}}) is False
    assert condition_state(compiled, ROBOT, {**completed,
                                           TUBE: {"location": "/World/Lab/Rack/A1"}}) is False

import json
import math
from pathlib import Path

import pytest

from packages.protocol.math import (
    compose_pose, joint_position_error, normalize_entity_key, normalize_quaternion,
    primitive_dimensions, same_hemisphere, z_up_to_y_up,
)

FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "packages/protocol/fixtures/math.json").read_text())


@pytest.mark.parametrize("case", FIXTURE["primitiveCases"])
def test_primitive_conventions(case):
    assert primitive_dimensions(case["geometry"]) == case["expected"]


def test_body_times_local_site_and_visual():
    for case in FIXTURE["poseCases"]:
        actual = compose_pose(case["body"], case["local"])
        assert actual["p"] == pytest.approx(case["expected"]["p"])
        assert actual["q"] == pytest.approx(case["expected"]["q"])
        assert actual["p"] != case["body"]["p"]


def test_z_up_to_y_up_and_quaternion_hemisphere():
    for case in FIXTURE["axisCases"]:
        assert z_up_to_y_up(case["input"]) == tuple(case["expected"])
    for case in FIXTURE["hemisphereCases"]:
        assert same_hemisphere(case["previous"], case["current"]) == tuple(case["expected"])
    assert normalize_quaternion([0, 0, 0, 2]) == (0, 0, 0, 1)
    with pytest.raises(ValueError):
        normalize_quaternion([0, 0, 0, 0])


@pytest.mark.parametrize("label", ["", "/", "World//Body", "World/Body/", "a?b", "a#b"])
def test_invalid_labels(label):
    with pytest.raises(ValueError):
        normalize_entity_key(label)


def test_label_path_is_case_sensitive():
    assert normalize_entity_key("World/Body") == "/World/Body"
    assert normalize_entity_key("/World/body") == "/World/body"


@pytest.mark.parametrize("kind,expected", [("fixed", math.sqrt(5)), ("revolute", math.sqrt(5)), ("ball", math.sqrt(5)), ("prismatic", 1), ("free", None), ("d6", None)])
def test_joint_position_error_removes_allowed_travel_in_parent_anchor_frame(kind, expected):
    parent = {"p": [0, 0, 0], "q": [0, 0, math.sqrt(0.5), math.sqrt(0.5)]}
    child = {"p": [1, 2, 0], "q": [0, 0, 0, 1]}
    actual = joint_position_error(kind, parent, child, [1, 0, 0])
    if expected is None:
        assert actual is None
    else:
        assert actual == pytest.approx(expected)

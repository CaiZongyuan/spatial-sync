"""Explicit approach inputs and the actual #18 B3 joint-limit regression."""

import sys
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from tools import teach

B3_GRASP = [-.776822552533618, 1.4013246932516596, -.5089849418874065,
            -2.7476659566033637, 1.0993200198114903, 4.5167999339506295, -1.3038719786062662]
B3_BOTTOM = [.02800000086426735, .36399999260902405, .9620000123977661]


def b3_scene():
    base = np.eye(4)
    base[:3, 3] = [.2, .7, .95]
    return teach.TeachingScene(
        teach.Teacher(base=base),
        {"graspHeightM": .1, "locations": {"locations": {
            "rack.B3": {"site": "/World/Lab/Rack/B3", "zone": "rack", "grasp": B3_GRASP,
                        "approach": B3_GRASP, "requires": []}}}},
        {"rack.B3": B3_BOTTOM}, {"sceneVersion": "fr3-lab@1", "sceneContentHash": "a" * 64}, (),
    )


def test_b3_explicit_100mm_approach_keeps_strict_fk_and_original_scene():
    pytest.importorskip("scipy", reason="Numerical teaching requires the simulation extra")
    selection = b3_scene()
    before = deepcopy(selection.config)
    document, evidence = teach.teach_selected(selection, approach_height=.1)
    location = document["locations"]["rack.B3"]
    grasp = selection.teacher.fk(location["grasp"])
    approach = selection.teacher.fk(location["approach"])
    assert np.linalg.norm(approach[:3, 3] - (grasp[:3, 3] + [0, 0, .1])) < .0001
    np.testing.assert_allclose(approach[:3, :3], grasp[:3, :3], atol=.0005)
    assert evidence[0]["approachHeightM"] == .1
    assert evidence[0]["approachTcpErrorM"] < .0001
    assert evidence[0]["validationScope"] == "offline-urdf-fk"
    assert evidence[0]["newtonP6Verified"] is False
    assert selection.config == before


def test_b3_default_height_still_rejects_failed_solver_with_specific_diagnostics():
    pytest.importorskip("scipy", reason="Numerical teaching requires the simulation extra")
    with pytest.raises(teach.TeachingError, match="Bounded IK") as failure:
        teach.teach_selected(b3_scene())
    measurements = failure.value.measurements
    assert measurements["positionErrorM"] > .0001
    assert measurements["weightedResidual"] > .0001
    assert measurements["minJointMarginRad"] < 1e-6
    assert len(measurements["jointMarginsRad"]) == 7
    assert measurements["newtonP6Verified"] is False
    assert "Unreachable" not in str(failure.value)


@pytest.mark.parametrize("height", [0, -.1, float("nan"), float("inf"), True, ".1"])
def test_invalid_height_rejects_before_any_teaching(height):
    def solve(*args, **kwargs):
        pytest.fail("Invalid approach height reached the solver")

    selection = SimpleNamespace(teacher=SimpleNamespace(solve=solve))
    with pytest.raises(ValueError, match="approach height"):
        teach.teach_selected(selection, approach_height=height)


def test_cli_failed_regeneration_preserves_previous_output(tmp_path, monkeypatch):
    output = tmp_path / "locations.yaml"
    output.write_text("previous good teaching\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["teach", "--scene-version", "test@1",
                                     "--approach-height-m", ".1", "--output", str(output)])
    monkeypatch.setattr(teach, "select_scene", lambda *args: object())

    def reject(selection, *, approach_height):
        assert approach_height == .1
        raise ValueError("Later endpoint failed")

    monkeypatch.setattr(teach, "teach_selected", reject)
    with pytest.raises(ValueError, match="Later endpoint failed"):
        teach.main()
    assert output.read_text() == "previous good teaching\n"


def test_capture_rejects_unused_approach_option(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["teach", "--capture", "rack.B3", "--approach-height-m", ".1"])
    with pytest.raises(SystemExit) as failure:
        teach.main()
    assert failure.value.code == 2


@pytest.mark.parametrize("joints", [[0.] * 7, B3_GRASP[:6], [float("nan")] * 7])
def test_invalid_initial_joints_reject_before_optional_solver_import(joints):
    with pytest.raises(ValueError, match="Initial joints"):
        teach.Teacher().solve(np.zeros(3), joints)

"""Independent numerical oracles; these do not claim real trainer-state consistency."""

import math
from copy import deepcopy

import numpy as np
import pytest
from pydantic import ValidationError

from packages.policies.consistency import compare_observations
from packages.policies.inference import CpuPolicy
from packages.policies.observations import (
    ObservationSpec,
    assemble,
    command_envelope_failure,
    coverage_failure,
    joint_targets,
    sim_gap,
)
from packages.training.schema import TaskMetadata
from tests.fixtures.policy import model_bytes, observation_spec
from tests.fixtures.training_task import task_document


def test_order_units_offset_scale_z_up_and_xyzw_against_independent_golden():
    value = observation_spec()
    value["observations"] = [
        {
            "name": "relative",
            "source": "body_position",
            "key": "$object",
            "frame": "/base",
            "unit": "m",
            "shape": [3],
            "offset": [0.0, 1.0, 0.5],
            "scale": [2.0, 3.0, 4.0],
        },
        {
            "name": "qd",
            "source": "joint_velocity",
            "keys": ["/b", "/a"],
            "frame": "world",
            "unit": "rad/s",
            "shape": [2],
            "offset": [1.0, -1.0],
            "scale": [0.5, 2.0],
        },
        {
            "name": "orientation",
            "source": "site_orientation",
            "key": "/site",
            "frame": "world",
            "unit": "xyzw",
            "shape": [4],
            "offset": [0.0] * 4,
            "scale": [1.0] * 4,
        },
        {
            "name": "last",
            "source": "previous_action",
            "frame": "world",
            "unit": "1",
            "shape": [1],
            "offset": [0.0],
            "scale": [1.0],
        },
    ]
    spec = ObservationSpec.model_validate(value)
    state = {
        "bodies": {
            "/base": {"p": [1.0, 2.0, 3.0], "q": [0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)]},
            "/tube": {"p": [1.0, 4.0, 4.0], "q": [0.0, 0.0, 0.0, 1.0]},
        },
        "joints": {"/a": {"q": 0.1, "qd": 4.0}, "/b": {"q": 0.2, "qd": -3.0}},
        "sites": {"/site": {"p": [9.0, 8.0, 7.0], "q": [0.0, 0.0, 0.0, 1.0]}},
    }
    # World displacement (0,2,1), inverse +90Z rotation => (2,0,1).
    actual = assemble(spec, state, previous_action=[9.0], references={"$object": "/tube"})
    np.testing.assert_allclose(actual, [4.0, -3.0, 2.0, -2.0, 10.0, 0.0, 0.0, 0.0, 1.0, 9.0], atol=1e-6)
    assert actual.dtype == np.float32


@pytest.mark.parametrize(
    "change",
    [
        {"shape": [2]},
        {"frame": ""},
        {"unit": "m/s"},
        {"offset": [float("nan")]},
        {"scale": [0.0]},
        {"source": "unsupported"},
        {"shape": [1, 1]},
        {"frame": "/rotated-joint"},
    ],
)
def test_spec_rejects_unknown_ambiguous_or_nonfinite_terms(change):
    value = observation_spec()
    value["observations"][0].update(change)
    with pytest.raises(ValidationError):
        ObservationSpec.model_validate(value)


def test_real_cpu_onnx_inference_then_independent_action_limits(tmp_path):
    path = tmp_path / "fixture.onnx"
    path.write_bytes(model_bytes())
    spec = ObservationSpec.model_validate(observation_spec())
    model = CpuPolicy(path, spec)
    assert model.session.get_providers() == ["CPUExecutionProvider"]
    assert model.infer([3.0]).tolist() == [3.0]
    assert model.inference_calls == model.valid_outputs == 1
    # raw3 * scale.1 clamps to .2 then position .95 clamps at 1.
    assert joint_targets(spec, [0.95], model.infer([3.0])).tolist() == [1.0]
    assert joint_targets(spec, [0.0], [-100.0]).tolist() == [-0.2]
    with pytest.raises(ValueError):
        model.infer([float("nan")])
    assert model.inference_calls == model.valid_outputs == 2  # Invalid input never enters the native runtime.
    with pytest.raises(ValueError):
        joint_targets(spec, [0.0], [float("inf")])
    wrong = deepcopy(spec)
    wrong.observations[0].shape = [2]
    with pytest.raises(ValueError):
        CpuPolicy(path, wrong)


def test_onnx_external_references_are_confined_before_runtime_load(tmp_path):
    path = tmp_path / "fixture.onnx"
    path.write_bytes(model_bytes(external_path="../outside"))
    with pytest.raises(ValueError):
        CpuPolicy(path, ObservationSpec.model_validate(observation_spec()))


def test_coverage_cannot_be_widened_by_evaluation_grid_or_extra_parameters():
    task = task_document()
    task["evaluation"]["params"]["from"].append("rack.A2")
    assert coverage_failure(task, "pickPlace", {"from": "rack.A1", "to": "centrifuge.slot1"}) is None
    for action, params in [
        ("pickPlace", {"from": "rack.A2", "to": "centrifuge.slot1"}),
        ("pick", {"location": "rack.A1"}),
        ("pickPlace", task["episode"]["params"] | {"speed": 0.5}),
    ]:
        assert coverage_failure(task, action, params)["code"] == "precondition_failed"


@pytest.mark.parametrize(
    "train,platform,expected",
    [(0.9, 0.75, False), (0.9, 0.7499, True), (0.9, 0.8, False), (None, 0.8, None), (0.7, 0.9, False)],
)
def test_sim_gap_is_strictly_more_than_15_percentage_points(train, platform, expected):
    assert sim_gap(train, platform) is expected


def test_sim_gap_rejects_percent_or_nonfinite_input():
    for value in (90, float("nan"), float("inf"), -1, True):
        with pytest.raises(ValueError):
            sim_gap(value, 0.7)


def test_consistency_report_requires_256_independent_inputs_and_checks_maximum():
    spec = ObservationSpec.model_validate(observation_spec())
    samples = [
        {
            "state": {"joints": {"/World/Lab/RobotA/joint1": {"q": 0.25, "qd": 0.0}}},
            "trainingObservation": [0.5],
            "previousAction": [0.0],
        }
        for _ in range(256)
    ]
    assert compare_observations(spec, samples)["numericalPass"]
    assert not compare_observations(spec, samples)["qualified"]
    assert not compare_observations(spec, samples[:255])["numericalPass"]
    samples[-1]["trainingObservation"] = [0.5002]
    result = compare_observations(spec, samples)
    assert not result["numericalPass"] and result["maxAbsoluteError"] == pytest.approx(0.0002)


def test_embodied_angular_velocity_projected_gravity_command_and_offset_action_space():
    half_sqrt2 = math.sqrt(0.5)
    raw_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "ang_vel_body",
                "source": "body_angular_velocity",
                "key": "/World/Lab/Bot/Base",
                "frame": "body",
                "unit": "rad/s",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "ang_vel_world",
                "source": "body_angular_velocity",
                "key": "/World/Lab/Bot/Base",
                "frame": "world",
                "unit": "rad/s",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [0.5, 0.5, 0.5],
            },
            {
                "name": "gravity",
                "source": "projected_gravity",
                "key": "/World/Lab/Bot/Base",
                "unit": "1",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "cmd",
                "source": "command",
                "frame": "body",
                "unit": "m/s",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [2.0, 2.0, 1.0],
                "lower": [-0.3, -0.3, -1.0],
                "upper": [0.3, 0.3, 1.0],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": ["/World/Lab/Bot/hip_l", "/World/Lab/Bot/hip_r"],
            "units": ["rad", "rad"],
            "defaultPosition": [0.2, -0.2],
            "scale": [0.25, 0.25],
            "lower": [-0.8, -0.8],
            "upper": [0.8, 0.8],
        },
    }
    spec = ObservationSpec.model_validate(raw_spec)
    assert spec.size == 12

    # +90 deg pitch around Y: q = (0, sin(45), 0, cos(45)).
    # Local +X points to world -Z, local +Z points to world +X.
    # Therefore world gravity (0, 0, -1) in body frame is (+1, 0, 0)!
    # World angular velocity (1, 2, 3) rotated by -90 deg about Y is (-3, 2, 1)!
    state = {
        "bodies": {
            "/World/Lab/Bot/Base": {
                "p": [0.0, 0.0, 0.3],
                "q": [0.0, half_sqrt2, 0.0, half_sqrt2],
                "w": [1.0, 2.0, 3.0],
            }
        },
        "joints": {},
        "sites": {},
    }
    assembled = assemble(spec, state, previous_action=[0.0, 0.0], command=[0.15, -0.05, 0.4])
    expected = np.array(
        [
            -3.0, 2.0, 1.0,
            0.5, 1.0, 1.5,
            1.0, 0.0, 0.0,
            0.3, -0.1, 0.4,
        ],
        dtype=np.float32,
    )
    np.testing.assert_allclose(assembled, expected, atol=1e-6)

    # Missing, malformed, or out-of-bounds command must fail assembly
    with pytest.raises(ValueError, match="command"):
        spec.assemble(state, previous_action=[0.0, 0.0])
    with pytest.raises(ValueError):
        spec.assemble(state, previous_action=[0.0, 0.0], command=[0.1, float("nan"), 0.0])
    with pytest.raises(ValueError, match="bounds"):
        spec.assemble(state, previous_action=[0.0, 0.0], command=[0.45, 0.0, 0.0])

    # joint_position_offset targets = clip(defaultPosition + raw * scale, lower, upper)
    targets = joint_targets(spec, [0.7, -0.7], [1.0, -4.0])
    np.testing.assert_allclose(targets, [0.45, -0.8], atol=1e-7)

    # Optional maxDelta on joint_position_offset also bounds per-step excursion from current positions
    bounded_raw = deepcopy(raw_spec)
    bounded_raw["action"]["maxDelta"] = [0.1, 0.1]
    bounded_spec = ObservationSpec.model_validate(bounded_raw)
    np.testing.assert_allclose(joint_targets(bounded_spec, [0.0, 0.0], [1.0, -4.0]), [0.1, -0.1], atol=1e-7)

    # Consistency report passes command through when present
    samples = [
        {
            "state": state,
            "trainingObservation": expected.tolist(),
            "previousAction": [0.0, 0.0],
            "command": [0.15, -0.05, 0.4],
        }
        for _ in range(256)
    ]
    assert compare_observations(spec, samples)["numericalPass"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda d: d["action"].pop("defaultPosition"),
        lambda d: d["action"].update({"defaultPosition": [0.95, 0.0]}),
        lambda d: d["observations"][0].update({"unit": "m/s"}),
        lambda d: d["observations"][0].update({"lower": [-1.0, -1.0, -1.0], "upper": [1.0, 1.0, 1.0]}),
        lambda d: d["observations"][2].update({"unit": "m"}),
        lambda d: d["observations"][2].update({"frame": "world"}),
        lambda d: d["observations"][2].update({"frame": "body"}),
        lambda d: d["observations"][2].update({"frame": "/World/Lab/OtherBody"}),
        lambda d: d["observations"][3].update({"key": "/World/Lab/Bot/Base"}),
        lambda d: d["observations"][3].update({"frame": "/World/Lab/Bot/Base"}),
        lambda d: d["observations"][3].pop("lower"),
        lambda d: d["observations"][3].update({"lower": [0.5, 0.0], "upper": [-0.5, 0.0]}),
    ],
)
def test_embodied_observation_and_offset_action_validation_rejects_invalid_contracts(mutator):
    raw_spec = {
        "schema": "lw.obs-spec/1",
        "observations": [
            {
                "name": "ang_vel",
                "source": "body_angular_velocity",
                "key": "/World/Lab/Bot/Base",
                "frame": "body",
                "unit": "rad/s",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "joint_q",
                "source": "joint_position",
                "keys": ["/World/Lab/Bot/hip_l", "/World/Lab/Bot/hip_r"],
                "frame": "world",
                "unit": "rad",
                "shape": [2],
                "offset": [0.0, 0.0],
                "scale": [1.0, 1.0],
            },
            {
                "name": "gravity",
                "source": "projected_gravity",
                "key": "/World/Lab/Bot/Base",
                "frame": "/World/Lab/Bot/Base",
                "unit": "1",
                "shape": [3],
                "offset": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            {
                "name": "cmd",
                "source": "command",
                "frame": "body",
                "unit": "1",
                "shape": [2],
                "offset": [0.0, 0.0],
                "scale": [1.0, 1.0],
                "lower": [-1.0, -1.0],
                "upper": [1.0, 1.0],
            },
        ],
        "action": {
            "space": "joint_position_offset",
            "jointKeys": ["/World/Lab/Bot/hip_l", "/World/Lab/Bot/hip_r"],
            "units": ["rad", "rad"],
            "defaultPosition": [0.0, 0.0],
            "scale": [0.2, 0.2],
            "lower": [-0.5, -0.5],
            "upper": [0.5, 0.5],
        },
    }
    mutator(raw_spec)
    with pytest.raises(ValidationError):
        ObservationSpec.model_validate(raw_spec)


def test_delta_action_still_requires_max_delta_and_forbids_default_position():
    base = observation_spec()
    missing_delta = deepcopy(base)
    missing_delta["action"].pop("maxDelta")
    with pytest.raises(ValidationError):
        ObservationSpec.model_validate(missing_delta)

    with_default = deepcopy(base)
    with_default["action"]["defaultPosition"] = [0.0]
    with pytest.raises(ValidationError):
        ObservationSpec.model_validate(with_default)


def test_continuous_param_bounds_and_command_envelope_coverage():
    raw_task = task_document()
    raw_task["implements"] = "balancer/v1.moveTo"
    raw_task["episode"] = {
        "params": {"speed": 0.12},
        "paramBounds": {"x": [-1.5, 1.5], "y": [-1.0, 1.0], "speed": [0.05, 0.25]},
        "commandEnvelope": [[-0.25, 0.25], [-0.15, 0.15], [-0.8, 0.8]],
    }
    parsed = TaskMetadata.model_validate(raw_task).model_dump(mode="json")

    assert coverage_failure(parsed, "moveTo", {"x": 0.5, "y": -0.4}) is None
    assert coverage_failure(parsed, "moveTo", {"x": -1.5, "y": 1.0, "speed": 0.2}) is None
    assert coverage_failure(parsed, "moveTo", {"x": 2.0, "y": 0.0})["code"] == "precondition_failed"
    assert coverage_failure(parsed, "moveTo", {"x": 0.5})["code"] == "precondition_failed"
    assert coverage_failure(parsed, "moveTo", {"x": True, "y": 0.0})["code"] == "precondition_failed"
    extra_params = {"x": 0.5, "y": 0.0, "extra": 1}
    assert coverage_failure(parsed, "moveTo", extra_params)["code"] == "precondition_failed"

    assert command_envelope_failure(parsed, [0.2, -0.1, 0.5]) is None
    assert command_envelope_failure(parsed, [0.3, 0.0, 0.0])["code"] == "precondition_failed"
    assert command_envelope_failure(parsed, [0.1, 0.0])["code"] == "precondition_failed"


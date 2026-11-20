import math

import numpy as np
import pytest

from packages.policies.observations import ObservationSpec


def test_versioned_motion_observations_use_simulation_time_and_shortest_arc():
    spec = {
        "schema": "lw.obs-spec/2",
        "observations": [
            {
                "name": "phase",
                "source": "motion_phase",
                "frame": "world",
                "unit": "1",
                "shape": [2],
                "offset": [0, 0],
                "scale": [1, 1],
            },
            {
                "name": "reference",
                "source": "reference_motion_frame",
                "frame": "world",
                "unit": "1",
                "shape": [8],
                "offset": [0] * 8,
                "scale": [1] * 8,
            },
        ],
        "action": {
            "space": "joint_position_delta",
            "jointKeys": ["/World/Lab/Bot/Hinge"],
            "units": ["rad"],
            "scale": [1],
            "lower": [-1],
            "upper": [1],
            "maxDelta": [0.1],
        },
    }
    model = ObservationSpec.model_validate(spec)
    motion = {
        "loop": True,
        "frames": [
            {"time": 0, "position": [0, 0, 0], "orientation": [0, 0, 0, 1], "joints": [-0.2]},
            {
                "time": 1,
                "position": [1, 0, 0],
                "orientation": [0, 0, -math.sqrt(3) / 2, -0.5],
                "joints": [0.6],
            },
        ],
    }
    for elapsed, expected in [(0, [0, 1]), (0.25, [1, 0]), (0.5, [0, -1]), (1, [0, 1])]:
        values = model.assemble({"motionElapsed": elapsed}, previous_action=[0], motion=motion)
        np.testing.assert_allclose(values[:2], expected, atol=1e-6)
        if elapsed == 0.25:
            np.testing.assert_allclose(
                values[2:], [0.25, 0, 0, 0, 0, 0.2588190451, 0.9659258263, 0], atol=1e-6
            )
    with pytest.raises(ValueError):
        ObservationSpec.model_validate(spec | {"schema": "lw.obs-spec/1"})


def test_one_shot_holds_final_frame_and_calibration_round_trips():
    from packages.policies.calibration import JointCalibration
    from packages.policies.motion import motion_observations

    frames = [
        {"time": 0, "position": [0, 0, 0], "orientation": [0, 0, 0, 1], "joints": [-0.2]},
        {"time": 1, "position": [1, 0, 0], "orientation": [0, 0, 1, 0], "joints": [0.6]},
    ]
    assert motion_observations({"loop": False, "frames": frames}, 0.25)["motion_phase"] == [0.75]
    last = motion_observations({"loop": False, "frames": frames}, 5)
    assert last["motion_phase"] == [0]
    assert last["reference_motion_frame"] == pytest.approx([1, 0, 0, 0, 0, 1, 0, 0.6])
    calibrated = JointCalibration.model_validate(
        {
            "schema": "lw.joint-calibration/1",
            "baseTransform": [1, 0, 0, 0.2, 0, 1, 0, 0, 0, 0, 1, 0.1, 0, 0, 0, 1],
            "joints": [
                {
                    "jointKey": "/Asset/pitch",
                    "sourceName": "motor_pitch",
                    "sign": -1,
                    "zeroOffset": 0.25,
                    "unit": "rad",
                }
            ],
        }
    )
    (joint,) = calibrated.joints
    assert (0.40 - joint.zeroOffset) * joint.sign == pytest.approx(-0.15)


def test_reference_tracking_reuses_phase_clock_and_shortest_arc_for_pose_and_velocity():
    from packages.policies.motion import motion_observations, reference_tracking

    key = "/Asset/tip"
    start = {"time": 0., "position": [0., 0., .2], "orientation": [0., 0., 0., 1.],
             "joints": [0.], "bodyLocal": {key: [1., 0., 0.]}}
    end = {"time": 1., "position": [0., 0., .1], "orientation": [0., 0., -math.sqrt(.5), -math.sqrt(.5)],
           "joints": [.2], "bodyLocal": {key: [2., 0., 0.]}}
    motion = {"loop": False, "frames": [start, end, end | {"time": 1.4}]}
    values = reference_tracking(motion, .5)
    np.testing.assert_allclose(values["rootQuaternion"], [0, 0, math.sin(math.pi/8), math.cos(math.pi/8)])
    assert values["rootQuaternion"] == motion_observations(motion, .5)["reference_motion_frame"][3:7]
    assert values["rootPosition"] == pytest.approx([0., 0., .15])
    assert values["rootLinearVelocity"] == pytest.approx([0., 0., -.1])
    assert values["rootAngularVelocity"] == pytest.approx([0., 0., math.pi / 2])
    assert values["bodyLocal"][key] == pytest.approx([1.5, 0., 0.])
    for elapsed in (1., 1.2, 1.4, 100.):
        held = reference_tracking(motion, elapsed)
        assert held["rootLinearVelocity"] == held["rootAngularVelocity"] == [0., 0., 0.]
        assert held["bodyLocal"][key] == [2., 0., 0.]

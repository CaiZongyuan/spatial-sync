"""Real SDK joint limits retain USD units and unlimited default semantics."""

import math

import pytest
from pxr import Usd, UsdGeom, UsdPhysics

from packages.library.structural import validate


def joint_report(tmp_path, joint_type, limits, coordinates):
    path = tmp_path / "stage.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/World/Lab/Robot")
    joint = joint_type.Define(stage, "/World/Lab/Robot/Joint")
    if limits is not None:
        for attribute, value in zip((joint.CreateLowerLimitAttr(), joint.CreateUpperLimitAttr()), limits):
            attribute.Block() if value is None else attribute.Set(value)
    stage.GetRootLayer().Save()
    return validate(
        {
            "kind": "scene",
            "subject": "scene:posture-fixture@1",
            "metadata": {"stage": path.name},
            "package": {"path": str(tmp_path), "files": {path.name: str(path)}},
            "assets": [],
            "types": {"robot-arm/v1": {}},
            "programs": {
                "arm.fixture": {
                    "type": "robot-arm/v1",
                    "fields": {"joints": {"spatialReference": "joint", "posture": True}},
                }
            },
            "bindings": {
                "entities": [
                    {
                        "key": "/World/Lab/Robot",
                        "type": "robot-arm/v1",
                        "program": "arm.fixture",
                        "config": {"joints": ["/World/Lab/Robot/Joint"]},
                    }
                ]
            },
            "locations": {"locations": {"target": {"grasp": coordinates}}},
        }
    )


@pytest.mark.parametrize("limits", [(None, 90), (float("nan"), 90), (10, -10)])
def test_blocked_nan_and_reversed_sdk_joint_limits_fail_without_crashing(tmp_path, limits):
    report = joint_report(tmp_path, UsdPhysics.RevoluteJoint, limits, [0])
    failure = next(check for check in report["checks"] if check["id"] == "S8")
    assert failure["status"] == "fail" and "invalid or blocked joint limits" in failure["message"]


@pytest.mark.parametrize(
    "joint_type,limits,coordinates",
    [
        (UsdPhysics.PrismaticJoint, (-0.25, 0.25), [-0.25]),
        (UsdPhysics.PrismaticJoint, (-0.25, 0.25), [0.25]),
        (UsdPhysics.RevoluteJoint, (-90, 90), [-math.pi / 2]),
        (UsdPhysics.RevoluteJoint, (-90, 90), [math.pi / 2]),
        (UsdPhysics.RevoluteJoint, None, [1000]),
        (UsdPhysics.RevoluteJoint, (-math.inf, math.inf), [1000]),
    ],
)
def test_sdk_scalar_joint_boundary_and_unlimited_defaults_pass(tmp_path, joint_type, limits, coordinates):
    report = joint_report(tmp_path, joint_type, limits, coordinates)
    assert next(check for check in report["checks"] if check["id"] == "S8")["status"] == "pass"


@pytest.mark.parametrize(
    "joint_type,limits,value",
    [
        (UsdPhysics.PrismaticJoint, (-0.25, 0.25), 0.251),
        (UsdPhysics.RevoluteJoint, (-90, 90), math.pi / 2 + 0.001),
        (UsdPhysics.RevoluteJoint, (math.inf, math.inf), 0),
    ],
)
def test_sdk_outside_scalar_joint_limit_fails(tmp_path, joint_type, limits, value):
    report = joint_report(tmp_path, joint_type, limits, [value])
    failure = next(check for check in report["checks"] if check["id"] == "S8")
    assert failure["status"] == "fail" and "outside its joint limits" in failure["message"]

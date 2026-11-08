"""Readonly indexed metadata needs neither a model construction nor an FK articulation."""

from types import MappingProxyType

import numpy as np
import pytest

pytest.importorskip("warp")

from packages.sim_runtime.physics_port import JointRef, PhysicsPort


def test_old_joint_reference_constructor_and_unknown_metadata_remain_readable():
    reference = JointRef(slice(0, 1), slice(0, 1), slice(0, 1), 7)
    assert reference.child == 7 and reference.kind is None and reference.lower is reference.upper is None


@pytest.mark.parametrize("kind,lower,upper", [("revolute", 0., 1.5), ("prismatic", 0., .19)])
def test_world_root_scalar_joint_metadata_is_immutable_and_uses_actual_buffer_precision(kind, lower, upper):
    port = object.__new__(PhysicsPort)
    reference = JointRef(slice(3, 4), slice(2, 3), slice(1, 2), -1, kind, lower, upper)
    port.joints = {"/World/Lab/Drive": reference}
    port.target_q = np.zeros(2, dtype=np.float32)
    value = port.joint_metadata("/World/Lab/Drive")
    assert isinstance(value, MappingProxyType)
    assert dict(value) == {"kind": kind, "qWidth": 1, "qdWidth": 1, "targetWidth": 1,
                           "lower": lower, "upper": upper, "targetDtype": "float32"}
    assert dict(port.joint_metadata(reference)) == dict(value)
    with pytest.raises(TypeError):
        value["upper"] = 10
    assert not port.target_q.any()

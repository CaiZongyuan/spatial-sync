"""CPU Warp kernel proof: selected bodies, fixed frames and nonzero/yawed clones."""

import math

import numpy as np
import pytest

wp = pytest.importorskip("warp")

from apps.node.view_bridge import gather_kernel


def test_selected_device_gather_restores_canonical_frames_without_copying_all_body_poses():
    wp.init()
    poses = [[100, 100, 100, 0, 0, 0, 1] for _ in range(100)]
    indices, local, inverse, expected = [], [], [], []
    for env, yaw in enumerate((0.1, -0.2, 0.5, 1.0)):
        origin = (5 - env * 2, 2 + env, 0)
        clone = wp.transform(origin, wp.quat_from_axis_angle(wp.vec3(0, 0, 1), yaw))
        inverse.append(wp.transform_inverse(clone))
        for body in range(3):
            index = 2 + env * 19 + body * 3
            canonical_yaw = 0.3 * body
            canonical_position = (body + env * 0.1, body * 0.5, 1)
            local_pose = (0.2, 0.1, 0, 0, 0, 0, 1)
            # Independent analytic z-yaw/translation oracle for retained parent coordinates.
            parent_yaw = yaw + canonical_yaw
            x = origin[0] + math.cos(yaw) * canonical_position[0] - math.sin(yaw) * canonical_position[1]
            y = origin[1] + math.sin(yaw) * canonical_position[0] + math.cos(yaw) * canonical_position[1]
            poses[index] = [
                x - math.cos(parent_yaw) * 0.2 + math.sin(parent_yaw) * 0.1,
                y - math.sin(parent_yaw) * 0.2 - math.cos(parent_yaw) * 0.1, 1,
                0, 0, math.sin(parent_yaw / 2), math.cos(parent_yaw / 2),
            ]
            indices.append(index)
            local.append(local_pose)
            expected.append([
                *canonical_position, 0, 0, math.sin(canonical_yaw / 2), math.cos(canonical_yaw / 2)
            ])
    body_q = wp.array(poses, dtype=wp.transform, device="cpu")
    # Any pre-gather full pose readback is a concrete failure, including CPU test execution.
    body_q.numpy = lambda: pytest.fail("Full producer body pose readback is forbidden")
    output = wp.empty(12, dtype=wp.transform, device="cpu")
    wp.launch(gather_kernel(wp), dim=12, inputs=[
        body_q, wp.array(indices, dtype=wp.int32, device="cpu"),
        wp.array(local, dtype=wp.transform, device="cpu"),
        wp.array(inverse, dtype=wp.transform, device="cpu"), output, 3,
    ], device="cpu")
    assert output.shape == (12,)
    np.testing.assert_allclose(output.numpy(), np.asarray(expected), atol=2e-6)

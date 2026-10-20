"""Optional real CPU FK checks; the GPU-free gate uses actual Virtual programs."""

import copy
import math

import numpy as np
import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from packages.info.preview import PreviewRequest
from packages.protocol.schema import SessionOptions
from packages.sim_runtime.info_host import NewtonInfoHost
from packages.sim_runtime.manifest_export import export_manifest
from packages.sim_runtime.physics_port import PhysicsPort
from packages.sim_runtime.simulation import NewtonSimulation

ROBOT = "/World/Lab/RobotA"


def arrays(owner):
    return {name: value.numpy().copy() for name, value in vars(owner).items()
            if callable(getattr(value, "numpy", None))}


def assert_arrays(owner, expected):
    actual = arrays(owner)
    assert actual.keys() == expected.keys()
    for name in expected:
        np.testing.assert_array_equal(actual[name], expected[name], err_msg=name)


def manifest(sim):
    return export_manifest(sim, session_id="preview-test", scene_revision="rev", revision_numeric_id=1)


def test_real_arm_preview_keeps_both_states_controls_facts_and_motion_history_unchanged():
    with NewtonSimulation("robot-and-tubes", device="cpu") as sim:
        info = NewtonInfoHost(sim, manifest(sim), SessionOptions(device="cpu"), lambda _events: None)
        sim.step(8)
        sim.pause()
        port = info.port
        port.relative_site_velocity(next(iter(port.bodies.values())), tuple(port.sites.values())[:2])
        states = [(owner, arrays(owner)) for owner in (sim.state, sim.next_state, sim.control)]
        values = info.snapshot().model_dump(mode="json")
        previous = port.previous_positions.copy()
        history = copy.deepcopy(port._site_motion_history)
        pending = (port.target_q.copy(), port.target_qd.copy(), port.last_time, port.sample_time_ms)
        time = sim.sim_time
        request = PreviewRequest(entityKey=ROBOT, action="moveTo",
                                 params={"location": "rack.A1", "pose": "grasp"},
                                 planId="test", revision=1, stepId=1)
        preview = info.preview(request)
        assert 1 < len(preview.points) <= 64
        binding = info.catalog.entities[ROBOT]["config"]
        target = binding["locations"]["locations"]["rack.A1"]
        target_pose = port.site_pose(target["site"])[0]
        assert np.linalg.norm(np.array(preview.points[-1]) - target_pose) <= .01
        assert set(preview.targetBodyPoses) <= set(port.body_keys.values())
        assert binding["handBody"] in preview.targetBodyPoses
        # Consumers cannot mutate the stored target through a returned value.
        preview.points[0] = (99, 99, 99)
        assert info.preview(request).points[0] != (99, 99, 99)
        for owner, expected in states:
            assert_arrays(owner, expected)
        assert info.snapshot().model_dump(mode="json") == values
        np.testing.assert_array_equal(port.previous_positions, previous)
        np.testing.assert_array_equal(port.target_q, pending[0])
        np.testing.assert_array_equal(port.target_qd, pending[1])
        assert (port.last_time, port.sample_time_ms) == pending[2:]
        assert sim.sim_time == time
        assert port._site_motion_history.keys() == history.keys()
        for key, (bodies, sites, sampled_at) in history.items():
            after = port._site_motion_history[key]
            np.testing.assert_array_equal(after[0], bodies)
            np.testing.assert_array_equal(after[1], sites)
            assert after[2] == sampled_at
        # A subsequent real solver/control tick invalidates the physical context.
        first = info.preview(request)
        sim.step(8)
        changed = info.preview(request)
        q = sim.state.joint_q.numpy()
        wanted = [float(q[port.joints[key].q][0]) for key in binding["jointKeys"]]
        assert changed.startJointPositions == wanted
        assert changed.startJointPositions != first.startJointPositions
        assert len(info.host._preview_cache) == 2


def test_xpbd_snapshot_reads_current_ik_without_writing_live_coordinates():
    with NewtonSimulation("falling-boxes", device="cpu") as sim:
        port = PhysicsPort(sim, manifest(sim))
        sim.step(24)
        before = [(owner, arrays(owner)) for owner in (sim.state, sim.next_state, sim.control)]
        snapshot = port.preview_snapshot()
        joint = next(key for key in port.joints if key.endswith("Hinge"))
        current = snapshot.positions([joint])
        assert current != pytest.approx([-np.pi / 4], abs=1e-3)
        body = port.bodies["/World/Pendulum/Link"]
        # Independent geometric oracle: anchor=(1,0,1.5), the local COM is
        # (0,0,-.35), and the actual world body has rotated about positive Y.
        position = sim.state.body_q.numpy()[body, :3]
        angle = -math.atan2(float(position[0]) - 1, 1.5 - float(position[2]))
        assert current == pytest.approx([angle], abs=1e-5)
        site = next(key for key, value in port.sites.items() if value.body == body)
        pose = snapshot.forward([joint], current, site)
        # The actual hinge anchor stays fixed while the pendulum swings.
        assert pose["p"] == pytest.approx([1, 0, 1.5], abs=1e-5)
        for owner, expected in before:
            assert_arrays(owner, expected)

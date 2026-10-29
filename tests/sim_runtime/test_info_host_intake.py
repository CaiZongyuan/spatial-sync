"""Paused intake must commit fresh measured holds before the next solver step."""

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("warp")
pytestmark = pytest.mark.newton

from packages.protocol.schema import InvokeRequest
from packages.sim_runtime.info_host import NewtonInfoHost
from packages.sim_runtime.physics_port import PhysicsPort, SiteRef
from tests.fixtures.cell_passage import StrokePort, build_workcell_host
from tests.unit.test_cell_passage import invoke


class BufferedPort(StrokePort):
    def __init__(self):
        super().__init__()
        self.actual_q, self.actual_qd = self.q.copy(), self.velocities.copy()
        self.native_targets = self.targets.copy()

    def capture(self):
        self.q.update(self.actual_q)
        self.velocities.update(self.actual_qd)

    def commit(self):
        self.native_targets.update(self.targets)


@pytest.mark.parametrize("callback", ["invoke", "cancel", "revoke_before_start"])
def test_paused_intake_commits_actual_hold_before_first_resumed_physics(tmp_path, callback):
    host, _ = build_workcell_host(tmp_path)
    port = BufferedPort()
    host.physics = port
    info = object.__new__(NewtonInfoHost)
    info.host, info.port, info.emit = host, port, lambda events: None
    # A known old drive target persists in the solver independently of the
    # public buffered port. Physics has reached a newer unobserved frame.
    port.targets["x"] = port.native_targets["x"] = .2
    if callback == "cancel":
        invoke(host, "move", "moving", position=[.2, .1, .05])
        host.tick(.1, .1)
    port.actual_q["x"], port.actual_qd["x"] = .075, .3
    request = InvokeRequest(invocationId="handoff", requestId="handoff", entityKey="/workcell",
                            action="requestControl", epoch=0, createdSimTime=host.sim_time, createdAtMs=0,
                            params={"owner": "auto", "epoch": 0, "operationId": "handoff",
                                    "nextOwner": "human", "mode": "manual"})
    if callback == "invoke":
        info.invoke(request)
    elif callback == "cancel":
        info.cancel("moving")
    else:
        # Revoke queued work must also drain a pending control-accept hold.
        port.capture()
        host.invoke(request)
        info.revoke_before_start("handoff")
    first_resumed_physics_target = port.native_targets["x"]
    assert first_resumed_physics_target == .075
    assert host.values()["/workcell"]["controlSafe"] is False
    assert port.q["x"] == .075 and port.velocities["x"] == .3


def test_same_simtime_capture_retains_measured_body_and_relative_site_velocity():
    class Array:
        def __init__(self, values):
            self.values = np.array(values, dtype=float)

        def numpy(self):
            return self.values

    port = object.__new__(PhysicsPort)
    port.sim = SimpleNamespace(sim_time=1., model=SimpleNamespace(device=SimpleNamespace(is_cuda=False)),
                               state=SimpleNamespace(body_q=Array([[.2, 0, 0, 0, 0, 0, 1]]),
                                                     joint_q=Array([.075]), joint_qd=Array([.3])))
    port._staging = None
    port.last_time = 0.
    port.previous_positions = np.zeros((1, 3))
    port._site_motion_history = {}
    site = SiteRef(-1, {"p": [0, 0, 0], "q": [0, 0, 0, 1]})
    sites = (site,)
    port.capture()
    before = port.relative_site_velocity(0, sites).copy()
    port.capture()  # Fresh RPC boundary, no actual physics/time advance.
    np.testing.assert_array_equal(port.velocity, [[.2, 0, 0]])
    np.testing.assert_array_equal(port.relative_site_velocity(0, sites), before)
    np.testing.assert_array_equal(port.q, [.075])
    np.testing.assert_array_equal(port.qd, [.3])

"""Bounded teaching motion through HostCore and the external actuator seam."""

import numpy as np
import pytest

from tests.fixtures.cell_passage import build_workcell_host
from tests.unit.test_cell_passage import execute, invoke


def test_move_limits_target_speed_and_acceleration_but_finishes_only_from_feedback(tmp_path):
    host, port = build_workcell_host(tmp_path)
    port.q.update(x=0.01, y=0.02, z=0.03)
    goal = [0.21, -0.08, 0.08]
    assert invoke(host, "move", "bounded", position=goal).status == "accepted"
    host.tick(0.1, 0.1)
    targets = [[port.targets[key] for key in ("x", "y", "z")]]
    assert targets[0] == [0.01, 0.02, 0.03]
    for index in range(1, 401):
        host.tick(0.1 + index * 0.01, 0.01)
        targets.append([port.targets[key] for key in ("x", "y", "z")])
    speeds = np.diff(targets, axis=0) / 0.01
    accelerations = np.diff(speeds, axis=0) / 0.01
    assert np.all(np.max(np.abs(speeds), axis=0) <= [0.2, 0.2, 0.05])
    assert np.max(np.abs(accelerations)) <= 0.25
    assert targets[-1] == goal
    # Elapsed profile time and written endpoint are not actual arrival.
    assert host.active_invocations("/workcell")
    port.q.update(zip(("x", "y", "z"), goal, strict=True))
    port.velocities["x"] = 0.001
    host.tick(4.2, 0.1)
    assert host.active_invocations("/workcell")
    port.velocities["x"] = 0
    host.tick(4.3, 0.1)
    terminal = next(row for row in host.snapshot().invocations if row.invocationId == "bounded")
    assert terminal.status == "succeeded"


@pytest.mark.parametrize("stop", ["cancel", "handoff"])
def test_move_pause_and_invalidation_preserve_weld_and_liquid_without_resuming_endpoint(tmp_path, stop):
    host, port = build_workcell_host(tmp_path)
    # Establish real domain inventory and an active carried tip before moving.
    execute(host, port, "reserve", "reserve", resource="/source", device="/workcell")
    execute(host, port, "pickupTip", "pick", tip="T1")
    execute(host, port, "aspirate", "asp", vessel="source", volumeUl=40)
    start = host.sim_time
    assert invoke(host, "move", "moving", position=[0.2, 0.1, 0.05]).status == "accepted"
    host.tick(start + 0.1, 0.1)
    host.tick(start + 0.5, 0.4)
    progressing = port.targets.copy()
    assert 0 < progressing["x"] < 0.2
    for _ in range(3):
        # Even a caller reporting wall dt cannot advance a frozen sim clock.
        host.tick(start + 0.5, 0.1)
        assert port.targets == progressing
    port.q["x"] = 0.012  # External actual feedback need not match the profile.
    port.velocities["x"] = 0.02
    if stop == "cancel":
        host.cancel("moving")
    else:
        response = invoke(host, "requestControl", "handoff", nextOwner="human", mode="manual")
        assert response.status == "accepted"
        assert invoke(host, "move", "busy-old", position=[1, 1, 1]).status == "rejected"
    assert port.targets["x"] == 0.012
    assert host.values()["/workcell"]["controlSafe"] is False
    assert port.welds == {("/pipette", "/tip1")}
    for time in (start + 1, start + 4, start + 10):
        host.tick(time, time - host.sim_time)
        assert port.targets["x"] == 0.012
        assert host.active_invocations("/workcell")
    port.velocities["x"] = 0
    host.tick(start + 10.1, 0.1)
    state = host.values()["/workcell"]
    assert state["controlSafe"] is True
    assert state["materialSnapshot"]["tip"]["volumeUl"] == pytest.approx(40)
    assert port.welds == {("/pipette", "/tip1")}
    assert next(x for x in host.snapshot().invocations if x.invocationId == "moving").status == "cancelled"
    if stop == "handoff":
        assert invoke(host, "move", "late", position=[1, 1, 1]).error.code == "stale_control"

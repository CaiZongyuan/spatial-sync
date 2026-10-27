"""A slow catch-up batch must yield to queued control before intake times out."""

import time

import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from packages.protocol.schema import SessionOptions
from packages.sim_runtime.runner import _Runner
from packages.transport import create_transport


def test_slow_catchup_yields_to_control_within_action_intake_deadline(monkeypatch):
    parent, child = create_transport()
    runner = None
    try:
        runner = _Runner(child, "control-yield", "falling-boxes", SessionOptions(realtimeFactor=None))
        step = runner.sim.step
        sent_at = None

        def slow_step(count):
            nonlocal sent_at
            for _ in range(count):
                if sent_at is None:
                    sent_at = time.monotonic()
                    parent.control.send({"kind": "close", "token": "yield-control"})
                # Deterministic CPU-pressure fixture; no timeout is extended.
                time.sleep(.02)
                step(1)

        monkeypatch.setattr(runner.sim, "step", slow_step)
        runner.run()
        assert sent_at is not None
        assert time.monotonic()-sent_at < .5, "Catch-up starved the control pipe beyond action intake"
        assert runner.sim.sim_time > 0
    finally:
        if runner is not None:
            runner.close()
        child.close()
        parent.close()

"""Only an explicit owned CPU MuJoCo opt-in enables native sleeping."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from packages.protocol.schema import SessionOptions
from packages.sim_runtime.runner import _Runner
from packages.transport import create_transport


@pytest.mark.parametrize("enabled", [False, True])
def test_owned_cpu_sleep_entry_defaults_off_and_records_actual_opt_in(tmp_path, monkeypatch, enabled):
    import mujoco

    if enabled:
        monkeypatch.setenv("SPATIAL_NEWTON_CPU_SLEEPING", "1")
    else:
        monkeypatch.delenv("SPATIAL_NEWTON_CPU_SLEEPING", raising=False)
    monkeypatch.setenv("SPATIAL_PASSAGE_RUN_ROOT", str(tmp_path))
    package = SimpleNamespace(metadata={"stage": "python:falling-boxes", "title": "Tiny CPU sleep entry",
                                        "simulation": {"solver": "mujoco", "warmupSteps": 1}},
                              bindings={"entities": []})
    parent, child = create_transport()
    runner = None
    try:
        runner = _Runner(child, "sleep-entry", "falling-boxes", SessionOptions(device="cpu"), package)
        assert runner.sim.native_cpu_sleeping is enabled
        assert bool(runner.sim.solver.mj_model.opt.enableflags & mujoco.mjtEnableBit.mjENBL_SLEEP) is enabled
        assert runner.sim.solver.enable_sleeping is False  # Do not bypass the SDK's GPU-only wrapper.
        receipts = list((tmp_path / "runtime/native-options").glob("cpu-sleeping-*.json"))
        if enabled:
            [receipt] = receipts
            value = json.loads(receipt.read_text())
            assert value["cpuSleepingRequested"] is True
            assert value["nativeCpuSleepingEnabled"] is True
            assert value["solver"] == "mujoco" and value["device"] == "cpu"
        else:
            assert receipts == []
    finally:
        if runner is not None:
            runner.close()
        parent.close()
        child.close()

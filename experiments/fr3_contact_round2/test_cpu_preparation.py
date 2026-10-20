"""CPU counterexamples for the future recorder and its frozen command contract."""

import importlib.util
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import torch

ROOT = Path("D:/Projects/Robots/spatial-sync")
PREP = ROOT / ".scratch/rsi-platform/issue-29-round2/first-home-prep-v1"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecorderTests(unittest.TestCase):
    def test_first_done_keeps_snapshot_and_does_not_mutate_physics(self):
        fake = types.ModuleType("frozen_home_trace")
        fake.tensor = lambda value: value
        fake.BODY_ROLES = ("root", "link7", "leftfinger", "rightfinger", "vial", "rack")
        fake.MANAGER_FIELDS = ("fixed_force_norm_raw", "moving_force_norm_raw")
        fake.install = lambda: []

        class Trace:
            def __init__(self, model, observer):
                self.device, self.bytes = "cpu", 0
                self.first = torch.zeros((1024, 10), dtype=torch.int32)
                self.bodies = torch.arange(6).expand(1024, 6)
                self.mean = torch.zeros((1024, 8))
                self.original_bilateral = torch.zeros(1024)
                self.manager = torch.zeros((1024, 2))
                self.mapping = {
                    name: torch.arange(9).expand(1024, 9).numpy()
                    for name in ("q_ids", "v_ids", "target_q_ids")
                }

            def manager_capture(self, env, manager, done):
                self.first[done, 0] = 1

        fake.Trace = Trace
        physics = types.ModuleType("isaaclab_newton.physics")
        q = torch.zeros((6, 7))
        q[:, 6] = 1
        state = types.SimpleNamespace(body_q=q, joint_q=torch.arange(9).float(), joint_qd=torch.zeros(9))
        physics.NewtonManager = types.SimpleNamespace(
            _state_0=state, _control=types.SimpleNamespace(joint_target_q=torch.zeros(9))
        )
        wrapper = load("round2_home_test", Path(__file__).with_name("round2_home.py"))
        with patch.dict("sys.modules", {"frozen_home_trace": fake, "isaaclab_newton.physics": physics}):
            wrapper.install()
            recorder = fake.Trace(None, None)
            recorder.mean[:, -1] = -0.2
            done = torch.zeros(1024, dtype=torch.bool)
            done[51] = True
            before = {name: value.clone() for name, value in vars(state).items()}
            recorder.manager_capture(None, None, done)
            snapshot = recorder.round2["first_terminal_q_qd_target"][51].clone()
            state.joint_q += 1  # A real reset would replace these positions.
            recorder.manager_capture(None, None, torch.zeros_like(done))
            torch.testing.assert_close(recorder.round2["first_terminal_q_qd_target"][51], snapshot)
            self.assertEqual(int(recorder.round2["controls"][51]), 1)
            self.assertEqual(int(recorder.round2["controls"][0]), 2)
            self.assertEqual(int(recorder.round2["close"][0]), 2)
            for name in ("body_q", "joint_qd"):
                torch.testing.assert_close(getattr(state, name), before[name])
            torch.testing.assert_close(state.joint_q, before["joint_q"] + 1)
            self.assertFalse(torch.cuda.is_initialized())


if __name__ == "__main__":
    torch.set_num_threads(1)
    unittest.main()

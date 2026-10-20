"""Run the real final counter through extracted observer code, using CPU tensors."""

import argparse
import ast
import io
import json
import sys
import types
import unittest
from contextlib import redirect_stdout
from functools import wraps
from pathlib import Path
from unittest.mock import patch

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fr3_contact import evaluation

PREPARED = Path("D:/Projects/Robots/spatial-sync/.scratch/rsi-platform/issue-29-round2/first-home-prep-v4")


def extracted_function(node, namespace):
    exec(
        compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "observer.py", "exec"),
        namespace,
    )
    return namespace[node.name]


def real_status_classifier(tree):
    trace_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Trace")
    persist = next(
        node for node in trace_class.body if isinstance(node, ast.FunctionDef) and node.name == "persist"
    )
    assignment = next(
        node
        for node in persist.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "status" for target in node.targets)
    )
    final_exit = next(
        node
        for node in persist.body
        if isinstance(node, ast.If) and "original_exit" in ast.unparse(node.test)
    )
    function = ast.parse("def classify(flags, original_exit, done_count):\n    pass").body[0]
    function.body = ast.parse("error=None\ndisagreements=[]\nevents=[]").body + [
        assignment,
        final_exit,
        ast.Return(value=ast.Name(id="status", ctx=ast.Load())),
    ]
    return extracted_function(function, {})


class FinalControlTests(unittest.TestCase):
    def run_final_control(self, fault):
        tree = ast.parse((PREPARED / "frozen_home_trace.py").read_text(encoding="utf-8"))
        install = next(
            node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "install"
        )
        functions = {node.name: node for node in install.body if isinstance(node, ast.FunctionDef)}
        classify = real_status_classifier(tree)
        reason = torch.zeros(1024, dtype=torch.int32)
        reason[:400] = 1
        timeout = torch.zeros(1024, dtype=torch.bool)
        timeout[400:] = True
        terminal = {"reasons": reason, "max_excess": torch.zeros(1024)}
        env = types.SimpleNamespace(
            _fr3_terminal_validity=terminal,
            _fr3_terminal_success=torch.zeros(1024, dtype=torch.bool),
            _fr3_terminal_progress=torch.zeros((1024, 4), dtype=torch.bool),
            _fr3_terminal_started_from_home=torch.ones(1024, dtype=torch.bool),
            _fr3_terminal_max_rack_force=torch.zeros(1024),
            _fr3_terminal_raw_placement=torch.zeros(1024, dtype=torch.bool),
            _fr3_terminal_time_to_success_s=torch.full((1024,), -1.0),
            termination_manager=types.SimpleNamespace(
                get_term=lambda name: timeout if name == "time_out" else torch.zeros(1024, dtype=torch.bool)
            ),
        )
        state = types.SimpleNamespace(
            **{name: torch.zeros((1024, 2)) for name in ("joint_q", "joint_qd", "body_q", "body_qd")}
        )
        native = types.SimpleNamespace(
            **{
                name: torch.zeros((1024, 1))
                for name in ("qacc_warmstart", "qfrc_applied", "xfrc_applied", "ctrl", "act")
            }
        )
        manager = types.SimpleNamespace(_state_0=state, _solver=types.SimpleNamespace(mjw_data=native))
        facts_before = {name: value.clone() for name, value in terminal.items()}
        first_done = torch.stack((torch.ones(1024, dtype=torch.int32), reason, timeout.int()), -1)
        first_before = first_done.clone()
        trace = types.SimpleNamespace(
            flags=torch.zeros(1, dtype=torch.int32), first=first_done, artifact=None
        )

        def persist(reason_text, original_exit=None, error=None):
            trace.artifact = {
                "status": classify(int(trace.flags[0]), original_exit, 1024),
                "reason": reason_text,
                "flags": int(trace.flags[0]),
                "first_done_count": int(trace.first[:, 0].sum()),
                "range": int((trace.first[:, 1] == 1).sum()),
                "timeout": int(trace.first[:, 2].sum()),
                "original_exit": original_exit,
                "runtime_error": str(error) if error else None,
            }
            return trace.artifact["status"]

        trace.persist = persist
        observations = {"policy": torch.zeros((1024, 2))}
        rewards = torch.zeros(1024)
        if fault == "returned_observation_nan":
            observations["policy"][1023, 0] = float("nan")
        elif fault == "returned_reward_nan":
            rewards[1023] = float("nan")
        elif fault == "recovered_state_nan":
            state.joint_q[1023, 0] = float("nan")
        elif fault == "recovered_native_nan":
            native.qacc_warmstart[1023, 0] = float("nan")
        elif fault == "positive_invalid_reward":
            rewards[399] = 1.0
        result = (observations, rewards, torch.ones(1024, dtype=torch.bool), {})
        inputs_before = [
            value.clone()
            for value in (rewards, *observations.values(), *vars(state).values(), *vars(native).values())
        ]

        class Wrapper:
            num_envs, device = 1024, "cpu"
            unwrapped = env

            def step(self, actions):
                return result

        namespace = {
            "wraps": wraps,
            "torch": torch,
            "tensor": lambda value: value,
            "trace_for": lambda actual_env: trace,
            "NewtonManager": manager,
        }
        namespace["RslRlVecEnvWrapper"] = Wrapper
        namespace["evaluation"] = types.SimpleNamespace(
            install_episode_counter=lambda: evaluation._install_episode_counter(1024)
        )
        # Execute the generated install order itself: capture, guard, binding, original counter.
        # The frozen v3 code has only the counter binding and must fail the fault expectations.
        registration = []
        for node in install.body:
            if isinstance(node, ast.FunctionDef) and node.name == "returned_step":
                registration.append(node)
            elif isinstance(node, ast.Assign):
                targets = {ast.unparse(target) for target in node.targets}
                if "original_returned_step" in targets or "arguments" in targets:
                    registration.append(node)
                elif "RslRlVecEnvWrapper.step" in targets and ast.unparse(node.value) == "returned_step":
                    registration.append(node)
        fake_package, fake_rl = types.ModuleType("isaaclab_rl"), types.ModuleType("isaaclab_rl.rsl_rl")
        fake_rl.RslRlVecEnvWrapper = Wrapper
        with patch.dict("sys.modules", {"isaaclab_rl": fake_package, "isaaclab_rl.rsl_rl": fake_rl}):
            exec(
                compile(
                    ast.fix_missing_locations(ast.Module(body=registration, type_ignores=[])),
                    "observer-install.py",
                    "exec",
                ),
                namespace,
            )
        namespace["original_counted_step"] = Wrapper.step
        outer = extracted_function(functions["counted_step"], namespace)
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
            outer(Wrapper(), torch.zeros((1024, 8)))
        self.assertEqual(stopped.exception.code, 0)  # Keep the original oracle's exit behavior.
        oracle = json.loads(
            next(
                line.split("=", 1)[1]
                for line in output.getvalue().splitlines()
                if line.startswith("FR3_EVAL_RESULT=")
            )
        )
        self.assertEqual(oracle["episodes"], 1024)
        self.assertEqual(oracle["invalid_by_reason"]["range"], 400)
        self.assertEqual(oracle["timeout_rate"], 624 / 1024)
        self.assertEqual(oracle["contact_grasp_successes"], 0)
        torch.testing.assert_close(trace.first, first_before)
        for name, before in facts_before.items():
            torch.testing.assert_close(terminal[name], before)
        after = (rewards, *observations.values(), *vars(state).values(), *vars(native).values())
        for value, before in zip(after, inputs_before, strict=True):
            torch.testing.assert_close(value, before, equal_nan=True)
        self.assertFalse(torch.cuda.is_initialized())
        print(
            json.dumps(
                {
                    "cpu_fixture": True,
                    "fault": fault,
                    "artifact": trace.artifact,
                    "oracle_facts_preserved": True,
                    "inputs_unchanged": True,
                    "cuda_initialized": torch.cuda.is_initialized(),
                }
            )
        )
        return trace.artifact

    def test_final_faults_are_latched_before_original_exit(self):
        for fault, bit in (
            ("returned_observation_nan", 2),
            ("returned_reward_nan", 2),
            ("recovered_state_nan", 16),
            ("recovered_native_nan", 16),
            ("positive_invalid_reward", 64),
        ):
            with self.subTest(fault=fault):
                artifact = self.run_final_control(fault)
                self.assertEqual(artifact["status"], "diagnostic_error", artifact)
                self.assertEqual(artifact["flags"] & bit, bit, artifact)

    def test_finite_final_return_preserves_original_completion(self):
        artifact = self.run_final_control(None)
        self.assertEqual(artifact["status"], "original_oracle_completed_diagnostic_artifact")
        self.assertEqual(artifact["flags"], 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepared", type=Path, default=PREPARED)
    args, remainder = parser.parse_known_args()
    PREPARED = args.prepared
    torch.set_num_threads(1)
    unittest.main(argv=[sys.argv[0], *remainder])

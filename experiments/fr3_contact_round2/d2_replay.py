"""Private, unexecuted D2 callback: fixed D1 actions, one gripper-only contrast.

The original Task, raw observer and first-episode oracle remain their owners.
Import this module only inside an explicitly scheduled, owned diagnostic job.
"""

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import sys

import torch

CONTROLS, WORLDS, ACTIONS = 600, 1024, 8
ALPHA = 0.25
TRACE_SHA256 = "d112c87a8c1fc3dc7ade76eabfbf834c6ea0091bdc3034c54d18de518d6b0587"


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def same_bytes(left, right):
    return torch.equal(left.contiguous().view(torch.uint8), right.contiguous().view(torch.uint8))


def load_schedule(path):
    """Validate the frozen CPU data, without a policy forward or checkpoint load."""
    if file_sha(path) != TRACE_SHA256:
        raise RuntimeError("D1 trace identity changed")
    packet = torch.load(path, map_location="cpu", weights_only=True)
    requested = packet["actual_mean_action_history"]
    epochs = packet["episode_history"]
    first = packet["first_done"]
    home = packet["home_rows"]
    if (requested.shape != (CONTROLS, WORLDS, ACTIONS) or requested.dtype != torch.float32
            or requested.device.type != "cpu" or not torch.isfinite(requested).all()
            or bool((requested.abs() > 1.0).any())):
        raise RuntimeError("Expected finite CPU float32 D1 actions600x1024x8")
    expected_home = torch.arange(128, dtype=torch.int32).repeat(8)
    if home.shape != (WORLDS,) or not torch.equal(home.to(torch.int32), expected_home):
        raise RuntimeError("D1 canonical home/world mapping changed")
    if (epochs.shape != (CONTROLS, WORLDS, 2) or first.shape != (WORLDS, 10)
            or not (first[:, 0] == 1).all() or not (first[:, 7] == 1).all()
            or not (first[:, 8] == home).all() or not (first[:, 9] == 1).all()):
        raise RuntimeError("D1 first-episode/epoch layout changed")
    if ((first[:, 3] == 1).sum().item() != 400 or first[:, 5].sum().item() != 624
            or first[:, 6].sum().item() != 0):
        raise RuntimeError("D1 first-episode baseline changed")
    if not (epochs[0, :, 1] == 1).all():
        raise RuntimeError("D1 schedule does not start at first episodes")
    # Unfiltered arms are copied, never recomputed; float32 arithmetic touches column7 only.
    filtered = requested.clone()
    for control in range(1, CONTROLS):
        filtered[control, :, -1] = (
            ALPHA * requested[control, :, -1] + (1.0 - ALPHA) * filtered[control - 1, :, -1]
        )
    if not same_bytes(filtered[:, :, :7], requested[:, :, :7]):
        raise RuntimeError("Gripper transform changed an arm action")
    if not same_bytes(filtered[0], requested[0]) or not torch.isfinite(filtered).all():
        raise RuntimeError("World-local filter initialization/finite check failed")
    return requested, filtered, epochs, first, home


class ReplayPolicy:
    def __init__(self, actions, device):
        self.actions, self.device, self.index = actions, device, 0

    def __call__(self, observations):
        del observations  # No policy, observation normalization, or feedback inference.
        if self.index >= CONTROLS:
            raise RuntimeError("D2 exhausted its frozen600-control schedule")
        action = self.actions[self.index].to(self.device).clone()
        self.index += 1
        return action

    def reset(self, dones):
        del dones  # Never mix worlds or restart the schedule/filter on an autoreset.


def install():
    """Called by the pinned play backend before it constructs its environment/runner."""
    identity = json.loads(Path(os.environ["FR3_DIAG_IDENTITY_MANIFEST"]).read_text(encoding="utf-8"))
    arm = os.environ["FR3_D2_ARM"]
    if arm not in ("A", "B"):
        raise RuntimeError("D2 arm must be the literal A or B")
    for package, expected in identity["versions"].items():
        if metadata.version(package) != expected:
            raise RuntimeError("Protected environment version changed: " + package)
    backend = sys._getframe(1).f_globals
    backend_path = Path(backend.get("__file__", "")).resolve()
    expected_backend = identity["files"]["d2_play_backend"]
    if (backend_path != Path(expected_backend["path"]).resolve()
            or file_sha(backend_path) != expected_backend["sha256"]
            or backend.get("OnPolicyRunner").__name__ != "OnPolicyRunner"
            or backend["args_cli"].deterministic or backend["args_cli"].video
            or backend["args_cli"].real_time or backend["args_cli"].train_env_cfg):
        raise RuntimeError("Pinned play backend/default playback contract changed")
    requested, filtered, reference_epochs, reference_first, reference_home = load_schedule(
        identity["d2_trace"]
    )
    effective = requested if arm == "A" else filtered
    out = Path(os.environ["FR3_DIAG_EVIDENCE"])

    class ReplayRunner:
        """Only this play invocation uses the recorded-action adapter; no model is built."""
        def __init__(self, env, train_cfg, log_dir, device):
            del log_dir, device
            if (train_cfg["class_name"] != "OnPolicyRunner" or train_cfg["seed"] != 42
                    or train_cfg["clip_actions"] != 1.0 or env.num_envs != WORLDS):
                raise RuntimeError("D2 runner/configuration changed")
            self.env = env

        def load(self, filename):
            if Path(filename).resolve() != Path(identity["checkpoint"]).resolve():
                raise RuntimeError("Checkpoint identity token changed")
            # The CLI retains its old path token; intentionally no torch.load(checkpoint).

        def get_inference_policy(self, device):
            return ReplayPolicy(effective, device)

        def export_policy_to_jit(self, **kwargs):
            del kwargs  # This adapter has no learned policy; never write old exported assets.

        def export_policy_to_onnx(self, **kwargs):
            del kwargs

    backend["OnPolicyRunner"] = ReplayRunner
    import round2_home
    import frozen_home_trace as base

    arguments = round2_home.install()
    original_trace = base.Trace

    class ReplayTrace(original_trace):
        def __init__(self, model, observer):
            super().__init__(model, observer)
            self.d2_rows = []

        def manager_capture(self, env, manager, done):
            from isaaclab_newton.physics import NewtonManager

            index = self.controls - 1
            actual = base.tensor(self.mean).detach().cpu()
            if index != len(self.d2_rows) or not same_bytes(actual, effective[index]):
                raise RuntimeError("Effective replay actions were reordered, clipped or changed")
            state, control = NewtonManager._state_0, NewtonManager._control
            device = str(self.device)
            mapping = self.mapping

            def gather(value, name):
                ids = torch.as_tensor(mapping[name], device=device).long()
                return base.tensor(value)[ids].detach().cpu()

            # Capture all worlds before autoreset; never select by A's first-done mask.
            terminal = env._fr3_terminal_validity
            epoch = base.tensor(self.episode).detach().cpu().clone()
            raw = {key: value.detach().cpu().clone() for key, value in terminal.items()}
            if self.d2_rows:
                previous = self.d2_rows[-1]
                same_epoch = previous["epoch"] == epoch
                cleared = previous["raw"]["reasons"] & ~raw["reasons"]
                if bool((same_epoch & (cleared != 0)).any()):
                    raise RuntimeError("Original raw-invalid facts cleared within an epoch")
            self.d2_rows.append({
                "epoch": epoch,
                "episode_step": env.episode_length_buf.detach().cpu().clone(),
                "live_before": (base.tensor(self.first)[:, 0] == 0).detach().cpu(),
                "done": done.detach().cpu().clone(),
                "raw": raw,
                "q": gather(state.joint_q, "q_ids"),
                "qd": gather(state.joint_qd, "v_ids"),
                "target_q": gather(control.joint_target_q, "target_q_ids"),
                "target_qd": gather(control.joint_target_qd, "target_qd_ids"),
            })
            super().manager_capture(env, manager, done)

        def persist(self, reason, original_exit=None, error=None):
            status = super().persist(reason, original_exit=original_exit, error=error)
            if status is None:
                return status
            summary = json.loads((out / "trace.json").read_text(encoding="utf-8"))
            first = base.tensor(self.first).detach().cpu()
            manager_first = base.tensor(self.manager_first).detach().cpu()
            raw = first[:, base.FIRST_FIELDS.index("raw_reasons")]
            failures = {name: int(((raw & bit) != 0).sum()) for name, bit in
                        (("range", 1), ("velocity", 2), ("tube_lost", 4), ("nonfinite", 8))}
            checks = {
                "original_oracle_completed": status == "original_oracle_completed_diagnostic_artifact",
                "first1024": bool((first[:, 0] == 1).all()),
                "home_unseeded": bool((first[:, 7] == 1).all() and (first[:, 9] == 1).all()),
                "home_mapping": torch.equal(base.tensor(self.home).cpu(), reference_home),
                "all_controls_recorded": len(self.d2_rows) == self.controls,
                "schedule_limit": self.controls <= CONTROLS,
                "original_flags": not (summary["flags"] & ~1),
                "no_oracle_disagreement": not summary["disagreements"],
            }
            if arm == "A":
                events = summary["events"]
                checks.update({
                    "baseline600": self.controls == CONTROLS,
                    "baseline0_400_624": failures == {"range": 400, "velocity": 0, "tube_lost": 0, "nonfinite": 0}
                        and int(first[:, 5].sum()) == 624 and int(first[:, 6].sum()) == 0,
                    "baseline_first_facts": torch.equal(first, reference_first),
                    "baseline_first_event": bool(events) and
                        (events[0]["world"], events[0]["native_meta"]["control"], events[0]["native_tick"])
                        == (51, 111, 887),
                    "baseline_zero_stages": all(not bool(manager_first[:, base.MANAGER_FIELDS.index(name)].any())
                        for name in ("grasped", "lifted", "inserted", "success", "raw_placement")),
                    "baseline_epochs": torch.equal(
                        torch.stack([row["epoch"] for row in self.d2_rows]), reference_epochs[:, :, 1]
                    ) if len(self.d2_rows) == CONTROLS else False,
                })
            qualification = {
                "arm": arm, "state": "qualified" if all(checks.values()) else "inconclusive",
                "checks": checks, "original_status": status, "invalid_by_reason": failures,
                "successes": int(first[:, 6].sum()), "timeouts": int(first[:, 5].sum()),
                "h1_range_reduction_supported": arm == "B" and all(checks.values())
                    and failures["range"] <= 80 and failures["nonfinite"] == 0,
                "acceptance_claim": False, "training_authorized": False,
                "h2_limit": "Filter does not establish reward causation or attainable closing/contact geometry",
            }
            with (out / "d2-replay.pt").open("xb") as stream:
                torch.save({
                    "arm": arm, "alpha": ALPHA, "requested_actions": requested[:self.controls],
                    "effective_actions": effective[:self.controls], "control_rows": self.d2_rows,
                    "reference_epochs": reference_epochs[:self.controls], "reference_first_done": reference_first,
                    "first_done": first, "d1_trace_sha256": TRACE_SHA256,
                    "continuation_note": "After an A reference terminal, fixed D1 commands were generated in its later epoch;"
                        " B keeps executing the same global schedule until its own first terminal."
                        " Such continuation is an open-loop diagnostic, not a learned policy trajectory.",
                }, stream)
            with (out / "d2-qualification.json").open("x", encoding="utf-8") as stream:
                json.dump(qualification, stream, indent=2, allow_nan=False)
            if not all(checks.values()):
                raise RuntimeError("D2 original-oracle/replay qualification failed; do not start another arm")
            return status

    base.Trace = ReplayTrace
    print("FR3_D2_REPLAY=" + json.dumps({"arm": arm, "alpha": ALPHA, "controls": CONTROLS,
        "policy_forward": False, "checkpoint_loaded": False, "exported": False}), flush=True)
    return arguments

"""Create a private D2 manifest/supervisor from pinned D1 files; never run a probe.

SOURCE CANDIDATE ONLY: this preparation program has not been executed or compiled.
No simulator imports, simulation launches, environment sync or budget writes here.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError("Pinned D1 supervisor source seam changed: " + before)
    return text.replace(before, after)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root, prepared = args.root.resolve(), args.directory.resolve()
    repo = root / ".worktrees/issue-29-round2"
    archive = root / ".scratch/rsi-platform/issue-29-round2"
    d1 = archive / "first-home-prep-v4"
    manifest = d1 / "prepared_identity.json"
    if sha(manifest) != "6261b8af82e10a5535594336d9a6ed2a7c7ace3778b256eabc1339bae20695fe":
        raise RuntimeError("Reviewed D1 manifest changed")
    if prepared.parent != archive or prepared.exists():
        raise ValueError("Choose a fresh direct archive child for D2 preparation")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True).strip():
        raise RuntimeError("Freeze a clean candidate before preparing executable inputs")
    identity = json.loads(manifest.read_text(encoding="utf-8"))
    for item in identity["files"].values():
        if sha(Path(item["path"])) != item["sha256"]:
            raise RuntimeError("Frozen D1 input changed: " + item["path"])
    source = Path(__file__).resolve().parent
    prepared.mkdir()
    for name in ("frozen_home_trace.py", "trace_contract.py", "round2_home.py", "owned_status.py", "run_owned_terminal.py"):
        (prepared / name).write_bytes((d1 / name).read_bytes())
    (prepared / "d2_replay.py").write_bytes((source / "d2_replay.py").read_bytes())
    watchdog = (d1 / "bounded_watchdog.py").read_text(encoding="utf-8")
    for before, after in (
        ("HARD_SECONDS = 900.0", "HARD_SECONDS = 430.0"),
        ("'--timeout-seconds', '870'", "'--timeout-seconds', '420'"),
        ("'round2_home.install'", "'d2_replay.install'"),
        ("dt.timedelta(seconds=840)", "dt.timedelta(seconds=390)"),
        ("('frozen_home_trace.py', 'trace_contract.py', 'round2_home.py', 'owned_status.py')",
         "('frozen_home_trace.py', 'trace_contract.py', 'round2_home.py', 'owned_status.py', 'd2_replay.py')"),
    ):
        watchdog = replace_once(watchdog, before, after)
    (prepared / "bounded_watchdog.py").write_text(watchdog, encoding="utf-8", newline="\n")
    identity["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    identity["run_owned"] = str(prepared / "run_owned_terminal.py")
    identity["d2_trace"] = str(archive / "frozen-home-all-first-20261020-164027701/trace.pt")
    identity["files"] = dict(identity["files"])
    # Old files remain pinned; add new helper/backend/data pins without weakening them.
    extra = {
        "d2_trace": Path(identity["d2_trace"]),
        "d2_play_backend": Path("D:/Projects/Robots/IsaacLab/source/isaaclab_rl/isaaclab_rl/entrypoints/backends/play_rsl_rl.py"),
        "d2_vecenv_wrapper": Path("D:/Projects/Robots/IsaacLab/source/isaaclab_rl/isaaclab_rl/rsl_rl/vecenv_wrapper.py"),
        "d2_tutorial_runner": Path("D:/Projects/Robots/IsaacLabTutorial/src/isaaclab_tutorial/tasks/place_vial/config/so101/agents/rsl_rl_ppo_cfg.py"),
        "d2_tutorial_physics": Path("D:/Projects/Robots/IsaacLabTutorial/src/isaaclab_tutorial/tasks/place_vial/config/so101/env_cfg.py"),
        "d2_tutorial_actions": Path("D:/Projects/Robots/IsaacLabTutorial/src/isaaclab_tutorial/tasks/place_vial/mdp/actions.py"),
        "d2_original_manifest": manifest,
    }
    extra.update({"d2_source_" + path.name: path for path in source.glob("*") if path.is_file()})
    extra.update({"d2_prepared_" + path.name: path for path in prepared.iterdir() if path.is_file()})
    for key, path in extra.items():
        identity["files"][key] = {"path": str(path), "sha256": sha(path)}
    if identity["files"]["d2_trace"]["sha256"] != "d112c87a8c1fc3dc7ade76eabfbf834c6ea0091bdc3034c54d18de518d6b0587":
        raise RuntimeError("Frozen D1 trace changed")
    identity["prepared_module_sha256"] = {
        name: sha(prepared / name) for name in
        ("frozen_home_trace.py", "trace_contract.py", "round2_home.py", "owned_status.py", "d2_replay.py")
    }
    (prepared / "prepared_identity.json").write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    request = {
        "state": "prepared_unexecuted_requires_independent_review_and_root_go",
        "source_commit": identity["source_commit"], "identity": str(prepared / "prepared_identity.json"),
        "identity_sha256": sha(prepared / "prepared_identity.json"), "reservation_seconds": 900,
        "per_arm_inclusive_seconds": 450, "soft_seconds": 390, "supervisor_seconds": 420,
        "hard_seconds": 430, "cleanup_seconds": 15, "launcher_headroom_seconds": 5,
        "arms": ["A", "B"], "alpha": 0.25, "d1_trace_sha256": identity["files"]["d2_trace"]["sha256"],
        "gpu_executions": 0, "budget_reserved": False, "training_authorized": False,
        "launcher": str(source / "Run-D2-Replay.ps1"),
        "payload_argv": [identity["canonical_python"], identity["audit_entry"], "play", "--rl_library", "rsl_rl",
                         "--task", "Lab-FR3-Pick-Tube-v0", "--num_envs", "1024", "--checkpoint",
                         identity["checkpoint"], "--external_callback", "d2_replay.install", "presets=newton_mjwarp"],
        "phase_rule": "Exact fresh phase from reviewed Root GO, suffixed -A then -B; evidence is archive/phase",
        "inner_prefix_argv": [identity["primary_python"], identity["run_owned"], "--phase", "<PHASE>-<ARM>",
                              "--evidence", "<ARCHIVE>/<PHASE>-<ARM>", "--cwd", "<ARCHIVE>/<PHASE>-<ARM>",
                              "--source-commit", identity["source_commit"], "--timeout-seconds", "420", "--"],
    }
    (prepared / "EXECUTION_REQUEST.json").write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

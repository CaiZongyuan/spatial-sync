"""Freeze the first future GPU probe; this command imports no simulator."""

import argparse
import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("D:/Projects/Robots/spatial-sync")
OLD = ROOT / ".scratch/rsi-platform/issue-29"
NEW = ROOT / ".scratch/rsi-platform/issue-29-round2"
REPO = ROOT / ".worktrees/issue-29-round2"
PREP = NEW / "first-home-prep-v3"
PYTHON = ROOT / ".worktrees/issue-29/experiments/fr3_contact/.venv/Scripts/python.exe"


def sha(path):
    return hashlib.file_digest(Path(path).open("rb"), "sha256").hexdigest()


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError(f"Expected exactly one frozen source seam: {before!r}")
    return text.replace(before, after)


def write(name, text):
    with (PREP / name).open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def main():
    global PREP
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=PREP)
    args = parser.parse_args()
    PREP = args.directory.resolve()
    PREP.mkdir(parents=True, exist_ok=False)
    base = OLD / "home-trace-prep-v1"
    native = OLD / "home-native-prep-v3"
    trace = (base / "frozen_home_trace.py").read_text(encoding="utf-8")
    trace = trace.replace("32 * 1024**2", "64 * 1024**2").replace("exceed32MiB", "exceed64MiB")
    trace = replace_once(trace, "cap must remain180", "cap must remain601")
    trace = replace_once(trace, "'range_counterexample' if flags & 1 else\n                  ", "")
    trace = replace_once(
        trace, "'timeout_reproduction_claim': False", "'timeout_reproduction_claim': done_count == 1024"
    )
    write("frozen_home_trace.py", trace)
    contract = (
        (base / "trace_contract.py")
        .read_text(encoding="utf-8")
        .replace("MAX_CONTROLS = 180", "MAX_CONTROLS = 601")
    )
    contract = contract.replace("32 * 1024**2", "64 * 1024**2").replace(
        "exceeds the32MiB", "exceeds the64MiB"
    )
    write("trace_contract.py", contract)
    watchdog = (native / "bounded_watchdog.py").read_text(encoding="utf-8")
    watchdog = watchdog.replace("HARD_SECONDS = 120.0", "HARD_SECONDS = 900.0")
    watchdog = watchdog.replace("'105'", "'870'").replace("seconds=90", "seconds=840")
    watchdog = watchdog.replace("'native_home_trace.install'", "'round2_home.install'")
    watchdog = watchdog.replace("'FR3_DIAG_MAX_CONTROL_STEPS': '111'", "'FR3_DIAG_MAX_CONTROL_STEPS': '601'")
    watchdog = replace_once(
        watchdog,
        "'FR3_CONTACT_ASSETS': identity['assets']",
        "'WARP_CACHE_PATH': identity['warp_cache_base'], 'FR3_CONTACT_ASSETS': identity['assets']",
    )
    watchdog = replace_once(
        watchdog,
        "('frozen_home_trace.py', 'trace_contract.py', 'native_home_trace.py', "
        "'native_contract.py', 'owned_status.py')",
        "('frozen_home_trace.py', 'trace_contract.py', 'round2_home.py', 'owned_status.py')",
    )
    write("bounded_watchdog.py", watchdog)
    for name in ("run_owned_terminal.py", "owned_status.py"):
        write(name, (native / name).read_text(encoding="utf-8"))
    write("round2_home.py", (Path(__file__).parent / "round2_home.py").read_text(encoding="utf-8"))
    prior = json.loads((base / "prepared_identity.json").read_text(encoding="utf-8"))
    identity = {
        **prior,
        "archive": str(NEW),
        "source_repository": str(REPO),
        "primary_python": str(PYTHON),
        "warp_cache_base": str(NEW / "warp-cache-v1"),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip(),
        "run_owned": str(PREP / "run_owned_terminal.py"),
        "prepared_module_sha256": {
            name: sha(PREP / name)
            for name in ("frozen_home_trace.py", "trace_contract.py", "round2_home.py", "owned_status.py")
        },
    }
    # Keep every original identity input and explicitly freeze the new owned observer/supervisor files.
    identity["files"] = dict(prior["files"])
    cache_receipt = NEW / "cache-seed-receipt-v1.json"
    identity["files"]["round2_cache_seed_receipt"] = {
        "path": str(cache_receipt),
        "sha256": sha(cache_receipt),
    }
    for path in sorted((REPO / "experiments/fr3_contact").glob("*.py")):
        identity["files"]["round2_task_" + path.name] = {"path": str(path), "sha256": sha(path)}
    for path in PREP.iterdir():
        if path.is_file():
            identity["files"]["round2_" + path.name] = {"path": str(path), "sha256": sha(path)}
    write("prepared_identity.json", json.dumps(identity, indent=2, ensure_ascii=False) + "\n")
    for name in (
        "frozen_home_trace.py",
        "trace_contract.py",
        "bounded_watchdog.py",
        "round2_home.py",
        "run_owned_terminal.py",
        "owned_status.py",
    ):
        ast.parse((PREP / name).read_text(encoding="utf-8"), filename=name)
    namespace = {"__name__": "cpu_contract"}
    exec(compile(contract, "trace_contract.py", "exec"), namespace)
    buffer_plan = namespace["planned_buffer_bytes"]()
    report = {
        "state": "prepared_not_executed",
        "prepared_identity_sha256": sha(PREP / "prepared_identity.json"),
        "gpu_execution_count": 0,
        "diagnostic_reservation_seconds": 930,
        "hard_execution_seconds": 900,
        "cleanup_seconds": 15,
        "max_controls": 601,
        "buffer_plan": buffer_plan,
        "ast_checks": "6 scripts parsed; no simulator imported",
        "simulator_modules_imported": [
            name
            for name in sys.modules
            if name.split(".")[0] in {"warp", "newton", "isaaclab", "mujoco", "mujoco_warp", "torch"}
        ],
    }
    assert not report["simulator_modules_imported"]
    write("PREPARATION_STATUS.json", json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

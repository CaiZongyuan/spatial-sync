"""Exactly one future common-boundary run, owned outer480s, fresh GO only.

Preparation does not execute this entrypoint. It reuses the already Source-free
tested supervise() ownership/deadline implementation without changing it.
"""
import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import psutil

ENTRY_DIR = Path(__file__).resolve().parent
TOOLS_DIR = ENTRY_DIR.parent
sys.path.insert(0, str(ENTRY_DIR))
sys.path.insert(1, str(TOOLS_DIR))

import overlay_common_trace as trace
from run_authorized_pair import owner, supervise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--prepared-audit", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8023)
    args = parser.parse_args()
    started = time.monotonic()
    trace.validate_go(json.loads(args.go.read_text()))
    trace.require_execution_identity()
    assert trace.base.sha(trace.base.__file__) == trace.BASE_SHA
    root = args.output_root.resolve()
    assert not str(root).startswith("/mnt/") and not root.exists()
    root.mkdir(parents=True, mode=0o700)
    primary = trace.base.HERE / root.name
    assert not primary.exists()
    primary.mkdir()
    prepared = json.loads((args.prepared_audit / "environment-audit.json").read_text())
    overlay = prepared["overlay"]
    candidate_overlay = trace.validate_candidate_overlay(prepared)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["ISSUE102_SOURCE"] = str(trace.base.SOURCE)
    os.environ["ISSUE102_TRACE_OUTER"] = "owned-480s-one-run"
    os.environ["PYTHONPATH"] = os.pathsep.join((str(trace.base.SOURCE), str(trace.base.SOURCE / "apps/cli"), overlay, prepared["extraPackageOverlay"]))
    window = {"startedUtc": datetime.now(UTC).isoformat(), "outerBudgetSeconds": 480, "maxRuns": 1, "source": trace.base.PIN, "instrumentSha256": trace.base.sha(trace.__file__), "supervisorSha256": trace.base.sha(__file__), "candidateOverlay": candidate_overlay, "owner": owner(psutil.Process()), "executionIdentity": trace.execution_identity(), "root": str(root), "primary": str(primary)}
    trace.base.dump(root / "outer-window.json", window)
    trace.base.dump(primary / "outer-window.json", window)
    print(json.dumps({"stage": "one-trace-outer-start", **window}), flush=True)
    with (root / "linux-processes-before.txt").open("wb") as file:
        subprocess.run(["ps", "-eo", "pid,ppid,lstart,pcpu,pmem,args", "--sort=pid"], stdout=file, timeout=3, check=True)
    trace.base.dump(root / "linux-contention-before.json", {"loadAverage": os.getloadavg(), "cpuCount": os.cpu_count(), "cpuPercent": psutil.cpu_percent(interval=.2), "listeners": [{"address": str(item.laddr), "pid": item.pid} for item in psutil.net_connections(kind="tcp") if item.status == psutil.CONN_LISTEN]})
    command = [sys.executable, "-B", str(Path(trace.__file__).resolve()), "--work-one", str(root / "work"), "--go", str(args.go.resolve()), "--prepared-audit", str(args.prepared_audit.resolve()), "--port", str(args.port)]
    remaining = 480 - (time.monotonic() - started) - 10
    if remaining <= 60:
        raise trace.TraceInvalid("setup consumed outer cleanup reserve before any run")
    # Exactly one supervised child command; no loop or retry path.
    result = supervise(command, root, remaining)
    with (root / "linux-processes-after.txt").open("wb") as file:
        subprocess.run(["ps", "-eo", "pid,ppid,lstart,pcpu,pmem,args", "--sort=pid"], stdout=file, timeout=3, check=True)
    trace.base.dump(root / "linux-listeners-after.json", [{"address": str(item.laddr), "pid": item.pid} for item in psutil.net_connections(kind="tcp") if item.status == psutil.CONN_LISTEN])
    copied = []
    for source in sorted(root.rglob("*")):
        if not source.is_file(): continue
        target = primary / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        copied.append({"path": str(source.relative_to(root)), "sha256": digest})
    final = {"status": "complete-instrumented-diagnostic" if result["exitCode"] == 0 else "invalid", "outerResult": result, "outerElapsedSeconds": time.monotonic() - started, "budgetSeconds": 480, "maxRuns": 1, "copiedFiles": copied}
    final["deadlineSatisfied"] = final["outerElapsedSeconds"] <= 480
    trace.base.dump(primary / "outer-final-readback.json", final)
    print(json.dumps({"stage": "one-trace-outer-end", "exitCode": result["exitCode"], "seconds": final["outerElapsedSeconds"], "deadlineSatisfied": final["deadlineSatisfied"]}), flush=True)
    if result["exitCode"] or not final["deadlineSatisfied"] or result["survivorsAfterParent"]:
        raise SystemExit(1)


if __name__ == "__main__": main()

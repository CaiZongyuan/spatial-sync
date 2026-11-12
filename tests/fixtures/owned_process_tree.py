"""Ordinary CPU subprocess descendants for cancellation and owner-loss checks."""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil


def write_json_atomic(path, payload):
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        staging.write_text(json.dumps(payload), encoding="utf-8")
        staging.replace(path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("owner", "trainer", "leaf"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--hold", action="store_true")
    args = parser.parse_args()
    if args.mode == "leaf":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        write_json_atomic(
            args.root / "leaf.json",
            {"pid": os.getpid(), "createdAt": psutil.Process().create_time()},
        )
        deadline = time.monotonic() + 30
        while not (args.root / "stop").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        return
    if args.mode == "trainer":
        subprocess.Popen([sys.executable, __file__, "leaf", str(args.root)])
        while not (args.root / "leaf.json").exists():
            time.sleep(0.01)
        if args.hold:
            while not (args.root / "stop").exists():
                time.sleep(0.02)
        return
    from apps.node.process import OwnedProcess

    with (args.root / "out.log").open("wb") as stdout, (args.root / "err.log").open("wb") as stderr:
        owned = OwnedProcess(
            [sys.executable, __file__, "trainer", str(args.root), "--hold"],
            cwd=args.root,
            env=os.environ,
            stdout=stdout,
            stderr=stderr,
        )
        write_json_atomic(
            args.root / "owner-ready.json",
            {"cleanup": str(owned.cleanup)},
        )
        try:
            owned.process.wait(timeout=30)
        finally:
            owned.close()


if __name__ == "__main__":
    main()

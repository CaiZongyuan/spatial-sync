"""Wait for the creating node to establish process ownership before launching work."""

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import psutil


def record_cleanup(path, outcome):
    path = Path(path)
    with path.open("w", encoding="ascii") as stream:
        stream.write(outcome)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        parent = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)


def main():
    request = json.loads(sys.stdin.readline())
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    if sys.platform.startswith("linux"):
        kernel = ctypes.CDLL(None, use_errno=True)
        if kernel.prctl(36, 1, 0, 0, 0) != 0:
            record_cleanup(request["cleanup"], "not_started")
            raise RuntimeError("Cannot establish child-subreaper ownership")
        signal.signal(signal.SIGTERM, stop)
        if kernel.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            record_cleanup(request["cleanup"], "not_started")
            raise RuntimeError("Cannot observe node owner loss")
        if os.getppid() != request["parentPid"]:
            record_cleanup(request["cleanup"], "not_started")
            return 1
    elif os.name != "nt":
        record_cleanup(request["cleanup"], "not_started")
        raise RuntimeError("Local node process ownership supports Linux and Windows")
    try:
        child = subprocess.Popen(
            request["command"],
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout,
            stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        record_cleanup(request["cleanup"], "not_started")
        raise
    code = 1
    try:
        while child.poll() is None and not stopping:
            time.sleep(0.02)
        code = child.returncode if child.returncode is not None else 1
    finally:
        owned = psutil.Process().children(recursive=True)
        for process in owned:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, remaining = psutil.wait_procs(owned, timeout=0.5)
        for process in remaining:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        _, remaining = psutil.wait_procs(remaining, timeout=3.5)
        # The subreaper now owns any orphaned grandchildren too; none may escape the proof.
        if remaining or psutil.Process().children(recursive=True):
            raise RuntimeError("Owned descendants remain; cleanup is unconfirmed")
        child.wait()
        record_cleanup(request["cleanup"], "clean")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

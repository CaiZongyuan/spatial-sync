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


def main():
    request = json.loads(sys.stdin.readline())
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    if sys.platform.startswith("linux"):
        kernel = ctypes.CDLL(None, use_errno=True)
        if kernel.prctl(36, 1, 0, 0, 0) != 0:
            raise RuntimeError("Cannot establish child-subreaper ownership")
        signal.signal(signal.SIGTERM, stop)
        if kernel.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
            raise RuntimeError("Cannot observe node owner loss")
        if os.getppid() != request["parentPid"]:
            Path(request["cleanup"]).write_text("clean", encoding="ascii")
            return 1
    elif os.name != "nt":
        raise RuntimeError("Local node process ownership supports Linux and Windows")
    child = subprocess.Popen(
        request["command"],
        stdin=subprocess.DEVNULL,
        stdout=sys.stdout,
        stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
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
        Path(request["cleanup"]).write_text("clean", encoding="ascii")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

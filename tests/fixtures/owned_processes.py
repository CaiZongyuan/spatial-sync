"""Snapshot and stop only the verified descendants of an owned browser-test handle."""

import json
import sys

import psutil


def identity(process):
    return {"pid": process.pid, "created": process.create_time(), "command": process.cmdline()}


def verified(value):
    try:
        process = psutil.Process(value["pid"])
        return process if identity(process) == value else None
    except psutil.NoSuchProcess:
        return None


if sys.argv[1] == "snapshot":
    try:
        root = psutil.Process(int(sys.argv[2]))
        values = [identity(process) for process in [root, *root.children(recursive=True)]]
    except psutil.NoSuchProcess:
        values = []
    print(json.dumps(values))
else:
    values = json.loads(sys.argv[2])
    # The root was captured while its current Node ChildProcess handle was live.
    # Capture later descendants only while that same root identity still exists.
    if values and (root := verified(values[0])) is not None:
        values += [identity(process) for process in root.children(recursive=True)]
    values = list({(value["pid"], value["created"]): value for value in values}.values())
    processes = []
    for value in reversed(values):
        if (process := verified(value)) is not None:
            try:
                process.terminate()
                processes.append(process)
            except psutil.NoSuchProcess:
                pass
    _, alive = psutil.wait_procs(processes, timeout=3)
    for process in alive:
        value = next(value for value in values if value["pid"] == process.pid)
        if verified(value) is not None:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
    _, alive = psutil.wait_procs(alive, timeout=3)
    print(json.dumps({"identities": values, "alive": [process.pid for process in alive]}))
    sys.exit(bool(alive))

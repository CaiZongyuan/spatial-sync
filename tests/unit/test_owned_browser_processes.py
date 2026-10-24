"""A disappearing child cannot prevent cleanup of other verified owned handles."""

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "fixtures/owned_processes.py"


@pytest.mark.parametrize("operation", ["snapshot", "stop"])
def test_exited_child_does_not_discard_live_identities_or_abort_cleanup(monkeypatch, capsys, operation):
    stopped = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return float(self.pid)

        def cmdline(self):
            if self.pid == 2:
                raise psutil.NoSuchProcess(self.pid)
            return ["owned", str(self.pid)]

        def children(self, recursive):
            return [Process(2), Process(3)]

        def terminate(self):
            stopped.append(self.pid)

    original = {"pid": 1, "created": 1., "command": ["owned", "1"]}
    reused = {"pid": 4, "created": 999., "command": ["owned", "4"]}
    fake = SimpleNamespace(Process=Process, NoSuchProcess=psutil.NoSuchProcess,
                           wait_procs=lambda processes, timeout: (processes, []))
    monkeypatch.setitem(sys.modules, "psutil", fake)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), operation,
                                     "1" if operation == "snapshot" else json.dumps([original, reused])])
    if operation == "stop":
        with pytest.raises(SystemExit) as exit:
            runpy.run_path(str(SCRIPT), run_name="__main__")
        assert exit.value.code == 0 and stopped == [3, 1]
    else:
        runpy.run_path(str(SCRIPT), run_name="__main__")
        assert stopped == []
    result = json.loads(capsys.readouterr().out)
    values = result if operation == "snapshot" else result["identities"]
    assert [value["pid"] for value in values] == ([1, 3] if operation == "snapshot" else [1, 4, 3])

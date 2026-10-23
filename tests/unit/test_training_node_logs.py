"""A producer exiting during a log read must not publish EOF from its stale prefix."""

import threading
import time
from pathlib import Path

import pytest

from apps.node.runner import OwnedRun


@pytest.mark.parametrize("stream", ["stdout.log", "stderr.log"])
def test_exit_after_log_read_waits_for_final_append_and_preserves_partial_line(tmp_path, monkeypatch, stream):
    owned = object.__new__(OwnedRun)
    owned.root, owned.uncertain_owner = tmp_path, False
    owned.metrics, owned._logs = {}, []
    owned._log_offsets = {"stdout.log": 0, "stderr.log": 0}
    owned.assignment = {"jobId": "job-log-barrier", "leaseId": "lease-log-barrier", "gpuId": None}
    owned.log_cursor, owned.baseline_elapsed = 0, 0
    owned.started, owned.finished = time.monotonic(), None
    owned.status, owned.reason = "running", None
    log = tmp_path / stream
    log.write_text("first\npartial", encoding="utf-8")
    read_done, exited = threading.Event(), threading.Event()

    def producer():
        assert read_done.wait(5)
        with log.open("a", encoding="utf-8") as writer:
            writer.write("-finished\nlast\n")
        owned.status, owned.reason, owned.finished = "failed", "controlled exit", time.monotonic()
        exited.set()

    owned.thread = threading.Thread(target=producer)
    original_read = Path.read_text

    def read_then_exit(path, *args, **kwargs):
        snapshot = original_read(path, *args, **kwargs)
        if path == log and not read_done.is_set():
            read_done.set()
            assert exited.wait(5)
            owned.thread.join(5)
            assert not owned.thread.is_alive()
        return snapshot

    monkeypatch.setattr(Path, "read_text", read_then_exit)
    owned.thread.start()
    try:
        first = owned.report()
        assert first["status"] == "running" and first["reason"] == "draining_logs"
        assert first["logs"] == [f"[{stream}] first"]
        owned.log_cursor += len(first["logs"])
        final = owned.report()
        assert final["status"] == "failed"
        assert final["logs"] == [f"[{stream}] partial-finished", f"[{stream}] last"]
        owned.log_cursor += len(final["logs"])
        assert owned.report()["logs"] == []
        assert owned._logs == first["logs"] + final["logs"]
    finally:
        read_done.set()
        owned.thread.join(5)
        assert not owned.thread.is_alive()

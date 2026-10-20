"""Bounded FIFO ownership, acknowledgements and shutdown use the actual file writer."""

import asyncio
import threading

import pytest

from packages.info.journal import AppendJournal as PlanJournal
from packages.info.journal import JournalError


async def test_full_queue_fails_closed_but_close_drains_accepted_fifo_and_joins(tmp_path, monkeypatch):
    journal = PlanJournal(tmp_path / "plans.jsonl", capacity=1)
    entered, release = threading.Event(), threading.Event()
    original = journal._write_batch

    def held_write(stream, entries):
        entered.set()
        assert release.wait(5)
        original(stream, entries)

    monkeypatch.setattr(journal, "_write_batch", held_write)
    first = journal.append('{"sequence":1}')
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        second = journal.append('{"sequence":2}')
        with pytest.raises(JournalError, match="queue is full"):
            journal.append('{"sequence":3}')
        assert journal.status["status"] == "failed"
        closing = asyncio.create_task(journal.close())
        await asyncio.sleep(0)
        assert not closing.done() and not first.done()
        release.set()
        with pytest.raises(JournalError, match="queue is full"):
            await closing
        assert first.result() == 1 and second.result() == 2
    finally:
        release.set()
        with pytest.raises(JournalError):
            await journal.close()
    assert not journal._thread.is_alive()
    assert journal.path.read_text("utf-8").splitlines() == ['{"sequence":1}', '{"sequence":2}']


async def test_fsync_failure_never_acknowledges_the_write_and_rejects_future_work(tmp_path, monkeypatch):
    journal = PlanJournal(tmp_path / "plans.jsonl")

    def failed_sync(_):
        raise OSError("Injected fsync failure")

    monkeypatch.setattr("packages.info.journal.os.fsync", failed_sync)
    ticket = journal.append('{"sequence":1}')
    with pytest.raises(JournalError, match="fsync failure"):
        await journal.flush()
    assert ticket.done() and isinstance(ticket.exception(), JournalError)
    with pytest.raises(JournalError):
        journal.append('{"sequence":2}')
    with pytest.raises(JournalError):
        await journal.close()
    assert not journal._thread.is_alive()


async def test_append_after_close_cannot_be_mistaken_for_a_saved_write(tmp_path):
    journal = PlanJournal(tmp_path / "plans.jsonl")
    await journal.close()
    with pytest.raises(JournalError, match="closed"):
        journal.append('{"sequence":1}')
    with pytest.raises(JournalError):
        await journal.flush()
    assert journal.status["status"] == "failed" and not journal.path.exists()


async def test_worker_start_failure_fails_the_ticket_and_close_does_not_join_an_unstarted_thread(
    tmp_path,
    monkeypatch,
):
    journal = PlanJournal(tmp_path / "plans.jsonl")

    def failed_start(_):
        raise RuntimeError("Injected thread startup failure")

    monkeypatch.setattr(threading.Thread, "start", failed_start)
    with pytest.raises(JournalError, match="thread startup failure"):
        journal.append('{"sequence":1}')
    with pytest.raises(JournalError, match="thread startup failure"):
        await journal.flush()
    with pytest.raises(JournalError, match="thread startup failure"):
        await journal.close()
    assert journal._thread is None and not journal.path.exists()

"""One owned FIFO writer for internal plan history; acknowledgements follow fsync."""

import asyncio
import os
import threading
from collections import deque
from concurrent.futures import Future
from pathlib import Path


class JournalError(Exception):
    pass


class PlanJournal:
    def __init__(self, path: Path, *, capacity=1024):
        self.path, self.capacity = path, capacity
        self._condition = threading.Condition()
        self._queue = deque()
        self._thread = None
        self._closing = False
        self._error = None
        self._sequence = 0
        self._acknowledged = 0
        self._tail = None

    @property
    def error(self):
        with self._condition:
            return self._error

    @property
    def status(self):
        with self._condition:
            return {
                "status": "failed"
                if self._error
                else ("saved" if self._acknowledged == self._sequence else "pending"),
                **({"message": "Plan history could not be saved"} if self._error else {}),
            }

    def append(self, payload):
        with self._condition:
            if self._error or self._closing:
                self._error = self._error or "Plan journal is closed"
                raise JournalError(self._error)
            if len(self._queue) >= self.capacity:
                self._error = "Plan history writer queue is full"
                raise JournalError(self._error)
            self._sequence += 1
            ticket = Future()
            self._tail = ticket
            self._queue.append((self._sequence, payload, ticket))
            if self._thread is None:
                try:
                    self._thread = threading.Thread(target=self._run, name="plan-history-writer", daemon=True)
                    self._thread.start()
                except Exception as exc:
                    self._thread = None
                    self._error = str(exc) or type(exc).__name__
                    self._queue.clear()
                    ticket.set_exception(JournalError(self._error))
                    raise JournalError(self._error) from exc
            self._condition.notify()
            return ticket

    @staticmethod
    def _write_batch(stream, entries):
        stream.writelines(payload + "\n" for _, payload, _ in entries)
        stream.flush()
        os.fsync(stream.fileno())

    def _run(self):
        stream, batch = None, []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.open("a", encoding="utf-8", newline="\n")
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._queue or self._closing)
                    if not self._queue:
                        break
                    batch = [self._queue.popleft() for _ in range(min(64, len(self._queue)))]
                self._write_batch(stream, batch)
                with self._condition:
                    self._acknowledged = batch[-1][0]
                for sequence, _, ticket in batch:
                    ticket.set_result(sequence)
                batch = []
        except Exception as exc:
            with self._condition:
                self._error = str(exc) or type(exc).__name__
                pending = [*batch, *self._queue]
                self._queue.clear()
            for _, _, ticket in pending:
                if not ticket.done():
                    ticket.set_exception(JournalError(self._error))
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError as exc:
                    with self._condition:
                        self._error = str(exc) or type(exc).__name__

    async def flush(self):
        with self._condition:
            ticket = self._tail
        if ticket is not None:
            # Caller cancellation never cancels a shared persistence acknowledgement.
            await asyncio.shield(asyncio.wrap_future(ticket))
        if self.error:
            raise JournalError(self.error)

    async def close(self):
        with self._condition:
            self._closing = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            await asyncio.to_thread(thread.join)
        if self.error:
            raise JournalError(self.error)

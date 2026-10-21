"""Bounded thread-safe transport handoff; only the gateway drains these receipts."""

import time
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Lock


@dataclass(frozen=True)
class BrokerReceipt:
    generation: int
    received_at: float
    kind: str
    topic: str = ""
    payload: bytes = b""
    retained: bool = False


class BrokerIngress:
    """No Program, mailbox, StateStore or event-loop callback runs on client threads.

    An overflow invalidates the connection generation. Keeping a partial stream
    could miss an offline message or accept an ack against the wrong state.
    """

    def __init__(self, *, capacity=1024, max_payload=65536, clock=time.monotonic):
        if capacity <= 0 or max_payload <= 0:
            raise ValueError("Ingress bounds must be positive")
        self._queue = Queue(maxsize=capacity)
        self._lock = Lock()
        self._clock, self.max_payload = clock, max_payload
        self._generation = 0
        self._connected = False
        self._overflowed = False
        self._closed = False

    def connected(self):
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            self._connected = True
            self._put(BrokerReceipt(self._generation, self._clock(), "connected"))

    def disconnected(self):
        with self._lock:
            if self._closed:
                return
            self._connected = False
            self._put(BrokerReceipt(self._generation, self._clock(), "disconnected"))

    def message(self, topic, payload, *, retained=False):
        # MQTT payload memory belongs to the callback's caller; own it immediately.
        received_at = self._clock()
        if len(payload) > self.max_payload:
            return False
        with self._lock:
            if self._closed or not self._connected or self._overflowed:
                return False
            return self._put(BrokerReceipt(self._generation, received_at, "message", topic,
                                           bytes(payload), bool(retained)))

    def _put(self, receipt):
        try:
            self._queue.put_nowait(receipt)
            return True
        except Full:
            self._overflowed = True
            self._connected = False
            return False

    def drain(self):
        with self._lock:
            receipts = []
            while True:
                try:
                    receipts.append(self._queue.get_nowait())
                except Empty:
                    break
            if self._overflowed:
                self._overflowed = False
                return (BrokerReceipt(self._generation, self._clock(), "disconnected"),)
            return tuple(receipts)

    def close(self):
        with self._lock:
            self._closed = True
            self._connected = False
        self.drain()

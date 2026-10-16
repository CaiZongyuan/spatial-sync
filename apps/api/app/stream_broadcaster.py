"""Nonblocking fanout. Each connection's pump is its sole WebSocket writer."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass

from starlette.websockets import WebSocket, WebSocketDisconnect

from apps.api.app.settings import Settings


@dataclass(frozen=True)
class Delivery:
    payload: str | bytes
    seq: int | None = None
    force: bool = False


class ClientConnection:
    def __init__(self, websocket: WebSocket, settings: Settings, role: str = "operator"):
        self.websocket = websocket
        self.settings = settings
        self.role = role
        self.reliable: asyncio.Queue[Delivery] = asyncio.Queue(maxsize=settings.reliable_queue_size)
        self.latest: Delivery | None = None
        self.wake = asyncio.Event()
        self.closed = asyncio.Event()
        self.subscribed = False
        self.info = True
        self.granted_rate_hz = 30.0
        self.dropped_frames = 0
        self.sent_messages = 0
        self.last_sent_seq = -1
        self._closing_code = 1000
        self._close_requested = False
        self._drain_requested = False
        self.task: asyncio.Task | None = None

    def start(self):
        self.task = asyncio.create_task(self._pump(), name="websocket-pump")

    def enqueue_reliable(
        self,
        payload: str | bytes,
        seq: int | None = None,
        force: bool = False,
    ):
        if self.closed.is_set() or self._close_requested:
            return
        if seq is not None and self.latest is not None and self.latest.seq <= seq:
            self.latest = None
        try:
            self.reliable.put_nowait(Delivery(payload, seq, force))
        except asyncio.QueueFull:
            self.request_close(1013)
        self.wake.set()

    def offer_frame(self, payload: str | bytes, seq: int):
        if self.closed.is_set() or self._close_requested or seq <= self.last_sent_seq:
            return
        if self.latest is not None:
            self.dropped_frames += 1
        self.latest = Delivery(payload, seq)
        self.wake.set()

    def request_close(self, code=1000):
        self._close_requested = True
        self._closing_code = code
        self.wake.set()
        if self.task is not None and self.task is not asyncio.current_task():
            self.task.cancel()

    async def finish(self, drain=False):
        if drain and self.task and not self.closed.is_set():
            self._drain_requested = True
            self.wake.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.closed.wait(), timeout=self.settings.send_timeout_seconds)
        self.request_close()
        if self.task:
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def _send(self, delivery: Delivery):
        send = self.websocket.send_bytes if isinstance(delivery.payload, bytes) else self.websocket.send_text
        await asyncio.wait_for(send(delivery.payload), timeout=self.settings.send_timeout_seconds)
        self.sent_messages += 1
        if delivery.seq is not None:
            self.last_sent_seq = max(self.last_sent_seq, delivery.seq)

    async def _pump(self):
        next_periodic = 0.0
        try:
            while not self._close_requested:
                self.wake.clear()
                if not self.reliable.empty():
                    delivery = self.reliable.get_nowait()
                    # Reliable frames can have been observed earlier as a periodic sample.
                    if delivery.force or delivery.seq is None or delivery.seq > self.last_sent_seq:
                        await self._send(delivery)
                    if delivery.force:
                        next_periodic = 0.0
                    continue
                if self._drain_requested:
                    break
                now = time.monotonic()
                if self.latest is not None and now >= next_periodic:
                    if next_periodic == 0:
                        next_periodic = now
                    delivery, self.latest = self.latest, None
                    if delivery.seq > self.last_sent_seq:
                        await self._send(delivery)
                    period = 1 / self.granted_rate_hz
                    next_periodic += (int((time.monotonic() - next_periodic) / period) + 1) * period
                    continue
                delay = max(0, next_periodic - now) if self.latest is not None else None
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
        except (TimeoutError, WebSocketDisconnect, OSError, RuntimeError):
            self._closing_code = 1013
        except asyncio.CancelledError:
            pass
        finally:
            self._close_requested = True
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(
                    self.websocket.close(code=self._closing_code), timeout=self.settings.send_timeout_seconds
                )
            self.closed.set()

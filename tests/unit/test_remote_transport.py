"""Controlled writer backpressure complements actual TLS capacity and peer-loss tests."""

import asyncio

import pytest

from packages.transport.remote import JSON, Limits, Outbox


async def test_controlled_drain_deadline_fails_only_the_stalled_channel_and_releases_budget():
    class Writer:
        def __init__(self, blocked):
            self.gate = asyncio.Event()
            if not blocked:
                self.gate.set()
            self.writes = []

        def write(self, value):
            self.writes.append(bytes(value))

        async def drain(self):
            await self.gate.wait()

    limits = Limits(request_seconds=0.02)
    slow_writer, healthy_writer = Writer(True), Writer(False)
    slow, healthy = Outbox(slow_writer, limits), Outbox(healthy_writer, limits)
    try:
        slow.offer((JSON, b'{"blocked":true}'))
        healthy.offer((JSON, b'{"healthy":true}'))
        done, _ = await asyncio.wait((slow.task,), timeout=0.25)
        assert slow.task in done, "a blocked drain exceeded the packet writer deadline"
        with pytest.raises(TimeoutError):
            slow.task.result()
        assert slow.bytes == 0
        await asyncio.wait_for(healthy.queue.join(), 0.25)
        assert not healthy.task.done() and healthy.bytes == 0
        assert b'{"healthy":true}' in healthy_writer.writes
    finally:
        await asyncio.gather(slow.close(), healthy.close())

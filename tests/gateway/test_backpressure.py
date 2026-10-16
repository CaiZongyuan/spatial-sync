"""Exercise the public connection writer seam with a deliberately blocked socket adapter."""

import asyncio
import json

from apps.api.app.settings import Settings
from apps.api.app.stream_broadcaster import ClientConnection


class Socket:
    def __init__(self, blocked=False):
        self.started = asyncio.Event()
        self.gate = asyncio.Event()
        if not blocked:
            self.gate.set()
        self.messages, self.close_codes = [], []
        self.writers, self.max_writers = 0, 0

    async def send_text(self, payload):
        self.writers += 1
        self.max_writers = max(self.max_writers, self.writers)
        self.started.set()
        try:
            await self.gate.wait()
            self.messages.append(payload)
        finally:
            self.writers -= 1

    async def close(self, code):
        self.close_codes.append(code)


def test_overflow_disconnects_only_slow_client_and_periodic_slot_keeps_latest():
    async def scenario():
        settings = Settings(reliable_queue_size=2, send_timeout_seconds=0.1)
        slow_socket, healthy_socket = Socket(blocked=True), Socket()
        slow, healthy = ClientConnection(slow_socket, settings), ClientConnection(healthy_socket, settings)
        slow.start()
        healthy.start()
        slow.enqueue_reliable("blocked")
        await asyncio.wait_for(slow_socket.started.wait(), 0.5)
        slow.enqueue_reliable("one")
        slow.enqueue_reliable("two")
        slow.enqueue_reliable("overflow")
        healthy.enqueue_reliable("ready")
        for seq in range(1, 10):
            healthy.offer_frame(json.dumps({"seq": seq}), seq)
        await asyncio.wait_for(slow.closed.wait(), 0.5)
        for _ in range(10):
            if len(healthy_socket.messages) == 2:
                break
            await asyncio.sleep(0)
        assert slow_socket.close_codes == [1013]
        assert healthy_socket.messages == ["ready", '{"seq": 9}']
        assert not healthy.closed.is_set() and healthy.dropped_frames == 8
        assert slow_socket.max_writers == healthy_socket.max_writers == 1
        await healthy.finish()

    asyncio.run(scenario())


def test_send_timeout_disconnects_and_reliable_frames_precede_ack_state():
    async def scenario():
        settings = Settings(send_timeout_seconds=0.02)
        socket = Socket(blocked=True)
        connection = ClientConnection(socket, settings)
        connection.start()
        connection.enqueue_reliable("timeout")
        await asyncio.wait_for(connection.closed.wait(), 0.5)
        assert socket.close_codes == [1013]
        healthy_socket = Socket()
        healthy = ClientConnection(healthy_socket, settings)
        healthy.offer_frame("old-periodic", 1)
        healthy.enqueue_reliable("command-frame", seq=2)
        healthy.enqueue_reliable("ack")
        healthy.enqueue_reliable("state")
        healthy.start()
        for _ in range(30):
            if len(healthy_socket.messages) == 3:
                break
            await asyncio.sleep(0)
        assert healthy_socket.messages == ["command-frame", "ack", "state"]
        assert healthy_socket.max_writers == 1
        await healthy.finish()

    asyncio.run(scenario())

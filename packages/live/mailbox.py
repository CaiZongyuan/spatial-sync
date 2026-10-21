"""Bounded per-device messages and commands, owned by one gateway event loop."""

from collections import deque
from copy import deepcopy
from time import monotonic


class DeviceMailbox:
    def __init__(self, *, clock=monotonic):
        self.clock = clock
        self.connected = False
        self.incoming = deque(maxlen=128)
        self.outgoing = deque(maxlen=128)
        self.last_telemetry = None
        self.telemetry_timeout = 0
        self.dispatched = {}
        self._commands = set()
        self.overflowed = False

    def online(self, now):
        return (self.connected and self.last_telemetry is not None
                and 0 <= now - self.last_telemetry < self.telemetry_timeout)

    def mark_online(self, now, timeout):
        self.last_telemetry, self.telemetry_timeout = now, timeout

    def mark_offline(self):
        self.last_telemetry = None

    def receive(self, topic, payload, *, retained=False, received_at=None, sample_time_ms=None):
        if not self.connected:
            return False
        if len(self.incoming) == self.incoming.maxlen:
            self.disconnect()
            self.overflowed = True
            return False
        at = self.clock() if received_at is None else received_at
        self.incoming.append((topic, deepcopy(payload), retained, at,
                              at * 1000 if sample_time_ms is None else sample_time_ms))
        return True

    def messages(self):
        values = tuple(self.incoming)
        self.incoming.clear()
        return values

    def send(self, topic, payload):
        if not self.connected or len(self.outgoing) == self.outgoing.maxlen:
            return False
        self.outgoing.append((topic, deepcopy(payload)))
        self._commands.add(payload["requestId"])
        return True

    def take_commands(self):
        commands = tuple(self.outgoing)
        self.outgoing.clear()
        return commands

    def mark_dispatched(self, request_id, at):
        if self.connected and request_id in self._commands:
            self.dispatched[request_id] = at

    def accepts_ack(self, request_id, received_at):
        dispatched_at = self.dispatched.get(request_id)
        return dispatched_at is not None and received_at >= dispatched_at

    def discard_command(self, request_id):
        self._commands.discard(request_id)
        self.dispatched.pop(request_id, None)
        retained = [command for command in self.outgoing if command[1]["requestId"] != request_id]
        self.outgoing.clear()
        self.outgoing.extend(retained)

    def disconnect(self):
        self.connected = False
        self.mark_offline()
        self.incoming.clear()
        self.outgoing.clear()
        self.dispatched.clear()
        self._commands.clear()

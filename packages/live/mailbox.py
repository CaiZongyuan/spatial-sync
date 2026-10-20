"""Bounded per-device messages and commands, owned by one gateway event loop."""

from collections import deque
from copy import deepcopy


class DeviceMailbox:
    def __init__(self):
        self.connected = False
        self.incoming = deque(maxlen=128)
        self.outgoing = deque(maxlen=128)
        self.last_telemetry = None
        self.telemetry_timeout = 0

    def online(self, now):
        return (self.connected and self.last_telemetry is not None
                and now - self.last_telemetry < self.telemetry_timeout)

    def mark_online(self, now, timeout):
        self.last_telemetry, self.telemetry_timeout = now, timeout

    def mark_offline(self):
        self.last_telemetry = None

    def receive(self, topic, payload, *, retained=False):
        self.incoming.append((topic, deepcopy(payload), retained))

    def messages(self):
        values = tuple(self.incoming)
        self.incoming.clear()
        return values

    def send(self, topic, payload):
        if not self.connected or len(self.outgoing) == self.outgoing.maxlen:
            return False
        self.outgoing.append((topic, deepcopy(payload)))
        return True

    def disconnect(self):
        self.connected = False
        self.mark_offline()
        self.incoming.clear()
        self.outgoing.clear()

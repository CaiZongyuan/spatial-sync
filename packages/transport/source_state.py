"""Complete source frames and sequence barriers across the two local Pipes."""

import math
import time


class SourceFrames:
    def __init__(self, manifest):
        self.manifest = manifest
        self.all_ids = frozenset(node.streamId for node in manifest.nodes if node.kind == "body")
        self.latest = None
        self.floor = -1
        self.paused = False
        self._state_seq = -1

    def _validate(self, frame):
        if (frame.sessionId, frame.sceneRevision) != (self.manifest.sessionId, self.manifest.sceneRevision):
            raise ValueError("Source frame identity does not match its Manifest")
        if len(frame.poses) != len(self.all_ids) or {row[0] for row in frame.poses} != self.all_ids:
            raise ValueError("Source frame does not contain the exact expected body set")

    def _cache(self, frame):
        if self.latest is None or frame.seq > self.latest.seq:
            self.latest = frame

    def periodic(self, frame):
        if frame.seq <= self.floor:
            return False
        self._validate(frame)
        self._cache(frame)
        return True

    def control(self, frame):
        self._validate(frame)
        self.floor = max(self.floor, frame.seq)
        self._cache(frame)

    def state(self, state):
        if state and state.seq >= self._state_seq:
            self._state_seq = state.seq
            self.paused = state.state == "PAUSED"


class RunnerHeartbeat:
    """Only a new Runner heartbeat can refresh the local liveness deadline."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.seq = None
        self._received = 0.0
        self._age = 0.0

    def observe(self, stats):
        seq, age = stats.get("runnerHeartbeatSeq"), stats.get("runnerHeartbeatMs")
        if (
            type(seq) is not int
            or seq < 0
            or not isinstance(age, (int, float))
            or not math.isfinite(age)
            or age < 0
        ):
            return
        now = self.clock()
        if self.seq is None or seq > self.seq:
            self.seq, self._age, self._received = seq, float(age), now

    def age(self, now=None):
        if self.seq is None:
            return None
        return self._age + ((self.clock() if now is None else now) - self._received) * 1000

    def stats(self, available):
        age = self.age()
        return {
            "runnerHeartbeatSeq": self.seq,
            "runnerHeartbeatMs": age,
            "runnerHeartbeatAlive": None if age is None else bool(available and age < 3000),
        }

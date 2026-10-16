"""Source sample membership, complete authority and cross-channel interest barriers."""

import math
import time


class SourceFrames:
    def __init__(self, manifest):
        self.manifest = manifest
        self.all_ids = frozenset(node.streamId for node in manifest.nodes if node.kind == "body")
        self.expected_ids = self.all_ids
        self.latest = self.complete = None
        self.floor = -1
        self.transition = False
        self.paused = False
        self._state_seq = -1

    def interest(self, stream_ids):
        if stream_ids is None:
            return None
        values = list(stream_ids)
        if any(type(value) is not int for value in values) or len(values) != len(set(values)):
            raise ValueError("Interest IDs must be unique integers")
        result = frozenset(values)
        if not result <= self.all_ids:
            raise ValueError("Interest contains an unknown Manifest streamId")
        return result

    def _validate(self, frame, expected):
        if (frame.sessionId, frame.sceneRevision) != (self.manifest.sessionId, self.manifest.sceneRevision):
            raise ValueError("Source frame identity does not match its Manifest")
        if len(frame.poses) != len(expected) or {row[0] for row in frame.poses} != expected:
            raise ValueError("Source frame does not contain the exact expected body set")

    def _cache(self, frame):
        if self.latest is None or frame.seq > self.latest.seq:
            self.latest = frame

    def periodic(self, frame):
        if self.transition or frame.seq <= self.floor:
            return False
        self._validate(frame, self.expected_ids)
        self._cache(frame)
        if self.expected_ids == self.all_ids:
            self._complete(frame)
        return True

    def _complete(self, frame):
        if self.complete is None or frame.seq >= self.complete.seq:
            self.complete = frame

    def control(self, frame):
        self._validate(frame, self.all_ids)
        self.floor = max(self.floor, frame.seq)
        self._complete(frame)
        self._cache(frame)

    def install_interest(self, stream_ids, frame):
        ids = self.interest(stream_ids)
        self.control(frame)
        self.expected_ids = self.all_ids if ids is None else ids
        self.transition = False

    def state(self, state):
        if state and state.seq >= self._state_seq:
            self._state_seq = state.seq
            self.paused = state.state == "PAUSED"


class RunnerHeartbeat:
    """Forward a relative receipt age without treating proxy traffic as a new heartbeat."""

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
        elif seq == self.seq:
            self._age = max(self.age(now), float(age))
            self._received = now

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

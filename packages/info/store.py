"""Single-writer immutable entity snapshots and event-driven readers."""

import asyncio
from collections import defaultdict, deque

from packages.protocol.schema import EntityObservations, EntitySnapshot, EntityValues, ObservationMeta


class StateStore:
    def __init__(self, scene_revision, epoch=0):
        self.scene_revision, self.epoch = scene_revision, epoch
        self.entities: dict[str, EntityValues] = {}
        self._waiters = defaultdict(set)
        self._events = deque(maxlen=10_000)
        self.event_seq = 0
        self._changed = asyncio.Event()
        self._snapshot_time = -1

    def configure(self, scene_revision, epoch=0):
        self.scene_revision, self.epoch = scene_revision, epoch
        self.entities.clear()
        self._snapshot_time = -1
        for waiters in self._waiters.values():
            for waiter in waiters:
                waiter.set()

    def apply(self, message):
        if message.sceneRevision != self.scene_revision or message.epoch < self.epoch:
            return False
        if isinstance(message, EntitySnapshot):
            if message.epoch == self.epoch and message.sampleTimeMs < self._snapshot_time:
                return False
            self._snapshot_time = message.sampleTimeMs
            self.epoch = message.epoch
            self.entities = {entity.key: entity.model_copy(deep=True) for entity in message.entities}
            for waiters in self._waiters.values():
                for waiter in waiters:
                    waiter.set()
            return True
        if not isinstance(message, EntityObservations) or message.epoch != self.epoch:
            return False
        for change in message.changes:
            previous = self.entities.get(change.key)
            if previous and change.rev < previous.rev:
                continue
            meta = dict(previous.meta) if previous else {}
            values = {
                prop: value
                for prop, value in change.values.items()
                if prop not in meta or message.sampleTimeMs >= meta[prop].sampleTimeMs
            }
            for prop in values:
                meta[prop] = ObservationMeta(
                    simTime=message.simTime, sampleTimeMs=message.sampleTimeMs, source=message.source
                )
                for waiter in self._waiters.get((change.key, prop), ()):
                    waiter.set()
            self.entities[change.key] = EntityValues(
                key=change.key,
                rev=change.rev,
                values=(previous.values if previous else {}) | values,
                meta=meta,
            )
        return True

    def values(self):
        return {key: entity.values for key, entity in self.entities.items()}

    async def wait(self, key, prop, predicate, timeout):
        event = asyncio.Event()
        waiters = self._waiters[(key, prop)]
        waiters.add(event)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        try:
            while True:
                event.clear()
                entity = self.entities.get(key)
                known = entity is not None and prop in entity.values
                value = entity.values[prop] if known else None
                if predicate(known, value):
                    return {"satisfied": True, "value": value, "timedOut": False}
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return {"satisfied": False, "value": value, "timedOut": True}
                try:
                    await asyncio.wait_for(event.wait(), remaining)
                except TimeoutError:
                    return {"satisfied": False, "value": value, "timedOut": True}
        finally:
            waiters.discard(event)
            if not waiters:
                self._waiters.pop((key, prop), None)

    def record(self, message):
        self.event_seq += 1
        self._events.append({"seq": self.event_seq, "message": message.model_dump(mode="json")})
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def events_after(self, after, timeout):
        changed = self._changed
        if after >= self.event_seq:
            try:
                await asyncio.wait_for(changed.wait(), timeout)
            except TimeoutError:
                return {"events": [], "lastSeq": self.event_seq, "timedOut": True, "gap": False}
        return {
            "events": [item for item in self._events if item["seq"] > after],
            "lastSeq": self.event_seq,
            "timedOut": False,
            "gap": bool(self._events and after < self._events[0]["seq"] - 1),
        }

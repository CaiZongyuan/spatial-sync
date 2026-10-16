"""Single-writer immutable entity snapshots and event-driven readers."""

import asyncio
import json
from collections import defaultdict, deque
from copy import deepcopy

from packages.protocol.json import encode
from packages.protocol.schema import EntityObservations, EntitySnapshot, EntityValues, ObservationMeta


class _StoredMeta(ObservationMeta, frozen=True):
    """One validated batch timestamp can be shared without mutable aliases."""


class StateStore:
    def __init__(self, scene_revision, epoch=0):
        self.scene_revision, self.epoch = scene_revision, epoch
        self.entities: dict[str, EntityValues] = {}
        self._waiters = defaultdict(set)
        self._events: deque[str] = deque(maxlen=10_000)
        self.event_seq = 0
        self._changed = asyncio.Event()
        self._snapshot_time = -1
        self._exports = {}

    def configure(self, scene_revision, epoch=0):
        self.scene_revision, self.epoch = scene_revision, epoch
        self.entities.clear()
        self._exports.clear()
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
            self._exports.clear()
            for waiters in self._waiters.values():
                for waiter in waiters:
                    waiter.set()
            return True
        if not isinstance(message, EntityObservations) or message.epoch != self.epoch:
            return False
        observed_at = _StoredMeta(
            simTime=message.simTime, sampleTimeMs=message.sampleTimeMs, source=message.source
        )
        for change in message.changes:
            previous = self.entities.get(change.key)
            if previous and change.rev < previous.rev:
                continue
            meta = dict(previous.meta) if previous else {}
            values = {
                prop: deepcopy(value) if isinstance(value, (dict, list)) else value
                for prop, value in change.values.items()
                if prop not in meta or message.sampleTimeMs >= meta[prop].sampleTimeMs
            }
            for prop in values:
                meta[prop] = observed_at
                for waiter in self._waiters.get((change.key, prop), ()):
                    waiter.set()
            update = {
                "rev": change.rev,
                "values": (previous.values if previous else {}) | values,
                "meta": meta,
            }
            # ObservationChange already validated identity, revision and every
            # JsonValue. Own mutable changes, then replace the immutable sample
            # without recursively validating unchanged properties again.
            self.entities[change.key] = (
                previous.model_copy(update=update)
                if previous
                else EntityValues.model_construct(key=change.key, **update)
            )
            self._exports.pop(change.key, None)
        return True

    def export_json(self, key):
        """Current state fields as immutable JSON; readers cannot mutate authority."""
        value = self.entities.get(key)
        if value is None:
            return None
        if key not in self._exports:
            self._exports[key] = encode(
                {
                    "rev": value.rev,
                    "values": value.values,
                    "meta": {
                        prop: {"simTime": at.simTime, "sampleTimeMs": at.sampleTimeMs, "source": at.source}
                        for prop, at in value.meta.items()
                    },
                }
            )
        return self._exports[key]

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
        document = encode(message.model_dump(mode="json"))
        # Keep the complete audit as immutable JSON, so retained observations
        # do not leave every historical change dictionary for GC to traverse.
        self._events.append(f'{{"seq":{self.event_seq},"message":{document}}}')
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()
        # Observations have no nullable model fields, so their full audit dump
        # is also the exact wire document (including null JsonValue properties).
        # Other messages retain their original exclude-none wire semantics.
        return document if isinstance(message, EntityObservations) else encode(message)

    async def events_after(self, after, timeout):
        changed = self._changed
        if after >= self.event_seq:
            try:
                await asyncio.wait_for(changed.wait(), timeout)
            except TimeoutError:
                return {"events": [], "lastSeq": self.event_seq, "timedOut": True, "gap": False}
        first_seq = self.event_seq - len(self._events) + 1
        return {
            "events": [
                json.loads(document)
                for seq, document in enumerate(self._events, first_seq)
                if seq > after
            ],
            "lastSeq": self.event_seq,
            "timedOut": False,
            "gap": bool(self._events and after < first_seq - 1),
        }

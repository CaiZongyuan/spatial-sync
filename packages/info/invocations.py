"""Bounded invocation history, idempotency and terminal notification."""

import asyncio
from collections import OrderedDict, defaultdict

from packages.protocol.schema import Invocation

TERMINAL = frozenset(("rejected", "succeeded", "failed", "cancelled"))


class InvocationTable:
    def __init__(self, evicted_lookup=None):
        self.active: dict[str, Invocation] = {}
        self._by_entity = defaultdict(dict)
        self.terminal: OrderedDict[str, Invocation] = OrderedDict()
        self.requests: dict[str, str] = {}
        self._waiters = defaultdict(set)
        # I1 bridge: I4 replaces this archive with the session log lookup.
        self._evicted = {}
        self._evicted_lookup = evicted_lookup

    def get(self, invocation_id):
        return self.active.get(invocation_id) or self.terminal.get(invocation_id)

    def active_for(self, entity_key):
        return self._by_entity.get(entity_key, {}).values()

    async def lookup(self, invocation_id):
        retained = self.get(invocation_id)
        if retained is not None:
            return retained
        if self._evicted_lookup is not None:
            return await self._evicted_lookup(invocation_id)
        return self._evicted.get(invocation_id)

    async def lookup_request(self, request_id):
        invocation_id = self.requests.get(request_id)
        return await self.lookup(invocation_id) if invocation_id else None

    def apply(self, invocation):
        # Strip the transport discriminator/revision before retaining the domain value.
        value = Invocation.model_validate(
            {
                key: item
                for key, item in invocation.model_dump().items()
                if key not in ("type", "sceneRevision")
            }
        )
        previous = self.get(value.invocationId)
        if previous is None and self.requests.get(value.requestId) == value.invocationId:
            return False  # an evicted terminal cannot be resurrected by a delayed host update
        if previous is not None and (previous.status in TERMINAL or previous == value):
            return False
        if previous and previous.status in ("accepted", "running") and value.status == "rejected":
            return False
        if previous and previous.status == "running" and value.status in ("accepted", "pending_approval"):
            return False
        self.requests.setdefault(value.requestId, value.invocationId)
        if value.status in TERMINAL:
            self.active.pop(value.invocationId, None)
            owned = self._by_entity.get(value.entityKey)
            if owned is not None:
                owned.pop(value.invocationId, None)
                if not owned:
                    self._by_entity.pop(value.entityKey, None)
            self.terminal[value.invocationId] = value
            while len(self.terminal) > 1000:
                expired_id, expired = self.terminal.popitem(last=False)
                if self._evicted_lookup is None:
                    self._evicted[expired_id] = expired
            for event in self._waiters.get(value.invocationId, ()):
                event.set()
        else:
            self.active[value.invocationId] = value
            self._by_entity[value.entityKey][value.invocationId] = value
        return True

    def list(self, *, entity=None, status=None, limit=1000):
        values = [*self.active.values(), *self.terminal.values()]
        return sorted(
            (
                item
                for item in values
                if (not entity or item.entityKey == entity) and (not status or item.status == status)
            ),
            key=lambda item: item.createdAtMs,
            reverse=True,
        )[:limit]

    def snapshot(self):
        return [*self.active.values(), *list(self.terminal.values())[-50:]]

    async def wait(self, invocation_id, timeout):
        event = asyncio.Event()
        self._waiters[invocation_id].add(event)
        try:
            value = self.get(invocation_id)
            if value is None or value.status in TERMINAL:
                return value, False
            try:
                await asyncio.wait_for(event.wait(), timeout)
            except TimeoutError:
                return await self.lookup(invocation_id), True
            return await self.lookup(invocation_id), False
        finally:
            self._waiters[invocation_id].discard(event)
            if not self._waiters[invocation_id]:
                self._waiters.pop(invocation_id, None)

"""Single-writer device programs, authoritative intake and ordered host events."""

import time
from collections import OrderedDict
from copy import deepcopy
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, ValidationError

from packages.protocol.schema import (
    EntityEvent,
    EntityObservations,
    EntitySnapshot,
    EntityValues,
    Invocation,
    InvocationError,
    InvocationUpdate,
    ServerError,
)

from .conditions import UNKNOWN, evaluate_conditions
from .diff import PropertyDiff
from .schema_subset import json_equal

TERMINAL = {"succeeded", "failed", "cancelled", "rejected"}
Reject = InvocationError


class ProgramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Program(Protocol):
    id: ClassVar[str]
    type: ClassVar[str]
    Config: ClassVar[type[BaseModel]]
    tick_priority: ClassVar[int]

    def init(self, ctx): ...
    def accept(self, inv, ctx): ...
    def start(self, inv, ctx): ...
    def tick(self, ctx, dt): ...
    def cancel(self, inv, ctx): ...


class _EntitySlot:
    """Integer property slots are private; only exports carry property names."""

    def __init__(self, key, definitions, validators, clock):
        self.key = key
        self.names = tuple(definitions)
        self.indices = {name: index for index, name in enumerate(self.names)}
        self.validators = tuple(validators[name] for name in self.names)
        self.diff = PropertyDiff(
            {index: definitions[name] for index, name in enumerate(self.names)}, clock=clock
        )
        self.reset()

    def reset(self):
        self.rev = 0
        self.values = [UNKNOWN] * len(self.names)
        self.meta = [None] * len(self.names)
        self.dirty = {}
        self.diff.reset()

    def get(self, name, default=UNKNOWN):
        index = self.indices.get(name)
        return default if index is None or self.values[index] is UNKNOWN else self.values[index]

    def export_values(self):
        return {
            name: deepcopy(value)
            for name, value in zip(self.names, self.values, strict=True)
            if value is not UNKNOWN
        }

    def snapshot(self):
        return EntityValues(
            key=self.key,
            rev=self.rev,
            values=self.export_values(),
            meta={
                name: deepcopy(meta)
                for name, meta in zip(self.names, self.meta, strict=True)
                if meta is not None
            },
        )


class _ValuesView:
    def __init__(self, host):
        self.host = host

    def get(self, key, default=None):
        index = self.host._entity_indices.get(key)
        return default if index is None else self.host._states[index]


class Ctx:
    def __init__(self, host, index, config):
        self._host, self._index, self.config = host, index, config
        self._properties = host._states[index].indices
        self._readers = {}

    @property
    def key(self):
        return self._host._states[self._index].key

    @property
    def sim_time(self):
        return self._host.sim_time

    @property
    def tick_index(self):
        return self._host.tick_index

    @property
    def fact_due(self):
        return self._host.fact_due

    @property
    def physics(self):
        return self._host.physics

    def set(self, **values):
        if self._host._executing != self._index:
            raise RuntimeError("Programs may only write their own entity during a callback")
        self._host._set(self._index, {self._properties[name]: value for name, value in values.items()})

    def get(self, prop):
        return self.read(self.key, prop)

    def read(self, entity_key, prop):
        token = entity_key, prop
        reader = self._readers.get(token)
        if reader is None:
            reader = self._readers[token] = self._host.reader(entity_key, prop)
        return reader()

    def emit(self, event, **data):
        if self._host._executing != self._index:
            raise RuntimeError("Programs may only emit events for their own entity")
        self._host._emit(self._index, event, data)

    def check(self, conditions):
        return evaluate_conditions(conditions, self.key, self._host._values_view)


class InvocationHandle:
    def __init__(self, host, invocation):
        self._host, self.invocation = host, invocation
        self.started = False
        self.last_progress_at = -float("inf")
        self._pending_progress = None
        self.accepted_at = host.sim_time
        self.entity_index = host._entity_indices[invocation.entityKey]

    @property
    def params(self):
        return deepcopy(self.invocation.params)

    @property
    def action(self):
        return self.invocation.action

    @property
    def id(self):
        return self.invocation.invocationId

    def progress(self, value):
        self._pending_progress = max(0.0, min(1.0, float(value)))

    def flush_progress(self, now):
        if (
            self._pending_progress is not None
            and self.invocation.status not in TERMINAL
            and now - self.last_progress_at + 1e-10 >= 0.25
        ):
            self.last_progress_at = now
            self._update(progress=self._pending_progress)
            self._pending_progress = None

    def _update(self, *, force=False, **fields):
        if self.invocation.status in TERMINAL and not force:
            return
        if fields.get("status") in TERMINAL:
            self._pending_progress = None
        self.invocation = self.invocation.model_copy(update=fields)
        self._host._updates[self.id] = self.invocation

    def succeed(self, result=None):
        self._update(
            status="succeeded", progress=1.0, result=deepcopy(result), endedSimTime=self._host.sim_time
        )

    def fail(self, code, message, **details):
        self._update(
            status="failed",
            error=Reject(code=code, message=message, details=details),
            endedSimTime=self._host.sim_time,
        )

    def cancelled(self):
        self._update(status="cancelled", endedSimTime=self._host.sim_time)


class HostCore:
    def __init__(
        self,
        catalog,
        registry,
        *,
        epoch=0,
        source="virtual",
        physics=None,
        fact_hz=10,
        publication_clock=None,
    ):
        self.catalog, self.epoch, self.source, self.physics = catalog, epoch, source, physics
        self.sim_time, self.tick_index, self.fact_due = 0.0, 0, True
        self.fact_hz, self._next_fact = fact_hz, 0.0
        self._publication_clock = publication_clock or time.monotonic
        self._entity_indices = {key: index for index, key in enumerate(catalog.entities)}
        self._states, self._programs, self._contexts, self._errors = [], [], [], []
        self._disabled = set()
        self._active, self._recent, self._updates = {}, OrderedDict(), OrderedDict()
        self._events, self._diagnostics = [], []
        self._executing = None
        for key, binding in catalog.entities.items():
            self._states.append(
                _EntitySlot(
                    key,
                    catalog.types[binding["type"]]["properties"],
                    catalog.property_validators(key),
                    self._publication_clock,
                )
            )
            self._programs.append(registry[binding["program"]]())
            self._contexts.append(Ctx(self, self._entity_indices[key], catalog.program_config(key)))
            self._errors.append(0)
        self._values_view = _ValuesView(self)
        self._order = sorted(
            range(len(self._programs)),
            key=lambda index: (getattr(self._programs[index], "tick_priority", 100), self._states[index].key),
        )
        for key in self._order:
            self._call(key, "init", self._contexts[key])
        self._prime_diff()

    def values(self):
        return {state.key: state.export_values() for state in self._states}

    def active_invocations(self, entity_key):
        """Read one device's active work without exporting all entity state."""
        return [
            handle.invocation.model_copy(deep=True)
            for handle in self._active.values()
            if handle.invocation.entityKey == entity_key
        ]

    def reader(self, entity_key, prop):
        index = self._entity_indices[entity_key]
        property_index = self._states[index].indices[prop]

        def read():
            value = self._states[index].values[property_index]
            return None if value is UNKNOWN else deepcopy(value)

        return read

    def _set(self, key, values):
        current = self._states[key]
        parsed = {prop: current.validators[prop](value) for prop, value in values.items()}
        changed = {
            prop: value
            for prop, value in parsed.items()
            if current.values[prop] is UNKNOWN or not json_equal(current.values[prop], value)
        }
        if not changed:
            return
        meta = {"simTime": self.sim_time, "sampleTimeMs": time.monotonic() * 1000, "source": self.source}
        current.rev += len(changed)
        for prop, value in changed.items():
            current.values[prop] = deepcopy(value)
            current.meta[prop] = meta
            current.dirty[prop] = current.values[prop]

    def _emit(self, key, name, data):
        entity_key = self._states[key].key
        data = self.catalog.validate_event(entity_key, name, data)  # undeclared events are program errors
        self._events.append(
            EntityEvent(
                sceneRevision=self.catalog.sceneRevision,
                epoch=self.epoch,
                entityKey=entity_key,
                event=name,
                data=deepcopy(data),
                simTime=self.sim_time,
                sampleTimeMs=time.monotonic() * 1000,
            )
        )

    def _call(self, key, method, *args):
        previous, self._executing = self._executing, key
        try:
            callback = getattr(self._programs[key], method, None)
            result = callback(*args) if callback else None
            self._errors[key] = 0
            return result
        except Exception as exc:
            self._errors[key] += 1
            if self._errors[key] >= 3:
                self._disabled.add(key)
            for handle in list(self._active.values()):
                if handle.entity_index == key:
                    handle.fail("program_error", str(exc) or type(exc).__name__)
            self._diagnostics.append(
                ServerError(
                    code="internal_error",
                    message=f"Program {self._states[key].key}: {type(exc).__name__}: {exc}",
                )
            )
            return Reject(code="program_error", message=str(exc) or type(exc).__name__)
        finally:
            self._executing = previous

    def _remember(self, invocation):
        self._recent[invocation.invocationId] = invocation
        self._recent.move_to_end(invocation.invocationId)
        while len(self._recent) > 50:
            self._recent.popitem(last=False)

    def invoke(self, request):
        if request.invocationId in self._active:
            return self._active[request.invocationId].invocation.model_copy(deep=True)
        if request.invocationId in self._recent:
            return self._recent[request.invocationId].model_copy(deep=True)
        error = None
        key = request.entityKey
        index = self._entity_indices.get(key)
        action = None
        if request.epoch != self.epoch:
            error = Reject(code="session_reset", message="Request belongs to a previous session epoch")
        elif key not in self.catalog.entities:
            error = Reject(code="unknown_entity", message=f"Unknown entity {key}")
        elif index in self._disabled:
            error = Reject(
                code="source_unavailable",
                message="Program disabled after three consecutive errors; reset to recover",
            )
        else:
            try:
                action = self.catalog.action(key, request.action)
            except KeyError:
                error = Reject(code="unknown_action", message=f"Unknown action {request.action}")
        if action is not None:
            try:
                params = self.catalog.validate_params(key, request.action, request.params)
                request = request.model_copy(update={"params": params})
            except ValidationError as exc:
                error = Reject(
                    code="invalid_params",
                    message="Action parameters do not match schema",
                    details={"errors": exc.errors(include_url=False, include_context=False)},
                )
            if error is None and not action["concurrent"]:
                busy = next(
                    (
                        h
                        for h in self._active.values()
                        if h.invocation.entityKey == key
                        and h.invocation.status not in TERMINAL
                        and not self.catalog.action(key, h.action)["concurrent"]
                    ),
                    None,
                )
                if busy:
                    error = Reject(
                        code="busy",
                        message="Entity already has an active action",
                        details={"activeInvocationId": busy.id},
                    )
            if error is None:
                failed = evaluate_conditions(
                    self.catalog.conditions(key, request.action), key, self._values_view
                )
                if failed:
                    error = Reject(
                        code="precondition_failed",
                        message="Action preconditions are not satisfied",
                        details={"failed": failed},
                    )
            if error is None:
                error = self._call(index, "accept", request, self._contexts[index])
        update = InvocationUpdate(
            **request.model_dump(),
            sceneRevision=self.catalog.sceneRevision,
            status="rejected" if error else "accepted",
            error=error,
            endedSimTime=self.sim_time if error else None,
        )
        if error:
            self._remember(update)
        else:
            self._active[request.invocationId] = InvocationHandle(self, update)
        return update.model_copy(deep=True)

    def cancel(self, invocation_id):
        handle = self._active.get(invocation_id)
        if handle is None:
            return self._recent[invocation_id].model_copy(deep=True)
        if not handle.started:
            handle.cancelled()
            return handle.invocation.model_copy(deep=True)
        self._call(handle.entity_index, "cancel", handle, self._contexts[handle.entity_index])
        return handle.invocation.model_copy(deep=True)

    def tick(self, sim_time, dt):
        if sim_time < self.sim_time or dt <= 0:
            raise ValueError("Host time must advance monotonically with positive dt")
        self.sim_time, self.tick_index = sim_time, self.tick_index + 1
        self.fact_due = sim_time + 1e-10 >= self._next_fact
        if self.fact_due:
            self._next_fact = sim_time + 1 / self.fact_hz
        for key in self._order:
            if key in self._disabled:
                continue
            for handle in list(self._active.values()):
                if (
                    handle.entity_index == key
                    and not handle.started
                    and handle.invocation.status not in TERMINAL
                ):
                    handle.started = True
                    handle._update(status="running", startedSimTime=sim_time)
                    self._call(key, "start", handle, self._contexts[key])
            self._call(key, "tick", self._contexts[key], dt)
        for handle in list(self._active.values()):
            action = self.catalog.action(handle.invocation.entityKey, handle.action)
            if (
                handle.invocation.status not in TERMINAL
                and sim_time - (handle.invocation.startedSimTime or handle.accepted_at)
                >= action["timeoutSeconds"]
            ):
                self._call(handle.entity_index, "cancel", handle, self._contexts[handle.entity_index])
                handle._update(
                    force=True,
                    status="failed",
                    endedSimTime=self.sim_time,
                    error=Reject(code="timeout", message="Action exceeded its simulation-time deadline"),
                )
        return self._drain()

    def _drain(self):
        now = self._publication_clock()
        for handle in self._active.values():
            handle.flush_progress(now)
        changes = []
        for key in self._order:
            state = self._states[key]
            outgoing = state.diff.update(state.dirty, now)
            if outgoing:
                changes.append(
                    {
                        "key": state.key,
                        "rev": state.rev,
                        "values": {state.names[prop]: value for prop, value in outgoing.items()},
                    }
                )
            state.dirty.clear()
        result = []
        if changes:
            result.append(
                EntityObservations(
                    sceneRevision=self.catalog.sceneRevision,
                    epoch=self.epoch,
                    simTime=self.sim_time,
                    sampleTimeMs=time.monotonic() * 1000,
                    source=self.source,
                    changes=changes,
                )
            )
        waiting = {
            inv.entityKey
            for inv in self._updates.values()
            if inv.status in TERMINAL and self._states[self._entity_indices[inv.entityKey]].diff.pending
        }
        ready_events = [event for event in self._events if event.entityKey not in waiting]
        ready_updates = {
            key: inv
            for key, inv in self._updates.items()
            if inv.status not in TERMINAL or inv.entityKey not in waiting
        }
        result.extend(ready_events)
        result.extend(ready_updates.values())
        result.extend(self._diagnostics)
        for invocation in ready_updates.values():
            if invocation.status in TERMINAL:
                self._active.pop(invocation.invocationId, None)
                self._remember(invocation)
        self._events = [event for event in self._events if event.entityKey in waiting]
        for key in ready_updates:
            del self._updates[key]
        self._diagnostics.clear()
        return result

    def _prime_diff(self):
        now = self._publication_clock()
        for state in self._states:
            state.diff.reset()
            state.diff.update(
                {prop: value for prop, value in enumerate(state.values) if value is not UNKNOWN},
                now,
            )
            state.dirty.clear()

    def drain_events(self):
        """Publish eligible intake outcomes without advancing a paused host."""
        return self._drain()

    def reset(self, epoch, sim_time=0):
        for handle in list(self._active.values()):
            handle.fail("session_reset", "Session was reset")
        failed = list(self._updates.values())
        for invocation in failed:
            self._remember(invocation)
        self._active.clear()
        self._updates.clear()
        self._events.clear()
        self.epoch, self.sim_time, self.tick_index, self._next_fact = epoch, sim_time, 0, 0.0
        self._disabled.clear()
        for key in self._order:
            self._errors[key] = 0
            self._states[key].reset()
            method = "reset" if hasattr(self._programs[key], "reset") else "init"
            self._call(key, method, self._contexts[key])
        self._prime_diff()
        return [*failed, self.snapshot(), *self._diagnostics]

    def snapshot(self):
        invocations = [h.invocation for h in self._active.values()] + list(self._recent.values())
        return EntitySnapshot(
            sceneRevision=self.catalog.sceneRevision,
            epoch=self.epoch,
            simTime=self.sim_time,
            sampleTimeMs=time.monotonic() * 1000,
            entities=[state.snapshot() for state in self._states],
            invocations=[
                Invocation.model_validate(
                    {k: v for k, v in inv.model_dump().items() if k not in ("type", "sceneRevision")}
                )
                for inv in invocations
            ],
        )

"""Gateway-owned monotonic device target, independent of spatial sessions."""

import asyncio
import json
import time
import uuid
from types import SimpleNamespace

from packages.info.pipeline import ActionPipeline
from packages.info.program import HostCore, Reject
from packages.info.store import StateStore
from packages.live.bindings import REGISTRY, live_catalog
from packages.live.mailbox import DeviceMailbox
from packages.live.mqtt import PahoTransport
from packages.protocol.schema import EntityObservations, EntitySnapshot, InvocationUpdate, SessionOptions


class DeviceHost:
    supports_plans = False

    def __init__(self, bindings, approvals, plans, *, transport_factory=PahoTransport,
                 clock=time.monotonic, approval_timeout=600):
        self.bindings, self.approvals, self.plans = bindings, approvals, plans
        self._clock, self._origin = clock, clock()
        self._transport_factory, self.transport = transport_factory, None
        self._generation, self._next_connect = 0, 0
        self._reconnect = True
        self._task = None
        self.state, self.error = "CREATED", None
        self.session_id = "live-" + uuid.uuid4().hex
        self.catalog = live_catalog(bindings, self.session_id)
        self.manifest = SimpleNamespace(sceneRevision=self.catalog.sceneRevision)
        self.epoch, self.sim_time = 0, 0.0
        self.options = SessionOptions(approval={"high": "require"})
        self.command_lock = asyncio.Lock()
        self.mailboxes = {key: DeviceMailbox(clock=self.now) for key in self.catalog.entities}
        self.host = HostCore(self.catalog, REGISTRY, drivers=self.mailboxes, source="device",
                             availability=self.availability, publication_clock=self._clock)
        self.info = StateStore(self.catalog.sceneRevision)
        self.info.apply(self.host.snapshot())
        self.source = self
        self.pipeline = ActionPipeline(self, self.catalog, self.info, self.broadcast_info,
                                       approval_timeout=approval_timeout)
        self.topics = {}
        for key in self.catalog.entities:
            config = self.catalog.program_config(key)
            for topic in {config.telemetry.topic, config.statusTopic,
                          *(command.ack for command in config.commands.values())} - {None}:
                self.topics.setdefault(topic, []).append(key)

    def now(self):
        return max(0.0, self._clock() - self._origin)

    def availability(self, key):
        mailbox = self.mailboxes.get(key)
        if mailbox is not None and not mailbox.online(self.now()):
            return Reject(code="device_unavailable", message="Device has no fresh connected telemetry")

    async def action_pipeline(self):
        return self.pipeline

    def broadcast_info(self, message):
        if isinstance(message, (EntitySnapshot, EntityObservations)):
            self.info.apply(message)
        self.info.record(message)

    def _messages(self, messages):
        for message in messages:
            if isinstance(message, InvocationUpdate):
                self.pipeline.update(message)
            elif message.type != "information.clock":
                self.broadcast_info(message)

    def _offline(self):
        for mailbox in self.mailboxes.values():
            mailbox.disconnect()
        self.pipeline.fail_active("device_unavailable", "Broker connection was lost")
        for key in self.catalog.entities:
            for invocation in self.host.active_invocations(key):
                self.host.cancel(invocation.invocationId)
        self._messages(self.host.drain_events())
        self._reconnect = True
        self._next_connect = self.now() + 1

    def _receive(self):
        if self.transport is None:
            return
        for receipt in self.transport.ingress.drain():
            if receipt.generation < self._generation:
                continue
            if receipt.kind == "connected":
                self._offline()
                self._generation = receipt.generation
                self._reconnect = False
                for mailbox in self.mailboxes.values():
                    mailbox.connected, mailbox.overflowed = True, False
            elif receipt.kind == "disconnected":
                # Overflow may have discarded this generation's initial connected receipt.
                self._generation = receipt.generation
                self._offline()
            elif receipt.kind == "message" and receipt.generation == self._generation:
                try:
                    payload = json.loads(receipt.payload)
                except (ValueError, UnicodeError):
                    continue
                for key in self.topics.get(receipt.topic, ()):
                    mailbox = self.mailboxes[key]
                    mailbox.receive(receipt.topic, payload, retained=receipt.retained,
                                    received_at=receipt.received_at - self._origin,
                                    sample_time_ms=receipt.received_at * 1000)
                    if mailbox.overflowed:
                        self._offline()
                        return

    def advance(self):
        """Synchronous event-loop step; transport take and publish cannot yield to cancel."""
        self._receive()
        now = self.now()
        if now > self.sim_time:
            self._messages(self.host.tick(now, now - self.sim_time))
            self.sim_time = now
        if self.transport is None:
            return
        for mailbox in self.mailboxes.values():
            for topic, payload in mailbox.take_commands():
                dispatched_at = self.now()
                if not mailbox.connected or not self.transport.publish(topic, payload):
                    self._offline()
                    return
                mailbox.mark_dispatched(payload["requestId"], dispatched_at)

    async def invoke(self, request):
        async with self.command_lock:
            self.advance()
            result = self.host.invoke(request)
            self._messages(self.host.drain_events())
            self.pipeline.update(result)
            return result

    async def cancel(self, invocation_id):
        async with self.command_lock:
            result = self.host.cancel(invocation_id)
            self._messages(self.host.drain_events())
            return result

    async def revoke_before_start(self, invocation_id):
        async with self.command_lock:
            result = self.host.revoke_before_start(invocation_id)
            self._messages(self.host.drain_events())
            return result

    async def _close_transport(self):
        if self.transport is None:
            return
        transport = self.transport
        closing = asyncio.create_task(asyncio.to_thread(transport.close))
        try:
            await asyncio.shield(closing)
        except asyncio.CancelledError:
            await closing
            self.transport = None
            raise
        self.transport = None

    async def _connect(self):
        await self._close_transport()
        self._generation = 0
        self.transport = self._transport_factory(self.bindings.broker, self.topics)
        self._reconnect = False
        self.transport.start()

    async def start(self):
        if self.state != "CREATED":
            return
        await self._connect()
        self.state = "RUNNING"
        self._task = asyncio.create_task(self._run(), name=self.session_id)

    async def _run(self):
        try:
            while self.state == "RUNNING":
                async with self.command_lock:
                    self.advance()
                if self._reconnect and self.now() >= self._next_connect:
                    await self._connect()
                await asyncio.sleep(.05)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.state, self.error = "FAILED", "Live device host failed"
            self._offline()
            await self._close_transport()

    async def close(self):
        self.state = "STOPPING"
        failure = None
        try:
            if self._task is not None:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._task = None
        except BaseException as error:
            failure = error
        for cleanup in (self._offline,
                        lambda: self.pipeline.fail_active("source_unavailable", "Live device host stopped"),
                        self.pipeline.close):
            try:
                cleanup()
            except BaseException as error:
                failure = failure or error
        try:
            await self._close_transport()
        except BaseException as error:
            failure = failure or error
        if failure is not None:
            self.state, self.error = "FAILED", "Live device host cleanup did not complete"
            raise failure
        self.source, self.state = None, "STOPPED"

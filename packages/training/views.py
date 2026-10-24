"""Bounded, read-only Job view fanout; training and its lease stay owned by Jobs."""

import asyncio
import contextlib
import hashlib
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field

from pydantic import ValidationError

from packages.info.plans import PlanError
from packages.training.jobs import TERMINAL
from packages.training.view_contract import VIEW_MESSAGE, VIEW_SCHEMA, CaptureSource, ViewHello
from packages.training.view_descriptor import ViewDescriptors


def attempt_identity(job):
    lease = job.get("leaseId")
    return "attempt-" + hashlib.sha256(f"{job['jobId']}:{lease}".encode()).hexdigest() if lease else None


@dataclass(eq=False)
class Producer:
    superseded: bool = False


@dataclass(eq=False)
class Reader:
    websocket: object
    event: asyncio.Event = field(default_factory=asyncio.Event)
    final: dict | None = None
    view_id: str | None = None
    sent: dict = field(default_factory=dict)
    status: str | None = None
    error: dict | None = None


@dataclass
class ViewState:
    hello: ViewHello
    descriptor: object
    producer: Producer
    manifests: dict
    latest: dict = field(default_factory=dict)
    rates: dict = field(default_factory=dict)
    connected: bool = True


class TrainingViews:
    def __init__(self, manager, previews, *, clock=time.monotonic):
        self.manager, self.clock = manager, clock
        self.descriptors = ViewDescriptors(manager, previews)
        self.states, self.readers, self.errors = {}, {}, {}
        self._pending, self._tracked = {}, set()
        self._producers = {}
        self._cleanup = set()
        self._worker = None

    def envelope(self, job, kind, payload, *, view_id=None, env_index=None):
        value = {
            "schema": VIEW_SCHEMA,
            "type": kind,
            "jobId": job["jobId"],
            "attemptId": attempt_identity(job),
            "viewId": view_id,
            "payload": payload,
        }
        if env_index is not None:
            value["envIndex"] = env_index
        return value

    def _live(self, job):
        if job["status"] != "running":
            raise PlanError("training_view_not_running", "The Job is not running", status=409)

    async def _descriptor(self, job):
        identity = job["jobId"]
        self._tracked.add(identity)
        if identity not in self._pending:
            snapshot = deepcopy(
                {
                    key: job[key]
                    for key in ("jobId", "task", "taskContentHash", "scene", "sceneContentHash", "assets")
                }
            )
            task = asyncio.create_task(asyncio.to_thread(self.descriptors.prepare, snapshot))
            self._pending[identity] = task
            task.add_done_callback(lambda finished: self._pending.pop(identity, None))
        return await asyncio.shield(self._pending[identity])

    async def inputs_hint(self, job):
        try:
            descriptor = await self._descriptor(job)
            return {"attemptId": attempt_identity(job), **descriptor.hint()}
        except PlanError as error:
            return {"attemptId": attempt_identity(job), "error": error.detail}

    def _notify(self, identity):
        for reader in self.readers.get(identity, ()):
            reader.event.set()

    async def push(self, job, raw, producer):
        self._live(job)
        if producer.superseded:
            raise PlanError("training_view_stale_producer", "Producer socket was replaced")
        try:
            message = (
                VIEW_MESSAGE.validate_json(raw) if isinstance(raw, str) else VIEW_MESSAGE.validate_python(raw)
            )
        except ValidationError as error:
            raise PlanError(
                "training_view_invalid_message", "Invalid Job view envelope", status=422
            ) from error
        if message.jobId != job["jobId"] or message.attemptId != attempt_identity(job):
            raise PlanError(
                "training_view_identity_mismatch", "Message does not own this Job dispatch", status=403
            )
        if message.type == "hello":
            return await self._hello(job, message, producer)
        state = self.states.get(job["jobId"])
        if message.type == "error":
            if state is not None and state.producer is not producer:
                raise PlanError("training_view_stale_producer", "Producer socket was replaced")
            if state is not None and message.viewId not in (None, state.hello.viewId):
                raise PlanError(
                    "training_view_identity_mismatch", "Error belongs to another producer incarnation"
                )
            # A producer with no descriptor/map can report Job-level unavailability.
            self.errors[job["jobId"]] = self.envelope(job, "error", message.payload.model_dump(mode="json"))
            if state:
                state.connected = False
            self._notify(job["jobId"])
            return True
        if state is None or state.producer is not producer or message.viewId != state.hello.viewId:
            raise PlanError(
                "training_view_identity_mismatch", "No matching live producer incarnation", status=403
            )
        if message.type != "frame":
            raise PlanError(
                "training_view_producer_message", "Only the gateway owns Manifest and Job completion"
            )
        return self._frame(job, state, message)

    async def _hello(self, job, message, producer):
        value = message.payload
        expected_assets = {item["id"]: item["contentHash"] for item in job["assets"]}
        if (value.task, value.taskContentHash, value.scene, value.sceneContentHash) != (
            job["task"],
            job["taskContentHash"],
            job["scene"],
            job["sceneContentHash"],
        ) or {item.id: item.contentHash for item in value.assets} != expected_assets:
            raise PlanError("training_view_inputs_mismatch", "Producer inputs differ from the approved Job")
        previous_producer = self._producers.get(job["jobId"])
        if previous_producer is not None and previous_producer is not producer:
            previous_producer.superseded = True
        self._producers[job["jobId"]] = producer
        descriptor = await self._descriptor(job)
        self._live(job)  # Geometry I/O did not reserve training ownership or freeze Job state.
        if producer.superseded:
            raise PlanError("training_view_stale_producer", "Producer socket was replaced")
        if value.descriptorDigest != descriptor.digest:
            raise PlanError("training_view_descriptor_mismatch", "Producer geometry descriptor differs")
        value = value.model_copy(update={"capture": CaptureSource(source=descriptor.source)})
        message = message.model_copy(update={"payload": value})
        previous = self.states.get(job["jobId"])
        if previous is not None and previous.hello.viewId == message.viewId:
            if previous.hello.payload != value:
                raise PlanError("training_view_descriptor_changed", "Same-view reconnect changed its binding")
            if previous.producer is not producer:
                previous.producer.superseded = True
            previous.producer, previous.connected = producer, True
        else:
            if previous is not None and previous.producer is not producer:
                previous.producer.superseded = True
            manifests = {
                index: descriptor.for_environment(
                    job["jobId"], message.viewId, index, value.source.model_dump(mode="json")
                )
                for index in value.environments
            }
            self.states[job["jobId"]] = ViewState(message, descriptor, producer, manifests)
        self.errors.pop(job["jobId"], None)
        self._notify(job["jobId"])
        return True

    def _frame(self, job, state, message):
        index, payload = message.envIndex, message.payload
        if index not in state.manifests:
            raise PlanError("training_view_environment_mismatch", "Environment is not selected")
        manifest = state.manifests[index]
        expected = {node["streamId"] for node in manifest["nodes"] if node["kind"] == "body"}
        if (
            payload.sceneRevision != manifest["sceneRevision"]
            or {row[0] for row in payload.poses} != expected
        ):
            raise PlanError(
                "training_view_mapping_unavailable", "Sample does not cover the full canonical body table"
            )
        previous = state.latest.get(index)
        if previous is not None:
            old = previous["payload"]
            current = payload.model_dump(mode="json")
            if payload.seq == old["seq"] and {
                key: value for key, value in current.items() if key != "type"
            } == {key: value for key, value in old.items() if key != "type"}:
                return True  # Idempotent latest-snapshot replay after producer reconnect.
            if payload.seq <= old["seq"] or payload.epoch < old["epoch"] or payload.simTime < old["simTime"]:
                raise PlanError(
                    "training_view_stale_frame", "Sample sequence, epoch or physics clock regressed"
                )
            if payload.epoch != old["epoch"] and payload.type != "stream.snapshot":
                raise PlanError("training_view_snapshot_required", "Reset requires a complete snapshot")
        elif payload.type != "stream.snapshot":
            raise PlanError(
                "training_view_snapshot_required", "First sample must be a real complete snapshot"
            )
        rate = state.rates.setdefault(index, deque())
        now = self.clock()
        while rate and rate[0] <= now - 1:
            rate.popleft()
        if len(rate) >= state.hello.payload.maxRateHz:
            return False
        rate.append(now)
        state.latest[index] = message.model_dump(mode="json")
        self._notify(job["jobId"])
        return True

    def disconnect(self, identity, producer):
        state = self.states.get(identity)
        if state is not None and state.producer is producer:
            state.connected = False
            self._notify(identity)

    async def send(self, websocket, message):
        await asyncio.wait_for(websocket.send_json(message), timeout=2)

    async def stream(self, job, reader):
        identity = job["jobId"]
        readers = self.readers.setdefault(identity, set())
        readers.add(reader)
        self._tracked.add(identity)
        reader.event.set()
        try:
            while True:
                await reader.event.wait()
                reader.event.clear()
                if reader.final is not None or job["status"] in TERMINAL:
                    final = reader.final or self.envelope(
                        job,
                        "ended",
                        {"status": job["status"], "reason": job["reason"]},
                        view_id=reader.view_id,
                    )
                    await self.send(reader.websocket, final)
                    await reader.websocket.close(code=1000)
                    return
                error = self.errors.get(identity)
                if error is not None:
                    if reader.error != error:
                        await self.send(reader.websocket, error)
                        reader.error = error
                    continue
                reader.error = None
                state = self.states.get(identity)
                if state is None:
                    if reader.status != "waiting":
                        await self.send(reader.websocket, self.envelope(job, "status", {"state": "waiting"}))
                        reader.status = "waiting"
                    continue

                def obsolete():
                    changed = job["status"] in TERMINAL or self.states.get(identity) is not state
                    if changed:
                        reader.event.set()
                    return changed

                if reader.view_id != state.hello.viewId:
                    reader.view_id = state.hello.viewId
                    reader.sent.clear()
                    await self.send(reader.websocket, state.hello.model_dump(mode="json"))
                    for index, manifest in state.manifests.items():
                        if obsolete():
                            break
                        await self.send(
                            reader.websocket,
                            self.envelope(job, "manifest", manifest, view_id=reader.view_id, env_index=index),
                        )
                if obsolete():
                    continue
                status = (
                    "live" if state.connected and state.latest else "waiting" if state.connected else "stale"
                )
                if reader.status != status:
                    await self.send(
                        reader.websocket,
                        self.envelope(job, "status", {"state": status}, view_id=reader.view_id),
                    )
                    reader.status = status
                if obsolete() or not state.connected:
                    continue
                for index, sample in tuple(state.latest.items()):
                    if obsolete() or not state.connected:
                        break
                    if reader.sent.get(index, -1) >= sample["payload"]["seq"]:
                        continue
                    outgoing = deepcopy(sample)
                    if index not in reader.sent:
                        outgoing["payload"]["type"] = "stream.snapshot"
                    await self.send(reader.websocket, outgoing)
                    reader.sent[index] = sample["payload"]["seq"]
        finally:
            readers.discard(reader)
            if not readers:
                self.readers.pop(identity, None)

    async def start(self):
        self._worker = asyncio.create_task(self._watch(), name="training-view-lifecycle")

    async def _watch(self):
        while True:
            for identity in tuple(self._tracked):
                job = self.manager.jobs.get(identity)
                if job["status"] not in TERMINAL:
                    continue
                state = self.states.pop(identity, None)
                view_id = state.hello.viewId if state else None
                if state:
                    state.latest.clear()
                    state.producer.superseded = True
                for reader in self.readers.get(identity, ()):
                    reader.final = self.envelope(
                        job, "ended", {"status": job["status"], "reason": job["reason"]}, view_id=view_id
                    )
                    reader.event.set()
                self.errors.pop(identity, None)
                self._producers.pop(identity, None)
                self.descriptors.mark_terminal(identity)
                cleanup = asyncio.create_task(asyncio.to_thread(self.descriptors.release, identity))
                self._cleanup.add(cleanup)
                cleanup.add_done_callback(self._cleanup.discard)
                self._tracked.discard(identity)
            await asyncio.sleep(0.1)

    async def close(self):
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        for readers in tuple(self.readers.values()):
            for reader in tuple(readers):
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(reader.websocket.close(code=1001), 2)
        if self._pending:
            await asyncio.gather(*self._pending.values(), return_exceptions=True)
        if self._cleanup:
            await asyncio.gather(*self._cleanup, return_exceptions=True)
        self.states.clear()
        self.errors.clear()
        self._producers.clear()

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import HTTPException

from apps.api.app.recordings import RecordingRepository, freeze_manifest
from apps.api.app.settings import Settings
from apps.api.app.sources.base import SpatialSource
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.api.app.stream_broadcaster import ClientConnection
from apps.api.app.structured_logging import log_event
from packages.assets.registry import AssetRegistry
from packages.info.approvals import ApprovalStore
from packages.info.invocations import TERMINAL, InvocationTable
from packages.info.pipeline import ActionPipeline
from packages.info.plans import PlanStore
from packages.info.store import StateStore
from packages.library.scenes import SceneLibrary
from packages.protocol.internal import SessionOptions
from packages.protocol.json import encode
from packages.protocol.math import compose_pose
from packages.protocol.schema import (
    Catalog,
    Cause,
    Command,
    CommandAck,
    EntityEvent,
    EntityObservations,
    EntitySnapshot,
    Heartbeat,
    InvocationUpdate,
    SceneManifest,
    SceneReplaced,
    ServerError,
    SessionState,
    Subscribe,
    SubscriptionAck,
    TransformFrame,
    validate_command_args,
)
from packages.transport import FrameCodec


def default_source_factory(kind: str, session_id: str) -> SpatialSource:
    if kind == "synthetic":
        return SyntheticSource(session_id)
    if kind == "newton":
        # The adapter owns process startup. Never import Newton into the gateway.
        try:
            from apps.api.app.sources.newton_source import NewtonSource
        except ImportError as exc:
            raise HTTPException(
                503, {"code": "source_unavailable", "message": "Newton adapter is not installed"}
            ) from exc
        return NewtonSource(session_id)
    raise HTTPException(422, {"code": "unknown_source"})


class Session:
    def __init__(
        self,
        scene_id: str,
        kind: str,
        options: SessionOptions,
        settings: Settings,
        factory,
        recordings,
        approvals,
        plans,
        scene_version=None,
    ):
        self.session_id = "session-" + uuid.uuid4().hex[:12]
        self.scene_id, self.kind = scene_id, kind
        self.scene_version = scene_version
        self.options, self.settings, self.factory = options, settings, factory
        self.approvals = approvals
        self.plans = plans
        self.source: SpatialSource | None = None
        self._starting_source: SpatialSource | None = None
        self.manifest: SceneManifest | None = None
        self.state = "CREATED"
        self.epoch, self.seq, self.sim_time = 0, 0, 0.0
        self._seq_high_water = 0
        self.actual_rtf = 0.0
        self._seq_offset = 0
        self._revision_number = 0
        self._frame: TransformFrame | None = None
        self._complete_frame: TransformFrame | None = None
        self._last_published_seq = -1
        self._tasks: list[asyncio.Task] = []
        self._heartbeat_task: asyncio.Task | None = None
        self.clients: set[ClientConnection] = set()
        self.command_lock = asyncio.Lock()
        self._acks: OrderedDict[str, CommandAck] = OrderedDict()
        self._rtf_samples = deque(maxlen=60)
        self.published_frames = 0
        self.payload_bytes = 0
        self.error: dict | None = None
        self._auto_paused = False
        self._body_stream_ids = frozenset()
        self.catalog = None
        self.info = None
        self.pipeline = None
        self.recordings = recordings
        self.recording = None
        self._recording_revision_ready = False

    async def start(self):
        previous_manifest = self.manifest
        recording_was_ready = self._recording_revision_ready
        self._recording_revision_ready = False
        self.state = "LOADING"
        self._log_event("session.lifecycle", state=self.state)
        if self.manifest:
            self.broadcast(self._lifecycle_state())
        candidate = None
        try:
            candidate = self.factory(self.kind, self.session_id)
            self._starting_source = candidate
            if self.scene_version and (configure := getattr(candidate, "configure_scene", None)):
                configure(self.scene_version)
            started = await candidate.start(
                self.scene_version.builder if self.scene_version else self.scene_id, self.options
            )
            manifest, catalog = started if isinstance(started, tuple) else (started, None)
            if manifest.sessionId != self.session_id:
                raise ValueError("source manifest sessionId does not match the session")
            manifest = manifest.model_copy(update={"revisionNumericId": self._revision_number + 1})
            body_ids = frozenset(node.streamId for node in manifest.nodes if node.kind == "body")
            initial_frame = await candidate.snapshot()
            if not self._complete_for(initial_frame, manifest, body_ids):
                raise ValueError("Initial source snapshot is not a complete Manifest sample")
            # Prepare the initial policy before exposing the candidate revision.
            # Creation is exclusive; reload already holds command_lock.
            policy_change = await self._observer_policy_change(candidate, "RUNNING", False)
            self.source = candidate
            self._revision_number += 1
            self.manifest = manifest
            self.catalog = catalog or Catalog(sceneRevision=manifest.sceneRevision)
            if self.info is None:
                self.info = StateStore(manifest.sceneRevision)
            else:
                self.info.configure(manifest.sceneRevision)
            previous_table = self.pipeline.table if self.pipeline else None
            if self.pipeline:
                self.pipeline.close()
            self.pipeline = ActionPipeline(
                self,
                self.catalog,
                self.info,
                self.broadcast_info,
                approval_timeout=self.settings.approval_timeout_seconds,
            )
            if previous_table:
                self.pipeline.table = previous_table
            else:
                self.pipeline.table = InvocationTable(evicted_lookup=self._recorded_invocation)
            self._body_stream_ids = body_ids
            self._frame = self._complete_frame = None
            self.state, self.error = "RUNNING", None
            self._auto_paused = False
            self._cache_frame(self._source_frame(initial_frame))
            if policy_change:
                self._apply_observer_policy_change(*policy_change, publish=False)
            self._last_published_seq = self.seq
            if callable(getattr(candidate, "entity_snapshot", None)):
                self.apply_info(candidate.entity_snapshot())
            await self._record_revision()
            self._log_event("session.lifecycle", state=self.state)
            self._tasks = [
                asyncio.create_task(self._monitor(), name="session-frames"),
                asyncio.create_task(self._events(), name="session-events"),
            ]
            if self._heartbeat_task is None:
                self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="session-heartbeat")
        except BaseException as exc:
            with contextlib.suppress(Exception):
                if candidate:
                    await candidate.close()
            # A failed candidate must never be read as the previous valid sample.
            self.source = None
            if self.manifest is previous_manifest:
                self._recording_revision_ready = recording_was_ready
            if isinstance(exc, asyncio.CancelledError):
                self.state = "STOPPED"
            elif self.state != "FAILED":
                self.fail(str(exc))
            raise
        finally:
            self._starting_source = None

    async def open_viewer(self):
        if self.kind != "newton":
            raise HTTPException(400, {
                "code": "viewer_unsupported", "message": "Native Viewer requires a Newton session"
            })
        async with self.command_lock:
            if self.state not in ("RUNNING", "PAUSED") or self.source is None:
                raise HTTPException(409, {
                    "code": "invalid_state", "message": "Native Viewer requires a running or paused session"
                })
            opener = getattr(self.source, "open_viewer", None)
            if not callable(opener):
                raise HTTPException(503, {
                    "code": "viewer_unavailable", "message": "This Newton adapter has no Native Viewer"
                })
            try:
                return await opener()
            except Exception as exc:
                raise HTTPException(503, {"code": "viewer_unavailable", "message": str(exc)}) from exc

    async def action_pipeline(self):
        """Wait for revision capture, then select the pipeline for immediate intake.

        Callers immediately invoke/cancel/approve after this returns. Fresh intake
        reserves work before its first suspending host call; that call remains
        outside the lifecycle lock so reload can fail reserved work promptly.
        """
        async with self.command_lock:
            return self.pipeline

    async def _record_revision(self):
        if self.recording is None:
            self.recording = self.recordings.create(
                self.session_id, self.scene_id, self.scene_version, seed=self.options.seed
            )
        if self.recording is None:
            return
        try:
            manifest, assets = await freeze_manifest(
                self.manifest, self.source, self.recordings.asset_registry
            )
            self.recording.add_revision(
                manifest,
                self.catalog.model_dump(mode="json"),
                self._encode_frame(self._complete_frame.model_copy(update={"type": "stream.snapshot"})),
                encode(self.info_snapshot()),
                state=encode(self._lifecycle_state()),
                assets=assets,
            )
            self._recording_revision_ready = True
        except Exception as exc:
            self.recording.stop_incomplete(f"Scene capture failed: {exc}")
            self._log_event("recording.failed", message=str(exc), level=logging.ERROR)

    async def _recorded_invocation(self, invocation_id):
        value = await self.recording.lookup_invocation(invocation_id) if self.recording else None
        # Eviction only archives terminal results. A D04 prefix may contain an
        # older accepted/running update whose eventual terminal was never written.
        return value if value is not None and value.status in TERMINAL else None

    def _record_json(self, message, payload):
        if self.recording:
            self.recording.append_json(
                payload,
                getattr(message, "sampleTimeMs", time.monotonic() * 1000),
                invocation_id=message.invocationId if isinstance(message, InvocationUpdate) else None,
            )

    def latest_frame(self) -> TransformFrame | None:
        try:
            frame = (
                self.source.latest_frame()
                if self.source and self.state not in ("FAILED", "STOPPED")
                else None
            )
        except Exception:
            frame = None
        if frame is not None:
            frame = (
                frame.model_copy(update={"seq": frame.seq + self._seq_offset}) if self._seq_offset else frame
            )
            if self.manifest and (
                frame.sessionId != self.session_id or frame.sceneRevision != self.manifest.sceneRevision
            ):
                frame = None
        cached = self._frame
        if cached and self.manifest and cached.sceneRevision != self.manifest.sceneRevision:
            cached = None
        selected = cached if frame is None or (cached is not None and frame.seq < cached.seq) else frame
        if selected is not None:
            self._seq_high_water = max(self._seq_high_water, selected.seq)
        return selected

    def _cache_frame(self, frame):
        if frame is not None:
            self._seq_high_water = max(self._seq_high_water, frame.seq)
            if self._is_complete(frame) and (
                self._complete_frame is None or frame.seq >= self._complete_frame.seq
            ):
                self._complete_frame = frame
        if frame is not None and frame.seq >= self.seq:
            self._frame = frame
            self.seq, self.epoch, self.sim_time = frame.seq, frame.epoch, frame.simTime

    def _source_frame(self, frame):
        return frame.model_copy(update={"seq": frame.seq + self._seq_offset})

    def _is_complete(self, frame):
        return self.manifest is not None and self._complete_for(frame, self.manifest, self._body_stream_ids)

    @staticmethod
    def _complete_for(frame, manifest, ids):
        return (
            (frame.sessionId, frame.sceneRevision) == (manifest.sessionId, manifest.sceneRevision)
            and len(frame.poses) == len(ids)
            and {row[0] for row in frame.poses} == ids
        )

    async def snapshot(self) -> TransformFrame:
        if self.state in ("FAILED", "STOPPED"):
            frame = self._complete_frame
        else:
            # HTTP reads must remain independent of a delayed command reply.
            source, manifest, offset = self.source, self.manifest, self._seq_offset
            if source is None or manifest is None:
                raise HTTPException(503, {"code": "session_not_ready"})
            try:
                frame = await source.snapshot()
            except TimeoutError as exc:
                raise HTTPException(504, {"code": "query_timeout"}) from exc
            except Exception as exc:
                raise HTTPException(503, {"code": "source_unavailable", "message": str(exc)}) from exc
            if source is not self.source or manifest is not self.manifest or offset != self._seq_offset:
                raise HTTPException(409, {"code": "revision_mismatch"})
            frame = self._source_frame(frame)
            if not self._is_complete(frame):
                self.fail("Source snapshot is not a complete sample of the current Manifest")
                raise HTTPException(503, {"code": "source_unavailable"})
            self._cache_frame(frame)
            # A read-only snapshot is a recovery sample, not a reset broadcast.
            self._last_published_seq = max(self._last_published_seq, frame.seq)
        if frame is None or not self._is_complete(frame):
            raise HTTPException(503, {"code": "session_not_ready"})
        return frame.model_copy(update={"type": "stream.snapshot"})

    def scene(self) -> SceneManifest:
        if self.manifest is None:
            raise HTTPException(503, {"code": "session_not_ready"})
        return self.manifest.model_copy(
            update={
                "epoch": self.epoch,
                "source": self.manifest.source.model_copy(
                    update={"realtimeFactor": self.options.realtimeFactor}
                ),
            }
        )

    def summary(self):
        frame = self.latest_frame()
        self._cache_frame(frame)
        manifest = self.manifest
        return {
            "sessionId": self.session_id,
            "sceneId": self.scene_id,
            "sceneVersion": self.scene_version.id if self.scene_version else f"{self.scene_id}@1",
            "sceneContentHash": self.scene_version.content_hash if self.scene_version else None,
            "source": self.kind,
            "state": self.state,
            "sceneRevision": manifest.sceneRevision if manifest else None,
            "revisionNumericId": self._revision_number,
            "epoch": self.epoch,
            "seq": self.seq,
            "simTime": self.sim_time,
            "realtimeFactor": self.options.realtimeFactor,
            "actualRtf": self.actual_rtf if self.state == "RUNNING" else 0.0,
            "solver": manifest.source.solver if manifest else None,
            "counts": {
                "nodes": len(manifest.nodes),
                "bodies": sum(n.kind == "body" for n in manifest.nodes),
                "shapes": manifest.source.shapeCount,
                "sites": len(manifest.sites),
                "joints": len(manifest.joints),
                "warnings": len(manifest.warnings),
            }
            if manifest
            else {},
            "error": self.error,
            "continueWithoutObservers": self.options.continueWithoutObservers,
            "seed": self.options.seed,
            "autoPaused": self._auto_paused,
        }

    async def _monitor(self):
        # Poll faster than publication so coarse timer phases do not merge adjacent samples.
        period = min(0.01, 1 / self.options.basePublishHz)
        deadline = time.monotonic()
        while True:
            deadline += period
            await asyncio.sleep(max(0, deadline - time.monotonic()))
            now = time.monotonic()
            # A late poll consumes the latest frame once, without a catch-up burst.
            deadline += max(0, int((now - deadline) / period)) * period
            if self.command_lock.locked() or self.state != "RUNNING":
                continue
            frame = self.latest_frame()
            if frame is not None and frame.seq > self._last_published_seq:
                self._cache_frame(frame)
                now = time.monotonic()
                self._rtf_samples.append((now, frame.simTime, frame.epoch))
                same_epoch = [x for x in self._rtf_samples if x[2] == frame.epoch and now - x[0] <= 2]
                if len(same_epoch) > 1 and now > same_epoch[0][0]:
                    self.actual_rtf = max(0, (frame.simTime - same_epoch[0][1]) / (now - same_epoch[0][0]))
                self.broadcast_frame(frame, reliable=False)

    async def _events(self):
        try:
            async for event in self.source.events():
                if isinstance(event, (EntitySnapshot, EntityObservations, EntityEvent, InvocationUpdate)):
                    async with self.command_lock:
                        self.apply_info(event)
                    continue
                if not isinstance(event, (ServerError, SceneReplaced)):
                    if (
                        event.sessionId != self.session_id
                        or event.sceneRevision != self.manifest.sceneRevision
                    ):
                        continue
                if isinstance(event, SessionState):
                    event = event.model_copy(update={"seq": event.seq + self._seq_offset})
                    self._apply_state(event)
                elif isinstance(event, SceneReplaced):
                    manifest = getattr(self.source, "manifest", None)
                    if (
                        not isinstance(manifest, SceneManifest)
                        or manifest.sceneRevision != event.sceneRevision
                    ):
                        raise ValueError("Source replaced a scene without exposing its new Manifest")
                    self.manifest = manifest
                    self._body_stream_ids = frozenset(
                        node.streamId for node in manifest.nodes if node.kind == "body"
                    )
                    self._revision_number = event.revisionNumericId
                    self._cache_frame(self.latest_frame())
                    for client in self.clients:
                        client.subscribed = False
                        client.latest = None
                self.broadcast(event)
            if self.state in ("RUNNING", "PAUSED"):
                self.fail("Source event stream ended")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.fail(str(exc))

    def fail(self, message):
        message = str(message) or "Source unavailable"
        self._cache_frame(self.latest_frame())
        self.error = {"code": "source_unavailable", "message": message}
        self.state, self.actual_rtf = "FAILED", 0
        if self.pipeline:
            self.pipeline.fail_active("source_unavailable", message)
        self.plans.invalidate(self, "source_unavailable")
        for client in self.clients:
            client.latest = None
        self.broadcast(ServerError(code="source_unavailable", message=message))
        # Initial creation may fail before there is a valid revision to publish.
        if self.manifest:
            self.broadcast(self._lifecycle_state("error", message))

    def _lifecycle_state(self, kind="lifecycle", message=None):
        return SessionState(
            sessionId=self.session_id,
            state=self.state,
            sceneRevision=self.manifest.sceneRevision,
            epoch=self.epoch,
            seq=self.seq,
            simTime=self.sim_time,
            realtimeFactor=self.options.realtimeFactor,
            actualRtf=self.actual_rtf if self.state == "RUNNING" else 0,
            cause={
                "kind": kind,
                **({"code": self.error["code"]} if kind == "error" and self.error else {}),
                **({"message": message} if message else {}),
            },
        )

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)
            summary = self.summary()
            self.broadcast(
                Heartbeat(
                    state=self.state,
                    simTime=summary["simTime"],
                    lastSeq=self.seq,
                    actualRtf=summary["actualRtf"],
                    serverTimeMs=time.monotonic() * 1000,
                )
            )

    def info_snapshot(self):
        return EntitySnapshot(
            sceneRevision=self.manifest.sceneRevision,
            epoch=self.info.epoch,
            simTime=self.sim_time,
            sampleTimeMs=time.monotonic() * 1000,
            entities=list(self.info.entities.values()),
            invocations=self.pipeline.table.snapshot(),
        )

    def broadcast_info(self, message):
        payload = self.info.record(message)
        if self._recording_revision_ready:
            self._record_json(message, payload)
        for client in tuple(self.clients):
            if client.subscribed and client.info:
                client.enqueue_reliable(payload)

    def apply_info(self, message):
        if message.sceneRevision != self.manifest.sceneRevision:
            return
        if isinstance(message, InvocationUpdate):
            self.pipeline.update(message)
            return
        if isinstance(message, EntitySnapshot):
            if message.epoch > self.info.epoch:
                self.pipeline.fail_active("session_reset", "Session was reset")
                self.plans.invalidate(self, "session_reset")
            if not self.info.apply(message):
                return
            for invocation in message.invocations:
                if invocation.epoch == message.epoch or invocation.status in (
                    "rejected",
                    "succeeded",
                    "failed",
                    "cancelled",
                ):
                    self.pipeline.table.apply(invocation)
            message = self.info_snapshot()
        elif isinstance(message, EntityObservations):
            if not self.info.apply(message):
                return
        elif message.epoch != self.info.epoch:
            return
        self.broadcast_info(message)

    def broadcast(self, message):
        if isinstance(message, SessionState):
            self._log_event(
                "session.state",
                state=message.state,
                cause=message.cause.kind,
                requestId=message.cause.requestId,
                command=message.cause.command,
            )
        elif isinstance(message, ServerError):
            self._log_event(
                "source.error",
                code=message.code,
                message=message.message,
                requestId=message.requestId,
                level=logging.ERROR,
            )
        payload = encode(message)
        if isinstance(message, (SessionState, SceneReplaced, ServerError)):
            self._record_json(message, payload)
        for client in tuple(self.clients):
            client.enqueue_reliable(payload)

    def broadcast_frame(self, frame: TransformFrame, reliable: bool):
        if reliable:
            self._log_event(frame.type)
        self._last_published_seq = max(self._last_published_seq, frame.seq)
        self.published_frames += 1
        payload = self._encode_frame(frame)
        if self.recording and self._recording_revision_ready:
            self.recording.append_frame(payload, frame.sampleTimeMs, reliable=reliable)
        self.payload_bytes += len(payload)
        for client in tuple(self.clients):
            if not client.subscribed:
                continue
            if reliable:
                client.enqueue_reliable(payload, seq=frame.seq, force=True)
            else:
                client.offer_frame(payload, frame.seq)

    def _encode_frame(self, frame: TransformFrame) -> bytes:
        # Source-local IPC revision/seq differ after a gateway-owned reload.
        return FrameCodec(
            self.session_id, self.manifest.sceneRevision, self.manifest.revisionNumericId
        ).encode(frame)

    def _apply_state(self, state: SessionState):
        if state.state in ("FAILED", "STOPPED") and self.pipeline:
            self.pipeline.fail_active("source_unavailable", state.cause.message or "Device host unavailable")
            self.plans.invalidate(self, "source_unavailable")
        self._seq_high_water = max(self._seq_high_water, state.seq)
        if (self.state, self.epoch, self.options.realtimeFactor) != (
            state.state,
            state.epoch,
            state.realtimeFactor,
        ):
            self._rtf_samples.clear()
        self.state, self.epoch, self.seq, self.sim_time = state.state, state.epoch, state.seq, state.simTime
        self.options.realtimeFactor = state.realtimeFactor
        if state.state != "RUNNING":
            self.actual_rtf = 0
            for client in self.clients:
                client.latest = None

    async def execute(self, client: ClientConnection, command: Command):
        async with self.command_lock:
            reason = None
            if client.role != "operator":
                reason = "unauthorized"
            elif command.requestId in self._acks:
                client.enqueue_reliable(encode(self._acks[command.requestId]))
                self._log_event("command.replayed", requestId=command.requestId, command=command.command)
                return
            elif self.state not in ("RUNNING", "PAUSED"):
                reason = "source_unavailable"
            else:
                try:
                    validate_command_args(command)
                except ValueError as exc:
                    reason = "unknown_command" if str(exc) == "unknown_command" else "invalid_args"
            if reason:
                ack = CommandAck(
                    requestId=command.requestId,
                    status="rejected",
                    reason=reason,
                    simTime=self.sim_time,
                    epoch=self.epoch,
                )
            else:
                try:
                    result = await self.source.execute(command)
                except Exception as exc:
                    self.fail(str(exc))
                    result = None
                if result is None:
                    ack = CommandAck(
                        requestId=command.requestId,
                        status="rejected",
                        reason="source_unavailable",
                        simTime=self.sim_time,
                        epoch=self.epoch,
                    )
                else:
                    ack = result.ack
                    # This synchronous section cannot interleave with periodic fanout.
                    if result.frame is not None:
                        frame = result.frame.model_copy(update={"seq": result.frame.seq + self._seq_offset})
                        self._cache_frame(frame)
                        self.broadcast_frame(frame, reliable=True)
                    self._log_event(
                        "command.ack",
                        requestId=ack.requestId,
                        command=command.command,
                        status=ack.status,
                        code=ack.reason,
                    )
                    client.enqueue_reliable(encode(ack))
                    if result.state is not None:
                        state = result.state.model_copy(update={"seq": result.state.seq + self._seq_offset})
                        self._apply_state(state)
                        self.broadcast(state)
                    if ack.status == "applied" and command.command == "reset":
                        self.pipeline.fail_active("session_reset", "Session was reset")
                        snapshot = (
                            self.source.entity_snapshot()
                            if callable(getattr(self.source, "entity_snapshot", None))
                            else EntitySnapshot(sceneRevision=self.manifest.sceneRevision, epoch=self.epoch)
                        )
                        self.apply_info(snapshot)
                    if ack.status == "applied" and command.command in ("pause", "resume"):
                        self._auto_paused = False
                    if client.role == "operator":
                        self._remember(ack)
                    await self._sync_observer_policy_locked()
                    return
            self._log_event(
                "command.ack",
                requestId=ack.requestId,
                command=command.command,
                status=ack.status,
                code=ack.reason,
            )
            client.enqueue_reliable(encode(ack))
            if client.role == "operator":
                self._remember(ack)

    def _log_event(self, event, **fields):
        metrics = {}
        if self.source and callable(getattr(self.source, "stats", None)):
            with contextlib.suppress(Exception):
                metrics = self.source.stats()
        log_event(
            event,
            session_id=self.session_id,
            scene_revision=self.manifest.sceneRevision if self.manifest else None,
            epoch=self.epoch,
            seq=self.seq,
            source=self.kind,
            runnerPID=metrics.get("runnerPID"),
            **fields,
        )

    def _remember(self, ack):
        self._acks[ack.requestId] = ack
        if len(self._acks) > 256:
            self._acks.popitem(last=False)

    def subscribe(self, client: ClientConnection, request: Subscribe, *, frame: TransformFrame):
        client.granted_rate_hz = min(max(request.rateHz, 1), self.options.basePublishHz)
        client.info = request.info
        client.subscribed = True
        # Preserve earlier command frame -> ack -> state before this ack -> snapshot.
        client.latest = None
        client.enqueue_reliable(
            encode(
                SubscriptionAck(
                    grantedRateHz=client.granted_rate_hz,
                    info=request.info,
                    requestId=request.requestId,
                )
            )
        )
        snapshot = frame.model_copy(update={"type": "stream.snapshot"})
        # Re-subscription deliberately permits a complete snapshot at the same seq.
        client.enqueue_reliable(self._encode_frame(snapshot), seq=snapshot.seq, force=True)
        if client.info:
            client.enqueue_reliable(encode(self.info_snapshot()))
            for plan in self.plans.snapshots(self):
                client.enqueue_reliable(encode(plan))

    async def observer_subscribe(self, client: ClientConnection, request: Subscribe):
        async with self.command_lock:
            if request.sceneRevision != self.manifest.sceneRevision:
                client.enqueue_reliable(
                    encode(
                        ServerError(
                            code="revision_mismatch",
                            message="Reload the Manifest before subscribing",
                            requestId=request.requestId,
                        )
                    )
                )
                return
            if (
                self.state in ("FAILED", "STOPPED")
                and self._complete_frame is not None
                and self._is_complete(self._complete_frame)
            ):
                self.subscribe(client, request, frame=self._complete_frame)
                if self.error:
                    client.enqueue_reliable(encode(ServerError(**self.error)))
                return
            if self.state not in ("RUNNING", "PAUSED") or self.source is None:
                client.enqueue_reliable(
                    encode(
                        ServerError(
                            code="source_unavailable",
                            message="Source unavailable",
                            requestId=request.requestId,
                        )
                    )
                )
                return
            try:
                frame = self._source_frame(await self.source.snapshot())
                if not self._is_complete(frame):
                    raise ValueError("Source snapshot is not a complete Manifest sample")
            except TimeoutError:
                client.enqueue_reliable(
                    encode(
                        ServerError(
                            code="session_not_ready",
                            message="Snapshot query timed out; retry the subscription",
                            requestId=request.requestId,
                        )
                    )
                )
                return
            except Exception as exc:
                self.fail(str(exc))
                return
            self._cache_frame(frame)
            self._last_published_seq = max(self._last_published_seq, frame.seq)
            self.subscribe(client, request, frame=frame)
            await self._sync_observer_policy_locked()

    async def remove_client(self, client: ClientConnection):
        async with self.command_lock:
            self.clients.discard(client)
            client.subscribed = False
            await self._sync_observer_policy_locked()

    async def _observer_policy_change(self, source, state, auto_paused):
        if self.options.continueWithoutObservers or state not in ("RUNNING", "PAUSED"):
            return
        observing = any(client.subscribed and not client.closed.is_set() for client in self.clients)
        if not observing and state == "RUNNING":
            kind, auto_paused = "pause", True
        elif observing and auto_paused and state == "PAUSED":
            kind, auto_paused = "resume", False
        else:
            return
        result = await source.execute(Command(requestId=f"observer-{uuid.uuid4().hex}", command=kind))
        if result.ack.status != "applied" or result.state is None:
            raise RuntimeError(f"Source rejected observer-policy {kind}")
        return result, auto_paused

    def _apply_observer_policy_change(self, result, auto_paused, *, publish=True):
        if result.frame is not None:
            frame = result.frame.model_copy(update={"seq": result.frame.seq + self._seq_offset})
            self._cache_frame(frame)
            if publish:
                self.broadcast_frame(frame, reliable=True)
        state = result.state.model_copy(
            update={
                "seq": result.state.seq + self._seq_offset,
                "cause": Cause(
                    kind="lifecycle",
                    message="No subscribers" if auto_paused else "First subscriber returned",
                ),
            }
        )
        self._apply_state(state)
        self._auto_paused = auto_paused
        if publish:
            self.broadcast(state)

    async def _sync_observer_policy_locked(self):
        try:
            policy_change = await self._observer_policy_change(self.source, self.state, self._auto_paused)
            if policy_change:
                self._apply_observer_policy_change(*policy_change)
        except Exception as exc:
            self.fail(str(exc))

    async def pose(self, key: str):
        # Pose is a read model: observing published bytes must not request a
        # fresh Runner capture or merge partial rows into a complete sample.
        latest = self.latest_frame()
        if latest is not None and self._is_complete(latest):
            self._cache_frame(latest)
        frame = self._complete_frame
        if frame is None or not self._is_complete(frame):
            raise HTTPException(503, {"code": "session_not_ready"})
        poses = {row[0]: {"p": list(row[1:4]), "q": list(row[4:8])} for row in frame.poses}
        nodes = {node.entityKey: node for node in self.manifest.nodes}
        node = nodes.get(key)
        if node and node.kind == "body":
            pose = poses.get(node.streamId)
        elif node and node.kind == "static":
            pose = node.initialPose.model_dump(mode="json")
        else:
            site = next((s for s in self.manifest.sites if s.entityKey == key), None)
            if site is None:
                raise HTTPException(404, {"code": "unknown_entity"})
            body_pose = (
                I_WORLD if site.parentBodyKey is None else poses.get(nodes[site.parentBodyKey].streamId)
            )
            pose = compose_pose(body_pose, site.localPose.model_dump()) if body_pose else None
        if pose is None:
            raise HTTPException(503, {"code": "session_not_ready"})
        return {
            "entityKey": key,
            **pose,
            "simTime": frame.simTime,
            "epoch": frame.epoch,
            "seq": frame.seq,
            "sceneRevision": frame.sceneRevision,
            "staleMs": max(0, time.monotonic() * 1000 - frame.sampleTimeMs),
        }

    def stats(self):
        source_stats = {}
        if self.source and callable(getattr(self.source, "stats", None)):
            with contextlib.suppress(Exception):
                source_stats = self.source.stats()
        return {
            **source_stats,
            **self.summary(),
            "physicsDt": self.options.physicsDt,
            "basePublishHz": self.options.basePublishHz,
            "publishedFrames": self.published_frames,
            "encodedPayloadBytes": self.payload_bytes,
            "readbackMode": "not_applicable"
            if self.kind == "synthetic"
            else source_stats.get("readbackMode", "source_reported"),
            "clients": [
                {
                    "subscribed": c.subscribed,
                    "info": c.info,
                    "grantedRateHz": c.granted_rate_hz,
                    "reliableQueueDepth": c.reliable.qsize(),
                    "droppedFrames": c.dropped_frames,
                    "sentMessages": c.sent_messages,
                }
                for c in self.clients
            ],
        }

    async def _stop_source(self):
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self.source:
            await self.source.close()
            self.source = None

    async def reload(self):
        async with self.command_lock:
            if self.scene_version:
                self.scene_version.require_launchable()
            self._cache_frame(self.latest_frame())
            self.state = "STOPPING"
            self.pipeline.fail_active("session_reset", "Scene was reloaded")
            self.plans.invalidate(self, "scene_reloaded")
            self.broadcast(self._lifecycle_state())
            try:
                await self._stop_source()
                # Delayed control replies retain their old sample seq/time. HTTP and
                # subscriptions may already have exposed a newer frame in the meantime.
                # Sources starting at either 0 or 1 must exceed every observed seq.
                self._seq_offset = self._seq_high_water + 1
                self._rtf_samples.clear()
                for client in self.clients:
                    client.subscribed = False
                    client.latest = None
                await self.start()
            except Exception as exc:
                if self.state != "FAILED":
                    self.fail(str(exc))
                if isinstance(exc, HTTPException):
                    raise
                raise HTTPException(503, self.error) from exc
            self.broadcast(
                SceneReplaced(
                    sessionId=self.session_id,
                    sceneRevision=self.manifest.sceneRevision,
                    revisionNumericId=self._revision_number,
                    epoch=self.epoch,
                )
            )
            self.broadcast(self._lifecycle_state())

    async def close(self):
        async with self.command_lock:
            self._auto_paused = False
            if self.pipeline:
                self.pipeline.fail_active("source_unavailable", "Session was stopped")
                self.plans.invalidate(self, "session_stopped")
                self.pipeline.close()
            self.state = "STOPPING"
            if self.manifest:
                self.broadcast(self._lifecycle_state())
            await self._stop_source()
            self.state = "STOPPED"
            if self.manifest:
                self.broadcast(self._lifecycle_state())
            await asyncio.gather(*(client.finish(drain=True) for client in tuple(self.clients)))
            self.clients.clear()
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None
            if self.recording:
                await self.recording.close()
                self._recording_revision_ready = False
                await asyncio.to_thread(self.recordings.retain)


I_WORLD = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}


class SessionManager:
    def __init__(self, settings: Settings, factory: Callable = default_source_factory, recordings=None):
        self.settings, self.factory = settings, factory
        self.recordings = recordings or RecordingRepository(settings, AssetRegistry.from_file())
        self.approvals = ApprovalStore(settings.state_root / "approvals.jsonl")
        self.plans = PlanStore(settings.state_root / "plans.jsonl", self.approvals)
        self._library = None
        self.sessions: dict[str, Session] = {}
        self._starting_session: Session | None = None
        self._create_lock = asyncio.Lock()

    @property
    def library(self):
        if self._library is None:
            self._library = SceneLibrary(state_root=self.settings.state_root)
        return self._library

    async def create(self, scene_id, source, options, *, scene_version=None):
        self.approvals.startup()
        self.plans.startup()
        selected = self.library.resolve(scene_version or scene_id, launch=True, source=source)
        if not selected.metadata["stage"].startswith("python:"):
            raise HTTPException(503, {"code": "source_unavailable",
                                      "message": "This source does not load USD scene packages"})
        source = source or selected.metadata["sources"][0]
        async with self._create_lock:
            for session in self.sessions.values():
                if session.state == "FAILED":
                    # A failed control stream may leave its runner alive; release it before replacement.
                    await session.close()
                if session.state not in ("STOPPED", "FAILED"):
                    if session.scene_version.id == selected.id and session.kind == source:
                        return session
                    raise HTTPException(
                        409,
                        {
                            "code": "session_exists",
                            "message": "Close the active session before starting another scene",
                        },
                    )
            session = Session(
                selected.name,
                source,
                options,
                self.settings,
                self.factory,
                self.recordings,
                self.approvals,
                self.plans,
                scene_version=selected,
            )
            self._starting_session = session
            try:
                await session.start()
            except asyncio.CancelledError:
                await session.close()
                raise
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(503, {"code": "source_unavailable", "message": str(exc)}) from exc
            finally:
                self._starting_session = None
            self.sessions[session.session_id] = session
            return session

    def platform_sessions(self):
        sessions = list(self.sessions.values())
        if self._starting_session is not None:
            sessions.append(self._starting_session)
        return sessions

    def get(self, session_id):
        if session_id not in self.sessions:
            raise HTTPException(404, {"code": "unknown_session"})
        return self.sessions[session_id]

    async def close(self):
        for session in self.sessions.values():
            await session.close()

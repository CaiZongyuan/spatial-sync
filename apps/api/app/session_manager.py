from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections import OrderedDict, deque
from collections.abc import Callable

from fastapi import HTTPException

from apps.api.app.delta_stream import DeltaBaseline
from apps.api.app.settings import Settings
from apps.api.app.sources.base import SpatialSource
from apps.api.app.sources.stress_scene import STRESS_SCENES
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.api.app.stream_broadcaster import ClientConnection
from apps.api.app.structured_logging import log_event
from apps.api.app.subscription_scope import ScopeResolver
from packages.protocol.internal import SessionOptions
from packages.protocol.math import compose_pose
from packages.protocol.schema import (
    Cause,
    Command,
    CommandAck,
    EntityState,
    Heartbeat,
    SceneManifest,
    SceneReplaced,
    ServerError,
    SessionState,
    Subscribe,
    SubscriptionAck,
    TransformDelta,
    TransformFrame,
    validate_command_args,
)
from packages.transport import FrameCodec
from packages.transport.scoped_frames import encode_scoped_frame

SCENES = [
    {
        "sceneId": "lab-demo",
        "name": "Synthetic Laboratory",
        "sources": ["synthetic"],
        "description": "Deterministic robot, 20 tubes, centrifuge and asset fallback; no GPU required",
    },
    {"sceneId": "falling-boxes", "name": "Newton Falling Boxes", "sources": ["newton"]},
    {"sceneId": "robot-and-tubes", "name": "Newton Robot and Tubes", "sources": ["newton"]},
    {"sceneId": "usd-lab", "name": "Newton USD Laboratory", "sources": ["newton"]},
]
SCENES += [
    {
        "sceneId": scene_id,
        "name": f"Synthetic stress · {moving} moving / {static} static",
        "sources": ["synthetic"],
        "description": "Deterministic kinematic load fixture; no physical solver or GPU",
    }
    for scene_id, (moving, static) in STRESS_SCENES.items()
]


def encode(model) -> str:
    data = model.model_dump(mode="json", exclude_none=True) if hasattr(model, "model_dump") else model
    return json.dumps(data, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


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
    def __init__(self, scene_id: str, kind: str, options: SessionOptions, settings: Settings, factory):
        self.session_id = "session-" + uuid.uuid4().hex[:12]
        self.scene_id, self.kind = scene_id, kind
        self.options, self.settings, self.factory = options, settings, factory
        self.source: SpatialSource | None = None
        self.manifest: SceneManifest | None = None
        self.state = "CREATED"
        self.epoch, self.seq, self.sim_time = 0, 0, 0.0
        self._seq_high_water = 0
        self.actual_rtf = 0.0
        self._seq_offset = 0
        self._revision_number = 0
        self._frame: TransformFrame | None = None
        self._complete_frame: TransformFrame | None = None
        self._interest: frozenset[int] | None = None
        self._last_published_seq = -1
        self._tasks: list[asyncio.Task] = []
        self._heartbeat_task: asyncio.Task | None = None
        self.clients: set[ClientConnection] = set()
        self.entity_states: dict[str, EntityState] = {}
        self.command_lock = asyncio.Lock()
        self._acks: OrderedDict[str, CommandAck] = OrderedDict()
        self._rtf_samples = deque(maxlen=60)
        self.published_frames = 0
        self.payload_bytes = 0
        self.error: dict | None = None
        self._auto_paused = False
        self._scope_resolver: ScopeResolver | None = None
        self._body_stream_ids = frozenset()
        self._next_keyframe_at = 0.0

    async def start(self):
        self.state = "LOADING"
        self._log_event("session.lifecycle", state=self.state)
        if self.manifest:
            self.broadcast(self._lifecycle_state())
        candidate = None
        try:
            candidate = self.factory(self.kind, self.session_id)
            manifest = await candidate.start(self.scene_id, self.options)
            if manifest.sessionId != self.session_id:
                raise ValueError("source manifest sessionId does not match the session")
            manifest = manifest.model_copy(update={"revisionNumericId": self._revision_number + 1})
            resolver = ScopeResolver(manifest)
            body_ids = frozenset(resolver.body_ids.values())
            # With no observers, periodic GPU captures need no body rows. The
            # reliable barrier remains a single complete recovery sample.
            interest_frame = await candidate.set_interest(frozenset())
            if not self._complete_for(interest_frame, manifest, body_ids):
                raise ValueError("Initial source interest barrier is not a complete Manifest sample")
            # Prepare the initial policy before exposing the candidate revision.
            # Creation is exclusive; reload already holds command_lock.
            policy_change = await self._observer_policy_change(candidate, "RUNNING", False)
            self.source = candidate
            self._revision_number += 1
            self.manifest, self._scope_resolver = manifest, resolver
            self._body_stream_ids = body_ids
            self._interest = frozenset()
            self._frame = self._complete_frame = None
            self._next_keyframe_at = time.monotonic() + 1
            self.state, self.error = "RUNNING", None
            self._auto_paused = False
            self._cache_frame(self._source_frame(interest_frame))
            if policy_change:
                self._apply_observer_policy_change(*policy_change, publish=False)
            self._last_published_seq = self.seq
            self._log_event("session.lifecycle", state=self.state)
            self._tasks = [
                asyncio.create_task(self._monitor(), name="session-frames"),
                asyncio.create_task(self._events(), name="session-events"),
            ]
            if self._heartbeat_task is None:
                self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="session-heartbeat")
        except Exception as exc:
            with contextlib.suppress(Exception):
                if candidate:
                    await candidate.close()
            # A failed candidate must never be read as the previous valid sample.
            self.source = None
            if self.state != "FAILED":
                self.fail(str(exc))
            raise

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
        return self.manifest is not None and self._complete_for(
            frame, self.manifest, self._body_stream_ids
        )

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
                if not isinstance(event, (ServerError, SceneReplaced)):
                    if (
                        event.sessionId != self.session_id
                        or event.sceneRevision != self.manifest.sceneRevision
                    ):
                        continue
                if isinstance(event, EntityState):
                    previous = self.entity_states.get(event.entityKey)
                    if previous and event.stateSeq <= previous.stateSeq:
                        continue
                    self.entity_states[event.entityKey] = event
                elif isinstance(event, SessionState):
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
                    self._scope_resolver = ScopeResolver(manifest)
                    self._body_stream_ids = frozenset(self._scope_resolver.body_ids.values())
                    self._revision_number = event.revisionNumericId
                    self.entity_states.clear()
                    self._cache_frame(self.latest_frame())
                    for client in self.clients:
                        client.subscribed = False
                        client.latest = None
                        client.delta_baseline = None
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
        for client in tuple(self.clients):
            client.enqueue_reliable(payload)

    def broadcast_frame(self, frame: TransformFrame, reliable: bool):
        if reliable:
            self._log_event(frame.type)
        now = time.monotonic()
        keyframe_due = now >= self._next_keyframe_at
        if keyframe_due:
            self._next_keyframe_at = now + 1
        selected, encoded = {}, {}
        self._last_published_seq = max(self._last_published_seq, frame.seq)
        self.published_frames += 1
        for client in tuple(self.clients):
            if not client.subscribed:
                continue
            scope = client.scope_filter
            scope_key = scope.key
            if scope_key not in selected:
                selected[scope_key] = scope.select(frame.poses)
            scoped = frame.model_copy(
                update={
                    "poses": selected[scope_key],
                    "subscriptionId": client.subscription_id if client.scoped_wire else None,
                    "keyframe": True if client.scoped_wire else None,
                }
            )
            baseline = client.delta_baseline
            full = (
                reliable
                or not client.delta_enabled
                or keyframe_due
                or baseline is None
                or baseline.frame.epoch != frame.epoch
                or baseline.frame.sceneRevision != frame.sceneRevision
            )
            # A control may return an already observed sample. It still carries a full
            # reliable frame before its ack/state; a delta must strictly follow its base.
            if not full and frame.seq <= baseline.frame.seq:
                continue
            if full:
                message = scoped
                if client.delta_enabled:
                    client.delta_baseline = DeltaBaseline(scoped)
            else:
                delta = baseline.difference(scoped)
                message = TransformDelta.model_construct(
                    type="transform.delta",
                    sessionId=frame.sessionId,
                    sceneRevision=frame.sceneRevision,
                    epoch=frame.epoch,
                    seq=frame.seq,
                    simTime=frame.simTime,
                    sampleTimeMs=frame.sampleTimeMs,
                    subscriptionId=client.subscription_id,
                    baseSeq=delta.base_seq,
                    poses=delta.poses,
                    removedStreamIds=delta.removed_stream_ids,
                )
            key = (
                scope_key,
                client.frame_format,
                message.subscriptionId,
                message.type,
                None if full else message.baseSeq,
            )
            if key not in encoded:
                encoded[key] = self._encode_frame(message, client.frame_format)
                payload = encoded[key]
                self.payload_bytes += len(payload.encode("utf-8") if isinstance(payload, str) else payload)
            if reliable or (keyframe_due and client.delta_enabled):
                client.enqueue_reliable(encoded[key], seq=frame.seq, force=True)
            else:
                client.offer_frame(encoded[key], frame.seq, client.subscription_id)

    def _encode_frame(self, frame, format_name: str) -> str | bytes:
        if format_name == "binary":
            if frame.subscriptionId is not None:
                return encode_scoped_frame(frame, self.manifest.revisionNumericId)
            # Source-local IPC revision/seq differ after a gateway-owned reload.
            return FrameCodec(
                self.session_id, self.manifest.sceneRevision, self.manifest.revisionNumericId
            ).encode(frame)
        return encode(frame)

    def _apply_state(self, state: SessionState):
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

    def _resolve_subscription(self, client, request):
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
            return None
        try:
            scope = self._scope_resolver.resolve(
                request.scope,
                entity_keys=request.entityKeys,
                device_key=request.deviceKey,
                viewport_planes=request.viewport.planes if request.viewport else (),
            )
        except ValueError as exc:
            client.enqueue_reliable(
                encode(
                    ServerError(
                        code="invalid_subscription",
                        message=str(exc),
                        requestId=request.requestId,
                    )
                )
            )
            return None
        return scope

    def subscribe(self, client: ClientConnection, request: Subscribe, *, scope=None, frame=None):
        scope = scope or self._resolve_subscription(client, request)
        if scope is None:
            return
        client.subscription_id += 1
        client.scoped_wire = request.delta or request.requestId is not None
        client.scope_filter = scope
        client.delta_enabled = request.delta
        client.delta_baseline = None
        client.granted_rate_hz = min(max(request.rateHz, 1), self.options.basePublishHz)
        client.frame_format = request.format
        client.subscribed = True
        # Only the lossy periodic slot is discarded. Reliable FIFO preserves earlier
        # command frame -> ack -> state, then this ack -> snapshot barrier.
        client.latest = None
        client.enqueue_reliable(
            encode(
                SubscriptionAck(
                    scope=request.scope,
                    grantedRateHz=client.granted_rate_hz,
                    format=request.format,
                    delta=request.delta,
                    requestId=request.requestId,
                    subscriptionId=client.subscription_id if client.scoped_wire else None,
                )
            )
        )
        frame = frame if frame is not None else self.latest_frame()
        if frame is None:
            client.enqueue_reliable(
                encode(
                    ServerError(
                        code="session_not_ready",
                        message="No published snapshot yet",
                    )
                )
            )
            return
        snapshot = frame.model_copy(
            update={
                "type": "stream.snapshot",
                "poses": scope.select(frame.poses),
                "subscriptionId": client.subscription_id if client.scoped_wire else None,
                "keyframe": True if client.scoped_wire else None,
            }
        )
        if client.delta_enabled:
            client.delta_baseline = DeltaBaseline(snapshot)
        # Re-subscription deliberately permits a complete snapshot at the same seq.
        client.enqueue_reliable(
            self._encode_frame(snapshot, client.frame_format),
            seq=snapshot.seq,
            force=True,
        )
        for event in self.entity_states.values():
            client.enqueue_reliable(encode(event))

    async def observer_subscribe(self, client: ClientConnection, request: Subscribe):
        async with self.command_lock:
            scope = self._resolve_subscription(client, request)
            if scope is None:
                return
            if self.state in ("FAILED", "STOPPED") and self._complete_frame is not None:
                if self._is_complete(self._complete_frame):
                    self.subscribe(client, request, scope=scope, frame=self._complete_frame)
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
            interest = self._observer_interest(client, scope)
            try:
                frame = await self._sync_interest_locked(interest)
            except TimeoutError as exc:
                if interest != self._interest:
                    self.fail(str(exc) or "Source interest change timed out")
                else:
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
            self.subscribe(client, request, scope=scope, frame=frame)
            await self._sync_observer_policy_locked()

    async def remove_client(self, client: ClientConnection):
        async with self.command_lock:
            self.clients.discard(client)
            client.subscribed = False
            if self.source and self.state in ("RUNNING", "PAUSED"):
                try:
                    interest = self._observer_interest()
                    if interest != self._interest:
                        await self._sync_interest_locked(interest)
                except Exception as exc:
                    self.fail(str(exc))
            await self._sync_observer_policy_locked()

    def _observer_interest(self, replacing=None, scope=None):
        scopes = [
            client.scope_filter
            for client in self.clients
            if client is not replacing and client.subscribed and not client.closed.is_set()
        ]
        if scope is not None:
            scopes.append(scope)
        if any(item.stream_ids is None for item in scopes):
            return None  # A viewport must gather moving bodies that may enter it.
        return frozenset(value for item in scopes for value in item.stream_ids)

    async def _sync_interest_locked(self, interest):
        changed = interest != self._interest
        frame = await (self.source.set_interest(interest) if changed else self.source.snapshot())
        frame = self._source_frame(frame)
        if not self._is_complete(frame):
            raise ValueError("Source interest barrier is not a complete Manifest sample")
        self._cache_frame(frame)
        self._interest = interest
        self._last_published_seq = max(self._last_published_seq, frame.seq)
        if changed:
            self.broadcast_frame(frame.model_copy(update={"type": "transform.frame"}), reliable=True)
        return frame

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
            policy_change = await self._observer_policy_change(
                self.source, self.state, self._auto_paused
            )
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
                    "grantedRateHz": c.granted_rate_hz,
                    "format": c.frame_format,
                    "scope": c.scope_filter.key[0] if c.scope_filter else None,
                    "subscriptionId": c.subscription_id,
                    "delta": c.delta_enabled,
                    "baseSeq": c.delta_baseline.frame.seq if c.delta_baseline else None,
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
            self._cache_frame(self.latest_frame())
            self.state = "STOPPING"
            self.broadcast(self._lifecycle_state())
            try:
                await self._stop_source()
                # Delayed control replies retain their old sample seq/time. HTTP and
                # subscriptions may already have exposed a newer frame in the meantime.
                # Sources starting at either 0 or 1 must exceed every observed seq.
                self._seq_offset = self._seq_high_water + 1
                self.entity_states.clear()
                self._rtf_samples.clear()
                for client in self.clients:
                    client.subscribed = False
                    client.latest = None
                    client.delta_baseline = None
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


I_WORLD = {"p": [0, 0, 0], "q": [0, 0, 0, 1]}


class SessionManager:
    def __init__(self, settings: Settings, factory: Callable = default_source_factory):
        self.settings, self.factory = settings, factory
        self.sessions: dict[str, Session] = {}
        self._create_lock = asyncio.Lock()

    async def create(self, scene_id, source, options):
        if not any(scene["sceneId"] == scene_id and source in scene["sources"] for scene in SCENES):
            raise HTTPException(
                404, {"code": "unknown_scene", "message": "Scene/source combination is not registered"}
            )
        async with self._create_lock:
            for session in self.sessions.values():
                if session.state == "FAILED":
                    # A failed control stream may leave its runner alive; release it before replacement.
                    await session.close()
                if session.state not in ("STOPPED", "FAILED"):
                    if session.scene_id == scene_id and session.kind == source:
                        return session
                    raise HTTPException(
                        409,
                        {
                            "code": "session_exists",
                            "message": "Close the active session before starting another scene",
                        },
                    )
            session = Session(scene_id, source, options, self.settings, self.factory)
            try:
                await session.start()
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(503, {"code": "source_unavailable", "message": str(exc)}) from exc
            self.sessions[session.session_id] = session
            return session

    def get(self, session_id):
        if session_id not in self.sessions:
            raise HTTPException(404, {"code": "unknown_session"})
        return self.sessions[session_id]

    async def close(self):
        for session in self.sessions.values():
            await session.close()

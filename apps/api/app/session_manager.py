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

from apps.api.app.settings import Settings
from apps.api.app.sources.base import SpatialSource
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.api.app.stream_broadcaster import ClientConnection
from packages.protocol.internal import SessionOptions
from packages.protocol.math import compose_pose
from packages.protocol.schema import (
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
    TransformFrame,
    validate_command_args,
)

logger = logging.getLogger("spatial.gateway")
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
        self.actual_rtf = 0.0
        self._seq_offset = 0
        self._revision_number = 0
        self._frame: TransformFrame | None = None
        self._last_published_seq = -1
        self._tasks: list[asyncio.Task] = []
        self.clients: set[ClientConnection] = set()
        self.entity_states: dict[str, EntityState] = {}
        self.command_lock = asyncio.Lock()
        self._acks: OrderedDict[str, CommandAck] = OrderedDict()
        self._rtf_samples = deque(maxlen=60)
        self.published_frames = 0
        self.payload_bytes = 0
        self.error: dict | None = None

    async def start(self):
        self.state = "LOADING"
        self.source = self.factory(self.kind, self.session_id)
        try:
            manifest = await self.source.start(self.scene_id, self.options)
            if manifest.sessionId != self.session_id:
                raise ValueError("source manifest sessionId does not match the session")
            self._revision_number += 1
            self.manifest = manifest.model_copy(update={"revisionNumericId": self._revision_number})
            self.state, self.error = "RUNNING", None
            self._cache_frame(self.latest_frame())
            self._last_published_seq = self.seq
            self._tasks = [
                asyncio.create_task(self._monitor(), name="session-frames"),
                asyncio.create_task(self._events(), name="session-events"),
                asyncio.create_task(self._heartbeat(), name="session-heartbeat"),
            ]
        except Exception:
            self.state = "FAILED"
            with contextlib.suppress(Exception):
                await self.source.close()
            raise

    def latest_frame(self) -> TransformFrame | None:
        try:
            frame = self.source.latest_frame() if self.source else None
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
        if frame is None or (cached is not None and frame.seq < cached.seq):
            return cached
        return frame

    def _cache_frame(self, frame):
        if frame is not None and frame.seq >= self.seq:
            self._frame = frame
            self.seq, self.epoch, self.sim_time = frame.seq, frame.epoch, frame.simTime

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
                "sites": len(manifest.sites),
                "joints": len(manifest.joints),
                "warnings": len(manifest.warnings),
            }
            if manifest
            else {},
            "error": self.error,
        }

    async def _monitor(self):
        while True:
            await asyncio.sleep(1 / self.options.basePublishHz)
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
                    self._revision_number = event.revisionNumericId
                    self.entity_states.clear()
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
        self.error = {"code": "source_unavailable", "message": message}
        self.state, self.actual_rtf = "FAILED", 0
        self.broadcast(ServerError(code="source_unavailable", message=message))
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
            cause={"kind": kind, **({"message": message} if message else {})},
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
        payload = encode(message)
        for client in tuple(self.clients):
            client.enqueue_reliable(payload)

    def broadcast_frame(self, frame: TransformFrame, reliable: bool):
        encoded = {}
        self._last_published_seq = max(self._last_published_seq, frame.seq)
        self.published_frames += 1
        for client in tuple(self.clients):
            if not client.subscribed:
                continue
            scope = client.stream_ids
            if scope not in encoded:
                scoped = (
                    frame
                    if scope is None
                    else frame.model_copy(update={"poses": [row for row in frame.poses if row[0] in scope]})
                )
                encoded[scope] = encode(scoped)
                self.payload_bytes += len(encoded[scope].encode("utf-8"))
            if reliable:
                client.enqueue_reliable(encoded[scope], seq=frame.seq)
            else:
                client.offer_frame(encoded[scope], frame.seq)

    def _apply_state(self, state: SessionState):
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
                    client.enqueue_reliable(encode(ack))
                    if result.state is not None:
                        state = result.state.model_copy(update={"seq": result.state.seq + self._seq_offset})
                        self._apply_state(state)
                        self.broadcast(state)
                    if client.role == "operator":
                        self._remember(ack)
                    return
            client.enqueue_reliable(encode(ack))
            if client.role == "operator":
                self._remember(ack)

    def _remember(self, ack):
        self._acks[ack.requestId] = ack
        if len(self._acks) > 256:
            self._acks.popitem(last=False)
        logger.info(
            encode(
                {
                    "event": "command.ack",
                    "sessionId": self.session_id,
                    "sceneRevision": self.manifest.sceneRevision,
                    "epoch": self.epoch,
                    "seq": self.seq,
                    "requestId": ack.requestId,
                    "status": ack.status,
                }
            )
        )

    def subscribe(self, client: ClientConnection, request: Subscribe):
        if request.sceneRevision != self.manifest.sceneRevision:
            client.enqueue_reliable(
                encode(
                    ServerError(code="revision_mismatch", message="Reload the Manifest before subscribing")
                )
            )
            return
        scope = None
        if request.scope == "entities":
            all_entities = {
                x.entityKey for x in [*self.manifest.nodes, *self.manifest.sites, *self.manifest.joints]
            }
            requested = set(request.entityKeys)
            if not requested <= all_entities:
                client.enqueue_reliable(
                    encode(
                        ServerError(code="invalid_subscription", message="Unknown entityKey in subscription")
                    )
                )
                return
            body_keys = set(requested)
            for site in self.manifest.sites:
                if site.entityKey in requested:
                    body_keys.add(site.parentBodyKey)
            for joint in self.manifest.joints:
                if joint.entityKey in requested:
                    body_keys.add(joint.childBodyKey)
            parents = {node.entityKey: node.parentEntityKey for node in self.manifest.nodes}
            for node in self.manifest.nodes:
                parent = node.parentEntityKey
                while parent is not None:
                    if parent in requested:
                        body_keys.add(node.entityKey)
                        break
                    parent = parents[parent]
            scope = frozenset(
                node.streamId
                for node in self.manifest.nodes
                if node.kind == "body" and node.entityKey in body_keys
            )
        client.stream_ids = scope
        client.granted_rate_hz = min(max(request.rateHz, 1), self.options.basePublishHz)
        client.subscribed = True
        client.latest = None
        client.enqueue_reliable(
            encode(SubscriptionAck(scope=request.scope, grantedRateHz=client.granted_rate_hz))
        )
        frame = self.latest_frame()
        if frame is None:
            client.enqueue_reliable(
                encode(ServerError(code="session_not_ready", message="No published snapshot yet"))
            )
            return
        snapshot = frame.model_copy(
            update={
                "type": "stream.snapshot",
                "poses": frame.poses if scope is None else [row for row in frame.poses if row[0] in scope],
            }
        )
        # A re-subscription deliberately permits a complete snapshot at the same seq.
        client.enqueue_reliable(encode(snapshot), seq=snapshot.seq, force=True)
        for event in self.entity_states.values():
            client.enqueue_reliable(encode(event))

    def pose(self, key: str):
        frame = self.latest_frame()
        if frame is None:
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

    async def reload(self):
        async with self.command_lock:
            self.state = "STOPPING"
            self.broadcast(self._lifecycle_state())
            await self._stop_source()
            # Sources may number their first snapshot 0 or 1; both must exceed the old Session seq.
            self._seq_offset = self.seq + 1
            self.entity_states.clear()
            self._rtf_samples.clear()
            for client in self.clients:
                client.subscribed = False
                client.latest = None
            await self.start()
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
            self.state = "STOPPING"
            if self.manifest:
                self.broadcast(self._lifecycle_state())
            await self._stop_source()
            self.state = "STOPPED"
            if self.manifest:
                self.broadcast(self._lifecycle_state())
            await asyncio.gather(*(client.finish(drain=True) for client in tuple(self.clients)))
            self.clients.clear()


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

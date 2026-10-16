"""Gateway-side SpatialSource over authenticated TLS; never imports Newton or network pickle."""

import asyncio
import time
import uuid

from packages.protocol.schema import Command, CommandResult, ServerError, SessionState, validate_command_args
from packages.transport import FrameCodec
from packages.transport.remote import (
    FRAME,
    JSON,
    MESH,
    Attached,
    Authenticated,
    Closed,
    Event,
    Failed,
    Heartbeat,
    Hello,
    InterestResult,
    JointResult,
    MeshResult,
    Outbox,
    QueryTimeout,
    Ready,
    RemoteConfig,
    Request,
    Result,
    SnapshotResult,
    Start,
    Unknown,
    close_stream,
    json_bytes,
    parse_json,
    receive,
    send,
)
from packages.transport.source_state import RunnerHeartbeat, SourceFrames


class RemoteQueryTimeout(TimeoutError):
    """A bounded read-only operation timed out without changing source ownership."""


class RemoteSource:
    def __init__(self, session_id: str, config: RemoteConfig):
        self.session_id, self.config, self.limits = session_id, config, config.limits
        self._streams, self._tasks, self._pending = [], [], {}
        self._events = asyncio.Queue(maxsize=self.limits.pending_messages)
        self._event_bytes = 0
        self._latest, self._codec, self._outbox = None, None, None
        self._failed = self._closing = self._closed = self._started = False
        self._close_task = None
        self._metrics = {}
        self._samples = None
        self._interest_lock = asyncio.Lock()
        self._requested_interest = None
        self._pending_kinds = {}
        self._runner_heartbeat = RunnerHeartbeat()

    async def _connect(self, context):
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                self.config.host,
                self.config.port,
                ssl=context,
                server_hostname=self.config.server_name,
                ssl_handshake_timeout=self.limits.handshake_seconds,
                ssl_shutdown_timeout=self.limits.close_seconds,
            ),
            self.limits.handshake_seconds,
        )
        self._streams.append(writer)
        return reader, writer

    async def start(self, scene_id, options):
        if self._started or self._closed:
            raise RuntimeError("Remote source cannot be started twice")
        self._started = True
        self.options = options.model_copy()
        try:
            context = self.config.tls_context()
            control, writer = await self._connect(context)
            await send(writer, JSON, json_bytes(Hello(role="control", token=self.config.token)), self.limits)
            _, payload = await receive(
                control, self.limits, timeout=self.limits.handshake_seconds, allowed=(JSON,)
            )
            authenticated = parse_json(payload)
            if not isinstance(authenticated, Authenticated):
                raise ValueError("Remote control authentication failed")
            frames, frame_writer = await self._connect(context)
            await send(
                frame_writer,
                JSON,
                json_bytes(Hello(role="frames", token=self.config.token, ticket=authenticated.ticket)),
                self.limits,
            )
            _, payload = await receive(
                frames, self.limits, timeout=self.limits.handshake_seconds, allowed=(JSON,)
            )
            if not isinstance(parse_json(payload), Attached):
                raise ValueError("Remote frame channel attachment failed")
            await send(
                writer,
                JSON,
                json_bytes(Start(sessionId=self.session_id, sceneId=scene_id, options=options)),
                self.limits,
            )
            _, payload = await receive(
                control, self.limits, timeout=self.limits.start_seconds, allowed=(JSON,)
            )
            ready = parse_json(payload)
            if not isinstance(ready, Ready):
                raise ValueError("Remote Runner did not provide a complete ready baseline")
            if (
                ready.manifest.sessionId != self.session_id
                or ready.manifest.source.kind != self.config.source_kind
            ):
                raise ValueError("Remote Manifest does not match the configured source/session")
            self.manifest = ready.manifest.model_copy(
                update={
                    "source": ready.manifest.source.model_copy(update={"sampleTimeClock": "gateway-receive"})
                }
            )
            self._samples = SourceFrames(self.manifest)
            self._codec = FrameCodec(
                self.session_id, self.manifest.sceneRevision, self.manifest.revisionNumericId
            )
            self._metrics.update(ready.stats)
            self._runner_heartbeat.observe(ready.stats)
            snapshot = await self._frame(control)
            if snapshot.type != "stream.snapshot":
                raise ValueError("Remote ready baseline must be a snapshot")
            self._cache(snapshot)
            self._outbox = Outbox(writer, self.limits)
            self._tasks = [
                asyncio.create_task(self._read_control(control), name="remote-control-reader"),
                asyncio.create_task(self._read_frames(frames), name="remote-frame-reader"),
                asyncio.create_task(self._heartbeat(), name="remote-heartbeat"),
            ]
            return self.manifest
        except BaseException:
            self._failed = True
            await self.close()
            raise

    async def _frame(self, reader):
        _, payload = await receive(reader, self.limits, allowed=(FRAME,))
        return self._decode_frame(payload, time.monotonic() * 1000)

    def _decode_frame(self, payload, received):
        frame = self._codec.decode(payload)
        # The host's monotonic clock is deliberately discarded at the actual Gateway.
        return frame.model_copy(update={"sampleTimeMs": received})

    def _cache(self, frame):
        self._samples.control(frame)
        self._latest = self._samples.latest

    async def _read_frames(self, reader):
        try:
            while True:
                kind, payload = await receive(reader, self.limits, allowed=(JSON, FRAME))
                received = time.monotonic() * 1000
                if kind == JSON:
                    if not isinstance(parse_json(payload), Heartbeat):
                        raise ValueError("Invalid remote frame heartbeat")
                    continue
                frame = self._decode_frame(payload, received)
                if self._samples.transition and frame.seq > self._samples.floor:
                    ids = frozenset(row[0] for row in frame.poses)
                    requested = (
                        self._samples.all_ids
                        if self._requested_interest is None
                        else self._requested_interest
                    )
                    if len(frame.poses) != len(ids) or ids not in (self._samples.expected_ids, requested):
                        raise ValueError("Invalid remote frame membership during interest transition")
                self._samples.periodic(frame)
                self._latest = self._samples.latest
        except Exception as exc:
            self._fail(type(exc).__name__)

    async def _read_control(self, reader):
        try:
            while True:
                _, payload = await receive(reader, self.limits, allowed=(JSON,))
                packet = parse_json(payload)
                if isinstance(packet, Heartbeat):
                    self._metrics.update(packet.stats)
                    self._runner_heartbeat.observe(packet.stats)
                    continue
                if isinstance(packet, Event):
                    if self._event_bytes + len(payload) > self.limits.pending_bytes:
                        raise ValueError("Remote event byte capacity exceeded")
                    self._events.put_nowait((packet.event, len(payload)))
                    self._event_bytes += len(payload)
                    continue
                if isinstance(packet, Failed):
                    raise ConnectionError("Remote source failed")
                operation = self._pending_kinds.get(packet.token)
                if operation == "interest" and not isinstance(packet, InterestResult):
                    raise ValueError("Remote interest response has the wrong operation kind")
                if operation == "snapshot" and not isinstance(packet, (SnapshotResult, QueryTimeout)):
                    raise ValueError("Remote snapshot response has the wrong operation kind")
                if isinstance(packet, Result):
                    if packet.result.frame is not None:
                        raise ValueError("Reliable remote poses must use NSYN")
                    value = packet.result
                    if packet.hasFrame:
                        frame = await self._frame(reader)
                        if (frame.epoch, frame.simTime) != (value.ack.epoch, value.ack.simTime):
                            raise ValueError("Remote command frame does not match acknowledgement")
                        if value.state and (frame.seq, frame.epoch, frame.simTime) != (
                            value.state.seq,
                            value.state.epoch,
                            value.state.simTime,
                        ):
                            raise ValueError("Remote command frame does not match authoritative state")
                        value = value.model_copy(update={"frame": frame})
                        self._cache(frame)
                    self._samples.state(value.state)
                elif isinstance(packet, (SnapshotResult, InterestResult)):
                    frame = await self._frame(reader)
                    if frame.type != "stream.snapshot":
                        raise ValueError("Remote full capture must be a snapshot")
                    if isinstance(packet, InterestResult):
                        if self._pending_kinds.get(packet.token) != "interest":
                            raise ValueError("Unexpected remote interest acknowledgement")
                        ids = self._samples.interest(packet.streamIds)
                        if ids != self._requested_interest:
                            raise ValueError("Remote interest acknowledgement does not match requested IDs")
                        self._samples.install_interest(ids, frame)
                        self._latest = self._samples.latest
                    else:
                        if operation not in (None, "snapshot"):
                            raise ValueError("Unexpected remote snapshot acknowledgement")
                        self._cache(frame)
                    value = frame
                elif isinstance(packet, JointResult):
                    value = packet.state
                elif isinstance(packet, MeshResult):
                    _, value = await receive(reader, self.limits, allowed=(MESH,))
                elif isinstance(packet, (Unknown, Closed, QueryTimeout)):
                    value = None
                else:
                    raise ValueError("Unexpected remote control response")
                future = self._pending.get(packet.token)
                if future is not None and not future.done():
                    if isinstance(packet, Unknown):
                        future.set_exception(KeyError(packet.key))
                    elif isinstance(packet, QueryTimeout):
                        future.set_exception(RemoteQueryTimeout("Remote query timed out"))
                    else:
                        future.set_result(value)
        except Exception as exc:
            self._fail(type(exc).__name__)

    async def _heartbeat(self):
        try:
            while True:
                await asyncio.sleep(self.limits.heartbeat_seconds)
                self._outbox.offer((JSON, json_bytes(Request(kind="ping", token=uuid.uuid4().hex))))
                if self._outbox.task.done():
                    raise ConnectionError("Remote writer stopped")
        except Exception as exc:
            self._fail(type(exc).__name__)

    def _fail(self, reason):
        if self._closing or self._failed:
            return
        self._failed = True
        error = RuntimeError(f"Remote source unavailable ({reason})")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        while not self._events.empty():
            self._events.get_nowait()
        self._event_bytes = 0
        self._events.put_nowait((ServerError(code="source_unavailable", message=str(error)), 0))
        if self._latest and not self._events.full():
            self._events.put_nowait(
                (
                    SessionState(
                        sessionId=self.session_id,
                        state="FAILED",
                        sceneRevision=self.manifest.sceneRevision,
                        epoch=self._latest.epoch,
                        seq=self._latest.seq,
                        simTime=self._latest.simTime,
                        realtimeFactor=self.options.realtimeFactor,
                        actualRtf=0,
                        cause={"kind": "error", "code": "source_unavailable", "message": str(error)},
                    ),
                    0,
                )
            )
        asyncio.create_task(self.close(), name="remote-failure-cleanup")

    async def _request(self, kind, **payload):
        if self._failed or self._closed or self._outbox is None:
            raise RuntimeError("Remote source unavailable")
        if len(self._pending) >= self.limits.pending_messages:
            self._fail("pending request capacity")
            raise RuntimeError("Remote pending request capacity exceeded")
        token = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self._pending[token] = future
        self._pending_kinds[token] = kind
        try:
            self._outbox.offer((JSON, json_bytes(Request(kind=kind, token=token, **payload))))
            timeout = self.limits.close_seconds * 4 if kind == "close" else self.limits.request_seconds
            return await asyncio.wait_for(future, timeout)
        except RemoteQueryTimeout:
            raise
        except TimeoutError as exc:
            if kind in ("joint", "mesh", "snapshot"):
                # Keep the reader alive to drain a late reply whose token has expired.
                raise RemoteQueryTimeout("Remote query timed out") from exc
            self._fail("request timeout")
            raise
        except (OSError, asyncio.QueueFull, ValueError) as exc:
            self._fail(type(exc).__name__)
            raise
        finally:
            self._pending.pop(token, None)
            self._pending_kinds.pop(token, None)

    async def execute(self, command: Command) -> CommandResult:
        validate_command_args(command)
        async with self._interest_lock:
            self._samples.transition = True
            try:
                result = await self._request("command", command=command)
                if result.state:
                    self.options.realtimeFactor = result.state.realtimeFactor
                return result
            finally:
                self._samples.transition = False

    async def snapshot(self):
        if not self._failed and self._samples.paused and self._samples.complete is not None:
            return self._samples.complete.model_copy(update={"type": "stream.snapshot"})
        return await self._request("snapshot")

    async def set_interest(self, stream_ids):
        ids = self._samples.interest(stream_ids)
        async with self._interest_lock:
            self._samples.transition = True
            self._requested_interest = ids
            try:
                return await self._request("interest", streamIds=None if ids is None else sorted(ids))
            except BaseException:
                self._fail("interest change failed")
                raise

    async def query_joint_state(self, joint_key):
        return await self._request("joint", key=joint_key)

    async def read_mesh(self, mesh_id):
        return await self._request("mesh", key=mesh_id)

    def latest_frame(self):
        return self._latest

    async def events(self):
        while (entry := await self._events.get()) is not None:
            event, size = entry
            self._event_bytes -= size
            yield event

    def stats(self):
        connected = self._started and not (self._failed or self._closed)
        return {
            **self._metrics,
            **self._runner_heartbeat.stats(connected and self._metrics.get("runnerAlive", True)),
            "transport": "authenticated-tls",
            "sampleTimeClock": "gateway-receive",
            "remoteConnected": connected,
            "runnerAlive": connected and self._metrics.get("runnerAlive", True),
            "runnerPIDIsRemote": True,
            "remoteTasksAlive": sum(not task.done() for task in self._tasks)
            + int(self._outbox is not None and not self._outbox.task.done()),
        }

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned(), name="remote-source-close")
        await asyncio.shield(self._close_task)

    async def _close_owned(self):
        self._closing = True
        if self._outbox and not self._failed:
            try:
                await self._request("close")
                # EOF follows host cleanup and release of its one-session ownership slot.
                await asyncio.wait_for(self._tasks[0], self.limits.close_seconds)
            except Exception:
                pass
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._outbox:
            await self._outbox.close()
        await asyncio.gather(
            *(close_stream(writer, self.limits.close_seconds) for writer in self._streams),
            return_exceptions=True,
        )
        self._closed = True
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Remote source closed"))
        self._pending.clear()
        if self._events.full():
            _, size = self._events.get_nowait()
            self._event_bytes -= size
        self._events.put_nowait(None)

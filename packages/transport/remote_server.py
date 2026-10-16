"""Authenticated Runner host. Local NewtonSource owns the only spawned simulation State."""

import asyncio
import hmac
import secrets
from collections import OrderedDict

from packages.protocol.schema import ServerError, validate_command_args
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
    JointResult,
    MeshResult,
    Outbox,
    QueryTimeout,
    Ready,
    Request,
    Result,
    ServerConfig,
    Start,
    Unknown,
    close_stream,
    json_bytes,
    parse_json,
    receive,
    send,
)


class RunnerServer:
    def __init__(self, config: ServerConfig, *, source_factory=None):
        self.config, self.limits = config, config.limits
        self.source_factory = source_factory or self._source
        self._server = None
        self._handlers = set()
        self._peers = {}
        self.rejected_connections = 0

    def _source(self, session_id):
        if self.config.source_kind == "synthetic":
            from apps.api.app.sources.synthetic_source import SyntheticSource

            return SyntheticSource(session_id)
        from apps.api.app.sources.newton_source import NewtonSource

        return NewtonSource(session_id)

    @property
    def port(self):
        return self._server.sockets[0].getsockname()[1]

    @property
    def active_sessions(self):
        return len(self._peers)

    async def __aenter__(self):
        self._server = await asyncio.start_server(
            self._accept,
            self.config.host,
            self.config.port,
            ssl=self.config.tls_context(),
            ssl_handshake_timeout=self.limits.handshake_seconds,
            ssl_shutdown_timeout=self.limits.close_seconds,
            backlog=16,
            limit=65536,
        )
        return self

    async def __aexit__(self, *_):
        await self.close()

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        self._handlers.add(task)
        peer = None
        owns_peer = False
        owns_frame = False
        try:
            if len(self._handlers) > 8:
                raise ConnectionError("Runner handshake capacity exceeded")
            _, payload = await receive(
                reader, self.limits, timeout=self.limits.handshake_seconds, allowed=(JSON,)
            )
            hello = parse_json(payload)
            if not isinstance(hello, Hello) or not hmac.compare_digest(
                hello.token.encode("utf-8"), self.config.token.encode("utf-8")
            ):
                raise ValueError("Runner authentication failed")
            if hello.role == "control":
                if hello.ticket is not None or self._peers:
                    raise ConnectionError("Runner already owns a session")
                ticket = secrets.token_urlsafe(32)
                peer = _Peer(self, reader, writer)
                self._peers[ticket] = peer
                owns_peer = True
                await send(writer, JSON, json_bytes(Authenticated(ticket=ticket)), self.limits)
                await peer.run()
            else:
                peer = self._peers.get(hello.ticket)
                if peer is None or peer.attached.is_set():
                    raise ValueError("Frame channel must bind one authenticated control peer")
                # Attachment is atomic between validation and the first await.
                peer.frame_reader, peer.frame_writer = reader, writer
                peer.attached.set()
                owns_frame = True
                await send(writer, JSON, json_bytes(Attached()), self.limits)
                peer.frame_acknowledged.set()
                await peer.finished.wait()
        except Exception:
            self.rejected_connections += 1
            if owns_peer and peer.control is not None:
                try:
                    peer.control.offer((JSON, json_bytes(Failed(message="Remote Runner operation failed"))))
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            if owns_peer:
                try:
                    await peer.close()
                finally:
                    self._peers = {
                        ticket: value for ticket, value in self._peers.items() if value is not peer
                    }
                    peer.finished.set()
            elif owns_frame and not peer.finished.is_set():
                peer.abort()
            await close_stream(writer, self.limits.close_seconds)
            self._handlers.discard(task)

    async def close(self):
        if self._server:
            self._server.close()
        tasks = list(self._handlers)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server:
            await self._server.wait_closed()


class _Peer:
    def __init__(self, server, reader, writer):
        self.server, self.reader, self.writer, self.limits = server, reader, writer, server.limits
        self.attached, self.frame_acknowledged, self.finished = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        self.frame_reader = self.frame_writer = None
        self.source = self.control = self.frames = None
        self.requests = asyncio.Queue(maxsize=self.limits.pending_messages)
        self.tasks = []
        self.ready = False
        self.closing = False
        self.source_closed = False
        self._close_task = None
        self.commands, self.command_bytes = OrderedDict(), 0
        self.owner = asyncio.current_task()

    def abort(self):
        if not self.closing and self.owner is not asyncio.current_task():
            self.owner.cancel()

    async def run(self):
        await asyncio.wait_for(self.frame_acknowledged.wait(), self.limits.handshake_seconds)
        _, payload = await receive(
            self.reader, self.limits, timeout=self.limits.handshake_seconds, allowed=(JSON,)
        )
        start = parse_json(payload)
        scenes = (
            ("lab-demo",)
            if self.server.config.source_kind == "synthetic"
            else ("falling-boxes", "robot-and-tubes", "usd-lab")
        )
        if not isinstance(start, Start) or start.sceneId not in scenes:
            raise ValueError("Remote scene is not registered for this fixed source")
        self.control, self.frames = (
            Outbox(self.writer, self.limits),
            Outbox(self.frame_writer, self.limits, latest=True),
        )
        self.tasks = [
            asyncio.create_task(self._requests(), name="runner-control-input"),
            asyncio.create_task(self._operate(start), name="runner-source-authority"),
            asyncio.create_task(self._pulse(), name="runner-frame-publication"),
            asyncio.create_task(self._frame_disconnect(), name="runner-frame-peer-watch"),
        ]
        watched = [*self.tasks, self.control.task, self.frames.task]
        done, _ = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()

    async def _requests(self):
        while True:
            _, payload = await receive(
                self.reader,
                self.limits,
                timeout=self.limits.read_seconds if self.ready else self.limits.start_seconds,
                allowed=(JSON,),
            )
            packet = parse_json(payload)
            if not isinstance(packet, Request):
                raise ValueError("Unexpected Runner operation")
            if packet.command:
                validate_command_args(packet.command)
            self.requests.put_nowait(packet)

    async def _frame_disconnect(self):
        # The frame channel is unidirectional after attachment; EOF or any input ends this peer.
        await self.frame_reader.read(1)
        raise ConnectionError("Remote frame peer disconnected or sent unexpected input")

    def _stats(self):
        return self.source.stats() if self.source and hasattr(self.source, "stats") else {}

    async def _pulse(self):
        last_seq, next_heartbeat = -1, 0.0
        loop = asyncio.get_running_loop()
        while True:
            if self.ready:
                frame = self.source.latest_frame()
                if frame is not None and frame.seq > last_seq:
                    self.frames.offer((FRAME, self.codec.encode(frame)))
                    last_seq = frame.seq
            now = loop.time()
            if now >= next_heartbeat:
                # Data heartbeats also keep paused/loading peers under a finite read timeout.
                if self.frames.queue.empty():
                    self.frames.offer((JSON, json_bytes(Heartbeat())))
                if self.ready:
                    self.control.offer((JSON, json_bytes(Heartbeat(stats=self._stats()))))
                next_heartbeat = now + self.limits.heartbeat_seconds
            await asyncio.sleep(0.01)

    async def _events(self):
        async for event in self.source.events():
            self.control.offer((JSON, json_bytes(Event(event=event))))
            if isinstance(event, ServerError) and event.code == "source_unavailable":
                raise ConnectionError("Owned Runner source failed")

    async def _operate(self, start):
        self.source = self.server.source_factory(start.sessionId)
        options = start.options.model_copy(update={"device": self.server.config.device})
        manifest = await asyncio.wait_for(
            self.source.start(start.sceneId, options), self.limits.start_seconds
        )
        if manifest.sessionId != start.sessionId or manifest.source.kind != self.server.config.source_kind:
            raise ValueError("Owned source returned inconsistent Manifest identity")
        self.codec = FrameCodec(start.sessionId, manifest.sceneRevision, manifest.revisionNumericId)
        snapshot = self.source.latest_frame().model_copy(update={"type": "stream.snapshot"})
        self.control.offer(
            (JSON, json_bytes(Ready(manifest=manifest, stats=self._stats()))),
            (FRAME, self.codec.encode(snapshot)),
        )
        self.ready = True
        events = asyncio.create_task(self._events(), name="runner-source-events")
        self.tasks.append(events)
        request_task = None
        try:
            while True:
                if events.done():
                    events.result()
                request_task = asyncio.create_task(self.requests.get())
                done, _ = await asyncio.wait((request_task, events), return_when=asyncio.FIRST_COMPLETED)
                if events in done:
                    request_task.cancel()
                    await asyncio.gather(request_task, return_exceptions=True)
                    events.result()
                    raise ConnectionError("Runner source event stream ended")
                packet = request_task.result()
                if packet.kind == "close":
                    await asyncio.wait_for(self.source.close(), self.limits.close_seconds * 4)
                    self.source_closed = True
                    self.control.offer((JSON, json_bytes(Closed(token=packet.token))))
                    return
                if packet.kind == "ping":
                    continue
                try:
                    async with asyncio.timeout(self.limits.request_seconds):
                        if packet.kind == "command":
                            await self._command(packet)
                        elif packet.kind == "joint":
                            state = await self.source.query_joint_state(packet.key)
                            self.control.offer(
                                (JSON, json_bytes(JointResult(token=packet.token, state=state)))
                            )
                        else:
                            data = await self.source.read_mesh(packet.key)
                            self.control.offer(
                                (JSON, json_bytes(MeshResult(token=packet.token))), (MESH, data)
                            )
                except KeyError:
                    self.control.offer((JSON, json_bytes(Unknown(token=packet.token, key=packet.key))))
                except TimeoutError:
                    if packet.kind == "command":
                        raise
                    self.control.offer((JSON, json_bytes(QueryTimeout(token=packet.token))))
        finally:
            if request_task is not None:
                request_task.cancel()
                await asyncio.gather(request_task, return_exceptions=True)
            events.cancel()
            await asyncio.gather(events, return_exceptions=True)

    async def _command(self, packet):
        key = packet.command.requestId
        if key in self.commands:
            result, frame, _ = self.commands[key]
        else:
            result = await self.source.execute(packet.command)
            frame = self.codec.encode(result.frame) if result.frame else None
            result = result.model_copy(update={"frame": None})
            size = len(
                json_bytes(Result(token=packet.token, result=result, hasFrame=frame is not None))
            ) + len(frame or b"")
            if size <= self.limits.pending_bytes:
                self.commands[key] = (result, frame, size)
                self.command_bytes += size
                while (
                    len(self.commands) > self.limits.pending_messages
                    or self.command_bytes > self.limits.pending_bytes
                ):
                    _, (_, _, old_size) = self.commands.popitem(last=False)
                    self.command_bytes -= old_size
        packets = [(JSON, json_bytes(Result(token=packet.token, result=result, hasFrame=frame is not None)))]
        if frame:
            packets.append((FRAME, frame))
        self.control.offer(*packets)

    async def close(self):
        self.closing = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_owned(), name="runner-peer-cleanup")
        await asyncio.shield(self._close_task)

    async def _close_owned(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.source and not self.source_closed:
            try:
                await asyncio.wait_for(self.source.close(), self.limits.close_seconds * 4)
            except (Exception, asyncio.CancelledError):
                pass
        if self.control:
            await self.control.close(drain=True)
        if self.frames:
            await self.frames.close()
        if self.frame_writer:
            await close_stream(self.frame_writer, self.limits.close_seconds)

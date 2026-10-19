"""Async gateway adapter. Newton/Warp are imported only inside the spawned runner."""

import asyncio
import contextlib
import multiprocessing as mp
import threading
import uuid
from collections import OrderedDict

from packages.info.catalog import Catalog
from packages.info.store import StateStore
from packages.protocol.internal import CommandResult, JointState, SessionOptions
from packages.protocol.schema import (
    Command,
    EntityEvent,
    EntityObservations,
    EntitySnapshot,
    Invocation,
    InvocationUpdate,
    SceneManifest,
    ServerError,
    SessionState,
    TransformFrame,
)
from packages.transport import FrameCodec, PipeWriter, create_transport
from packages.transport.source_state import RunnerHeartbeat, SourceFrames


class NewtonSource:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.scene_package = None
        self._process = None
        self._transport = None
        self._writer = None
        self._threads = []
        self._stop_readers = threading.Event()
        self._incoming_lock = threading.Lock()
        self._incoming = None
        self._frame_callback_pending = False
        self._codec = None
        self._latest = None
        self._samples = None
        self._command_lock = asyncio.Lock()
        self._runner_heartbeat = RunnerHeartbeat()
        self._pending = {}
        self._events = asyncio.Queue()
        self._metrics = {}
        self._failed = False
        self._failure_reason = None
        self._closing = False
        self._closed = False
        self._watch_task = None
        self._close_task = None
        self._info_store, self._info_latest = None, None
        self._invocations = OrderedDict()
        self._starting = True

    def configure_scene(self, scene_package):
        self.scene_package = scene_package

    async def start(self, scene_id: str, options: SessionOptions) -> tuple[SceneManifest, Catalog]:
        from packages.sim_runtime.runner import run_runner

        if self._process is not None or self._closed:
            raise RuntimeError("Newton source cannot be started twice")
        self._loop = asyncio.get_running_loop()
        self._ready = self._loop.create_future()
        self.options = options.model_copy()
        self.scene_id = scene_id
        parent, child = create_transport()
        self._transport = parent
        self._process = mp.get_context("spawn").Process(
            target=run_runner,
            args=(
                child, self.session_id, scene_id, options.model_dump(), self.scene_package
            ),
            name=f"newton-{self.session_id}",
        )
        self._process.start()
        child.close()
        self._metrics["runnerPID"] = self._process.pid
        self._writer = PipeWriter(parent.control, name=f"newton-control-write-{self.session_id}")
        for name, reader in (("frames", self._read_frames), ("control", self._read_control)):
            thread = threading.Thread(target=reader, name=f"newton-{name}-{self.session_id}", daemon=True)
            self._threads.append(thread)
            thread.start()
        self._watch_task = asyncio.create_task(self._watch(), name=f"newton-watch-{self.session_id}")
        try:
            result = await asyncio.wait_for(self._ready, timeout=180)
            self._starting = False
            self._loop.call_soon(self._drain_frame)
            return result
        except BaseException:
            await self.close()
            raise

    def _call_loop(self, callback, *args):
        try:
            self._loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            self._stop_readers.set()

    def _read_frames(self):
        try:
            while not self._stop_readers.is_set():
                if not self._transport.frames.poll(0.1):
                    continue
                payload = self._transport.frames.recv_bytes()
                with self._incoming_lock:
                    self._incoming = payload
                    if not self._frame_callback_pending:
                        self._frame_callback_pending = True
                        self._call_loop(self._drain_frame)
        except (OSError, EOFError) as exc:
            self._call_loop(self._fail, f"Newton frame pipe closed: {exc}")

    def _read_control(self):
        try:
            while not self._stop_readers.is_set():
                if self._transport.control.poll(0.1):
                    self._call_loop(self._on_control, self._transport.control.recv())
        except (OSError, EOFError) as exc:
            self._call_loop(self._fail, f"Newton control pipe closed: {exc}")

    def _cache(self, frame):
        self._samples.control(frame)
        self._latest = self._samples.latest

    def _drain_frame(self):
        with self._incoming_lock:
            self._frame_callback_pending = False
            if self._codec is None or self._starting:
                return
            payload, self._incoming = self._incoming, None
        if payload is not None:
            try:
                self._samples.periodic(self._codec.decode(payload))
                self._latest = self._samples.latest
            except ValueError as exc:
                self._fail(f"Invalid Newton frame pipe payload: {exc}")

    def _on_control(self, packet):
        try:
            kind = packet["kind"]
            if "stats" in packet:
                self._metrics.update(packet["stats"])
                self._runner_heartbeat.observe(packet["stats"])
            if kind == "ready":
                self.manifest = SceneManifest.model_validate(packet["manifest"])
                from packages.sim_runtime.info_catalog import load_info_catalog

                self.catalog = load_info_catalog(
                    self.manifest, self.scene_id, scene_package=self.scene_package
                )
                if self.catalog.model_dump(mode="json") != packet["catalog"]:
                    raise ValueError("Runner and gateway Catalog definitions differ")
                self._info_store = StateStore(self.manifest.sceneRevision)
                self._cache_info(EntitySnapshot.model_validate(packet["entitySnapshot"]))
                self._samples = SourceFrames(self.manifest)
                self._codec = FrameCodec(
                    self.session_id, self.manifest.sceneRevision, self.manifest.revisionNumericId
                )
                self._cache(TransformFrame.model_validate(packet["snapshot"]))
                self._drain_frame()
                if not self._ready.done():
                    self._ready.set_result((self.manifest, self.catalog))
            elif kind == "info_event":
                definition = {
                    "entity.snapshot": EntitySnapshot,
                    "entity.observations": EntityObservations,
                    "entity.event": EntityEvent,
                    "invocation.update": InvocationUpdate,
                    "server.error": ServerError,
                }
                event = definition[packet["event"]["type"]].model_validate(packet["event"])
                self._cache_info(event)
                self._events.put_nowait(event)
            elif kind == "event":
                self._events.put_nowait(ServerError.model_validate(packet["event"]))
            elif kind == "failed":
                self._fail(packet["message"])
            elif kind == "stats":
                pass
            else:
                future = self._pending.get(packet.get("token"))
                if kind == "result":
                    result = CommandResult.model_validate(packet["result"])
                    if result.frame:
                        self._cache(result.frame)
                    self._samples.state(result.state)
                    if packet.get("entitySnapshot"):
                        self._cache_info(EntitySnapshot.model_validate(packet["entitySnapshot"]))
                    value = result
                elif kind == "invocation_result":
                    value = InvocationUpdate.model_validate(packet["update"])
                elif kind == "snapshot_result":
                    value = TransformFrame.model_validate(packet["frame"])
                    if value.type != "stream.snapshot":
                        raise ValueError("A full capture must be a snapshot")
                    self._cache(value)
                elif kind == "joint_result":
                    value = JointState.model_validate(packet["state"])
                elif kind == "mesh_result":
                    value = packet["data"]
                elif kind == "viewer_result":
                    value = packet["viewer"]
                elif kind == "viewer_error":
                    if future is not None and not future.done():
                        future.set_exception(RuntimeError(packet["message"]))
                    return
                elif kind == "unknown_entity":
                    if future is not None and not future.done():
                        future.set_exception(KeyError(packet["key"]))
                    return
                elif kind == "closed":
                    value = None
                else:
                    raise ValueError(f"Unknown Newton control response: {kind}")
                if future is not None and not future.done():
                    future.set_result(value)
        except Exception as exc:
            self._fail(f"Invalid Newton control response: {exc}")

    async def _watch(self):
        while not self._closing:
            if not self._process.is_alive():
                self._fail(f"Newton runner exited with code {self._process.exitcode}")
                break
            await asyncio.sleep(0.05)

    def _fail(self, message):
        if self._closing or self._failed:
            return
        self._failed = True
        self._failure_reason = message
        error = RuntimeError(f"Newton source unavailable: {message}")
        if not self._ready.done():
            self._ready.set_exception(error)
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        self._events.put_nowait(ServerError(code="source_unavailable", message=message))
        if self._latest is not None:
            self._events.put_nowait(
                SessionState(
                    sessionId=self.session_id,
                    state="FAILED",
                    sceneRevision=self._latest.sceneRevision,
                    epoch=self._latest.epoch,
                    seq=self._latest.seq,
                    simTime=self._latest.simTime,
                    realtimeFactor=self.options.realtimeFactor,
                    actualRtf=0,
                    cause={"kind": "error", "code": "source_unavailable", "message": message},
                )
            )

    async def _request(self, kind, *, timeout=5, **payload):
        if self._failed or self._closed or self._writer is None:
            raise RuntimeError("Newton source unavailable")
        token = uuid.uuid4().hex
        future = self._loop.create_future()
        self._pending[token] = future
        try:
            self._writer.offer({"kind": kind, "token": token, **payload})
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(token, None)

    def latest_frame(self) -> TransformFrame | None:
        return self._latest

    def _cache_info(self, event):
        if isinstance(event, (EntitySnapshot, EntityObservations)):
            if not self._info_store.apply(event):
                return
            self._info_latest = event
        if isinstance(event, EntitySnapshot):
            self._invocations = OrderedDict((inv.invocationId, inv) for inv in event.invocations)
        elif isinstance(event, InvocationUpdate):
            self._invocations[event.invocationId] = Invocation.model_validate(
                {k: v for k, v in event.model_dump().items() if k not in ("type", "sceneRevision")}
            )
            terminal = [
                key
                for key, inv in self._invocations.items()
                if inv.status in ("succeeded", "failed", "cancelled", "rejected")
            ]
            for key in terminal[:-50]:
                del self._invocations[key]

    def entity_snapshot(self):
        latest = self._info_latest
        return EntitySnapshot(
            sceneRevision=self.manifest.sceneRevision,
            epoch=self._info_store.epoch,
            simTime=latest.simTime,
            sampleTimeMs=latest.sampleTimeMs,
            entities=list(self._info_store.entities.values()),
            invocations=list(self._invocations.values()),
        )

    async def invoke(self, request):
        return await self._request("invoke", timeout=0.5, request=request.model_dump(mode="json"))

    async def cancel(self, invocation_id):
        return await self._request("cancel", timeout=0.5, invocationId=invocation_id)

    async def revoke_before_start(self, invocation_id):
        return await self._request("revoke_before_start", timeout=0.5, invocationId=invocation_id)

    async def snapshot(self) -> TransformFrame:
        if self._samples and self._samples.paused and self._latest is not None:
            return self._latest.model_copy(update={"type": "stream.snapshot"})
        return await self._request("snapshot")

    async def events(self):
        while (event := await self._events.get()) is not None:
            yield event

    async def execute(self, command: Command) -> CommandResult:
        async with self._command_lock:
            try:
                result = await self._request("command", command=command.model_dump())
                if result.state:
                    self.options.realtimeFactor = result.state.realtimeFactor
                return result
            except TimeoutError:
                self._fail("Newton command timed out")
                await self.close()
                raise

    async def query_joint_state(self, joint_key: str) -> JointState:
        return await self._request("joint", key=joint_key, timeout=0.2)

    async def read_mesh(self, mesh_id: str) -> bytes:
        return await self._request("mesh", key=mesh_id)

    async def open_viewer(self) -> dict:
        if self._starting:
            raise RuntimeError("Newton source is still loading")
        try:
            return await self._request("open_viewer", timeout=30)
        except TimeoutError as exc:
            raise RuntimeError(
                "Native viewer did not become ready; retry after checking the backend"
            ) from exc

    def stats(self) -> dict:
        process = self._process
        pid = self._metrics.get("runnerPID")
        try:
            alive = bool(process and not self._closed and process.is_alive())
            if process is not None:
                pid = process.pid
        except ValueError:  # A sampler may overlap Process.close().
            alive = False
        viewer = self._metrics.get(
            "viewer", {"status": "not_started", "url": None, "clientCount": 0, "reason": None}
        )
        if self._closed or self._failed or (process is not None and not alive):
            viewer = {
                "status": "unavailable", "url": None, "clientCount": None,
                "reason": self._failure_reason or "Newton backend is not running",
            }
        return {
            **self._metrics,
            **self._runner_heartbeat.stats(alive and not self._failed),
            "runnerPID": pid,
            "device": self._metrics.get("device"),
            "runnerAlive": alive,
            "readerThreadsAlive": [thread.name for thread in self._threads if thread.is_alive()],
            "viewer": viewer,
        }

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                self._close_owned(), name=f"newton-close-{self.session_id}"
            )
        await asyncio.shield(self._close_task)

    async def _close_owned(self):
        if self._closed:
            return
        self._closing = True
        try:
            if self._process and self._process.is_alive() and not self._failed:
                with contextlib.suppress(Exception):
                    await self._request("close", timeout=2)
            if self._process:
                await asyncio.to_thread(self._process.join, 2)
                if self._process.is_alive():
                    self._process.terminate()
                    await asyncio.to_thread(self._process.join, 2)
                if self._process.is_alive():
                    self._process.kill()
                    await asyncio.to_thread(self._process.join, 2)
            self._stop_readers.set()
            if self._transport:
                self._transport.close()
            if self._writer:
                await asyncio.to_thread(self._writer.close)
            for thread in self._threads:
                await asyncio.to_thread(thread.join, 1)
            if self._watch_task:
                self._watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._watch_task
            if self._process and not self._process.is_alive():
                self._process.close()
                self._process = None
        finally:
            self._closed = True
            self._events.put_nowait(None)
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("Newton source closed"))
            self._pending.clear()

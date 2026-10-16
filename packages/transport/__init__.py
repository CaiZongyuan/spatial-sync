"""Local spawn transport. This module never imports Newton or Warp."""

import multiprocessing as mp
import queue
import struct
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection

from packages.protocol.schema import TransformFrame

HEADER = struct.Struct("<4sHHIIQddI")
POSE = struct.Struct("<I7f")


class FrameCodec:
    def __init__(self, session_id: str, scene_revision: str, revision_numeric_id: int):
        self.session_id = session_id
        self.scene_revision = scene_revision
        self.revision_numeric_id = revision_numeric_id

    def encode(self, frame: TransformFrame) -> bytes:
        header = HEADER.pack(
            b"NSYN",
            1,
            2 if frame.type == "stream.snapshot" else 1,
            self.revision_numeric_id,
            frame.epoch,
            frame.seq,
            frame.simTime,
            frame.sampleTimeMs,
            len(frame.poses),
        )
        return header + b"".join(POSE.pack(*row) for row in frame.poses)

    def decode(self, payload: bytes) -> TransformFrame:
        if len(payload) < HEADER.size:
            raise ValueError("Invalid frame length")
        magic, version, kind, revision, epoch, seq, sim_time, sample_time, count = HEADER.unpack_from(payload)
        if magic != b"NSYN" or version != 1 or kind not in (1, 2):
            raise ValueError("Invalid frame header")
        if revision != self.revision_numeric_id:
            raise ValueError("Unknown frame revision")
        if len(payload) != HEADER.size + count * POSE.size:
            raise ValueError("Invalid frame length")
        return TransformFrame(
            type="stream.snapshot" if kind == 2 else "transform.frame",
            sessionId=self.session_id,
            sceneRevision=self.scene_revision,
            epoch=epoch,
            seq=seq,
            simTime=sim_time,
            sampleTimeMs=sample_time,
            poses=list(POSE.iter_unpack(payload[HEADER.size :])),
        )


class LatestSlot:
    """One replaceable value. Producers never wait for a pipe consumer."""

    def __init__(self):
        self._condition = threading.Condition()
        self._value = None
        self._closed = False
        self.overwritten = 0

    def put(self, value):
        with self._condition:
            if self._closed:
                return
            self.overwritten += self._value is not None
            self._value = value
            self._condition.notify()

    def take(self):
        with self._condition:
            self._condition.wait_for(lambda: self._value is not None or self._closed)
            value, self._value = self._value, None
            return value

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()


@dataclass
class RunnerTransport:
    frames: Connection
    control: Connection

    def close(self):
        self.frames.close()
        self.control.close()


def create_transport() -> tuple[RunnerTransport, RunnerTransport]:
    context = mp.get_context("spawn")
    frame_read, frame_write = context.Pipe(duplex=False)
    parent_control, child_control = context.Pipe(duplex=True)
    return RunnerTransport(frame_read, parent_control), RunnerTransport(frame_write, child_control)


class PipeWriter:
    """One sending thread owns each connection direction, including reliable control."""

    def __init__(self, connection: Connection, *, latest_only=False, name="runner-pipe-writer"):
        self.connection = connection
        self.latest_only = latest_only
        self.slot = LatestSlot() if latest_only else None
        self.queue = queue.Queue(maxsize=256) if not latest_only else None
        self.stopped = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)
        self.thread.start()

    def offer(self, message):
        if self.stopped.is_set():
            raise RuntimeError("Runner pipe writer is stopped")
        if self.latest_only:
            self.slot.put(message)
        else:
            self.queue.put_nowait(message)

    def _run(self):
        try:
            while not self.stopped.is_set():
                if self.latest_only:
                    value = self.slot.take()
                    if value is None:
                        break
                    self.connection.send_bytes(value)
                else:
                    try:
                        value = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        self.connection.send(value)
                    finally:
                        self.queue.task_done()
        except (OSError, EOFError, BrokenPipeError) as exc:
            self.error = str(exc)
        finally:
            self.stopped.set()

    def close(self, *, drain=False):
        if drain and self.queue:
            deadline = time.monotonic() + 0.5
            with self.queue.all_tasks_done:
                self.queue.all_tasks_done.wait_for(
                    lambda: self.queue.unfinished_tasks == 0,
                    timeout=max(0, deadline - time.monotonic()),
                )
        self.stopped.set()
        if self.slot:
            self.slot.close()
        self.thread.join(timeout=0.5)

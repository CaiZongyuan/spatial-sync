"""Portable v1 session logs. Only the writer thread touches recording files."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import struct
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from packages.info.invocations import TERMINAL
from packages.info.store import StateStore
from packages.protocol.schema import EntityObservations, EntitySnapshot, Invocation
from packages.transport import HEADER as FRAME_HEADER

FILE_HEADER = b"LWLG\x01\x00"
RECORD_HEADER = struct.Struct("<IBd")
MAX_RECORD_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Record:
    kind: int
    t_ms: float
    payload: bytes
    offset: int = 0

    def encode(self):
        return RECORD_HEADER.pack(9 + len(self.payload), self.kind, self.t_ms) + self.payload


@dataclass(frozen=True)
class SegmentRead:
    records: list[Record]
    valid_bytes: int
    truncated: bool


def read_segment(raw: bytes) -> SegmentRead:
    """Read the valid prefix, tolerating an interrupted final write only."""
    if raw[:6] != FILE_HEADER:
        raise ValueError("Unsupported session log header/version")
    records, offset = [], 6
    while offset < len(raw):
        if len(raw) - offset < RECORD_HEADER.size:
            return SegmentRead(records, offset, True)
        length, kind, t_ms = RECORD_HEADER.unpack_from(raw, offset)
        if length < 9 or length > MAX_RECORD_BYTES or kind not in (1, 2, 3) or not math.isfinite(t_ms):
            raise ValueError(f"Invalid session log record at {offset}")
        end = offset + 4 + length
        if end > len(raw):
            return SegmentRead(records, offset, True)
        records.append(Record(kind, t_ms, raw[offset + RECORD_HEADER.size : end], offset))
        offset = end
    return SegmentRead(records, offset, False)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def _atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(_json_bytes(value))
    for attempt in range(5):
        try:
            temporary.replace(path)
            return
        except PermissionError as exc:
            # Windows readers can temporarily deny FILE_SHARE_DELETE. All waits
            # stay on the disk-owner thread; persistent failures still follow D04.
            if attempt == 4 or getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(0.01 * 2**attempt)


def _sim_time(record):
    if record.kind == 1:
        return FRAME_HEADER.unpack_from(record.payload)[6]
    return json.loads(record.payload).get("simTime") if record.kind == 2 else None


def segment_metadata(file, result, scene_revision=None):
    records = result.records
    if len(records) < 2 or records[0].kind != 1 or records[1].kind != 2:
        raise ValueError("Segment must begin with spatial and entity snapshots")
    if json.loads(records[1].payload).get("type") != "entity.snapshot":
        raise ValueError("Segment is missing its entity snapshot")
    sim_times = [value for record in records if (value := _sim_time(record)) is not None]
    return {
        "file": file,
        "sceneRevision": scene_revision or json.loads(records[1].payload)["sceneRevision"],
        "startSampleTimeMs": records[0].t_ms,
        "endSampleTimeMs": max(record.t_ms for record in records),
        "startSimTime": sim_times[0] if sim_times else 0,
        "endSimTime": sim_times[-1] if sim_times else 0,
        "bytes": result.valid_bytes,
    }


def recover_recordings(root: Path):
    """Recover even an open first segment whose metadata never reached a roll."""
    root = Path(root)
    if not root.exists():
        return
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        metadata_file = directory / "recording.json"
        try:
            metadata = json.loads(metadata_file.read_bytes())
            if metadata.get("formatVersion") != 1 or metadata.get("status") != "recording":
                continue
            metadata["segments"] = []
            for path in sorted(directory.glob("seg-*.bin")):
                if path.is_symlink():
                    continue
                result = read_segment(path.read_bytes())
                if len(result.records) >= 2:
                    metadata["segments"].append(segment_metadata(path.name, result))
            metadata["status"] = "incomplete"
            _duration(metadata)
            _atomic_json(metadata_file, metadata)
        except (OSError, ValueError, KeyError, struct.error):
            # A foreign/corrupt directory must not prevent gateway startup.
            continue


def _duration(metadata):
    segments = metadata["segments"]
    metadata["durationMs"] = (
        max(0, segments[-1]["endSampleTimeMs"] - segments[0]["startSampleTimeMs"]) if segments else 0
    )


class _Projection:
    """FIFO projection at the writer cursor, independent of future gateway state."""

    def __init__(self, revision):
        self.store = StateStore(revision)
        self.frame = None
        self.invocations = OrderedDict()
        self.state = None

    @property
    def coherent(self):
        return self.frame is not None and FRAME_HEADER.unpack_from(self.frame.payload)[4] == self.store.epoch

    def apply(self, record, message=None):
        if record.kind == 1:
            self.frame = record
            return
        if record.kind != 2:
            return
        value = message if message is not None else json.loads(record.payload)
        kind = value.get("type")
        if kind == "entity.snapshot":
            if not self.store.apply(EntitySnapshot.model_validate(value)):
                return
            self.invocations.clear()
            for invocation in value["invocations"]:
                self._invocation(invocation)
        elif kind == "entity.observations":
            self.store.apply(EntityObservations.model_validate(value))
        elif kind == "invocation.update":
            self._invocation(value)
        elif kind == "session.state":
            self.state = record

    def _invocation(self, value):
        self.invocations[value["invocationId"]] = {
            key: item for key, item in value.items() if key not in ("type", "sceneRevision")
        }
        if value["status"] in TERMINAL:
            self.invocations.move_to_end(value["invocationId"])
        # Segment prefixes need every active invocation and only the last 50 terminals.
        terminal = [key for key, value in self.invocations.items() if value["status"] in TERMINAL]
        for key in terminal[:-50]:
            del self.invocations[key]

    def snapshots(self, clock):
        frame = self.frame
        payload = (
            frame.payload[:6]
            + b"\x02\x00"
            + frame.payload[8:32]
            + struct.pack("<d", clock)
            + frame.payload[40:]
        )
        spatial = Record(1, clock, payload)
        info = EntitySnapshot(
            sceneRevision=self.store.scene_revision,
            epoch=self.store.epoch,
            sampleTimeMs=clock,
            simTime=FRAME_HEADER.unpack_from(frame.payload)[6],
            entities=list(self.store.entities.values()),
            invocations=list(self.invocations.values()),
        )
        records = [spatial, Record(2, clock, _json_bytes(info.model_dump(mode="json")))]
        if self.state:
            records.append(Record(2, clock, self.state.payload))
        return records


@dataclass
class _Entry:
    record: Record | None = None
    reliable: bool = True
    invocation_id: str | None = None
    revision: tuple | None = None
    gap: dict | None = None


class SessionLog:
    """Nonblocking producer, bounded queue, one disk owner and async history reads."""

    def __init__(
        self,
        directory,
        session_id,
        scene_id,
        *,
        queue_size=4096,
        segment_seconds=60,
        segment_bytes=16 * 1024 * 1024,
        open_segment=None,
    ):
        self.directory = Path(directory)
        self.metadata = {
            "formatVersion": 1,
            "protocolVersion": 2,
            "sessionId": session_id,
            "sceneId": scene_id,
            "createdAt": datetime.now(UTC).isoformat(),
            "status": "recording",
            "segments": [],
            "revisions": {},
            "durationMs": 0,
        }
        self.queue_size, self.segment_seconds, self.segment_bytes = queue_size, segment_seconds, segment_bytes
        self._open_segment = open_segment or (lambda path: path.open("wb"))
        self._condition = threading.Condition()
        self._queue = deque()
        self._closing = False
        self._failure = None
        self._pending = {}
        self._index = {}
        self._gap = None
        self._file = None
        self._segment = None
        self._unflushed = []
        self._flush_bytes = 0
        self._last_flush = time.monotonic()
        self._projection = None
        self._clock = 0
        self.thread = threading.Thread(target=self._run, name=f"recording-{session_id}", daemon=True)
        self.thread.start()

    @property
    def pending_count(self):
        with self._condition:
            return len(self._pending)

    @property
    def queue_depth(self):
        with self._condition:
            return len(self._queue)

    @property
    def error(self):
        return self._failure

    def add_revision(self, manifest, catalog, frame, entity_snapshot, *, state=None, assets=None):
        return self._offer(_Entry(revision=(manifest, catalog, frame, entity_snapshot, state, assets or {})))

    def append_frame(self, payload: bytes, t_ms: float, *, reliable=False):
        return self._offer(_Entry(Record(1, t_ms, payload), reliable))

    def append_json(self, payload: str, t_ms: float, *, invocation_id=None):
        return self._offer(_Entry(Record(2, t_ms, payload.encode()), True, invocation_id))

    def _lost(self, entry):
        ranges = [item for item in (entry.gap, self._gap) if item]
        if entry.record:
            ranges.append(
                {"count": 1, "startSampleTimeMs": entry.record.t_ms, "endSampleTimeMs": entry.record.t_ms}
            )
        self._gap = {
            "count": sum(item["count"] for item in ranges),
            "startSampleTimeMs": min(item["startSampleTimeMs"] for item in ranges),
            "endSampleTimeMs": max(item["endSampleTimeMs"] for item in ranges),
        }

    def _offer(self, entry):
        with self._condition:
            if self._closing:
                return False
            if len(self._queue) >= self.queue_size:
                if not entry.reliable:
                    self._lost(entry)
                    return False
                dropped = next((item for item in self._queue if not item.reliable), None)
                if dropped is None:
                    self._failure = "Reliable recording queue overflow"
                    self._closing = True
                    self._condition.notify()
                    return False
                self._queue.remove(dropped)
                self._lost(dropped)
            entry.gap, self._gap = self._gap, None
            if entry.invocation_id:
                self._pending[entry.invocation_id] = entry.record.payload
            self._queue.append(entry)
            self._condition.notify()
            return True

    async def lookup_invocation(self, invocation_id):
        with self._condition:
            payload = self._pending.get(invocation_id)
            location = self._index.get(invocation_id)
        if payload is None and location is not None:
            payload = await asyncio.to_thread(self._read_payload, location)
        if payload is None:
            return None
        value = json.loads(payload)
        if value.get("invocationId") != invocation_id:
            raise ValueError("Archived invocation identity does not match the requested ID")
        return Invocation.model_validate(
            {key: item for key, item in value.items() if key not in ("type", "sceneRevision")}
        )

    @staticmethod
    def _read_payload(location):
        path, offset, size = location
        with path.open("rb") as source:
            source.seek(offset)
            payload = source.read(size)
        if len(payload) != size:
            raise OSError("Archived invocation record is truncated")
        return payload

    async def close(self):
        with self._condition:
            self._closing = True
            self._condition.notify()
        await asyncio.to_thread(self.thread.join)

    def stop_incomplete(self, reason):
        with self._condition:
            self._failure = str(reason)
            self._closing = True
            self._condition.notify()

    def _run(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=False)
            self._metadata()
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._queue or self._closing, timeout=1)
                    entry = self._queue.popleft() if self._queue else None
                    done = self._closing and not self._queue and entry is None
                if done:
                    break
                if entry is not None:
                    if entry.revision:
                        self._revision(entry.revision)
                    else:
                        self._consume(entry)
                if self._flush_bytes >= 64 * 1024 or time.monotonic() - self._last_flush >= 1:
                    self._flush()
            if self._gap and self._file:
                self._write(Record(3, self._clock, _json_bytes(self._gap)))
            self._close_segment()
        except Exception as exc:
            with self._condition:
                self._failure = str(exc) or type(exc).__name__
                self._closing = True
                self._queue.clear()
                self._pending.clear()
            if self._file:
                try:
                    self._close_segment()
                except OSError:
                    file, self._file = self._file, None
                    if file is not None:
                        with contextlib.suppress(OSError):
                            file.close()
        finally:
            self.metadata["status"] = "incomplete" if self._failure else "complete"
            if self._failure:
                self.metadata["error"] = self._failure
            try:
                self._metadata()
            except OSError:
                pass  # Startup recovers the last atomic 'recording' metadata.

    def _revision(self, revision):
        manifest, catalog, frame, snapshot, state, assets = revision
        self._close_segment()
        key = manifest["sceneRevision"]
        number = manifest["revisionNumericId"]
        first = not self.metadata["revisions"]
        manifest_name = "manifest.json" if first else f"manifest-{number:06d}.json"
        catalog_name = "catalog.json" if first else f"catalog-{number:06d}.json"
        for name, payload in assets.items():
            path = self.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        _atomic_json(self.directory / manifest_name, manifest)
        _atomic_json(self.directory / catalog_name, catalog)
        self.metadata["revisions"][key] = {
            "manifest": manifest_name,
            "catalog": catalog_name,
            "revisionNumericId": number,
        }
        self.metadata.setdefault("sceneRevision", key)
        self._projection = _Projection(key)
        frame_record = Record(1, FRAME_HEADER.unpack_from(frame)[7], frame)
        info_record = Record(2, json.loads(snapshot)["sampleTimeMs"], snapshot.encode())
        self._clock = max(self._clock, frame_record.t_ms, info_record.t_ms)
        self._open_new_segment(key, [frame_record, info_record])
        if state:
            self._write(Record(2, self._clock, state.encode()))
        self._metadata()

    def _consume(self, entry):
        record = entry.record
        if not self._file:
            raise ValueError("Recording requires an initial revision")
        if self._projection.coherent and (
            max(self._clock, record.t_ms) - self._segment["startSampleTimeMs"] >= self.segment_seconds * 1000
            or self._segment["bytes"] >= self.segment_bytes
        ):
            snapshots = self._projection.snapshots(max(self._clock, record.t_ms))
            key = self._projection.store.scene_revision
            self._close_segment()
            self._open_new_segment(key, snapshots)
        if entry.gap:
            self._write(Record(3, record.t_ms, _json_bytes(entry.gap)))
        self._write(record, entry.invocation_id)

    def _open_new_segment(self, revision, records):
        name = f"seg-{len(self.metadata['segments']) + 1:06d}.bin"
        self._file = self._open_segment(self.directory / name)
        self._file.write(FILE_HEADER)
        self._segment = {
            "file": name,
            "sceneRevision": revision,
            "startSampleTimeMs": records[0].t_ms,
            "endSampleTimeMs": records[0].t_ms,
            "startSimTime": _sim_time(records[0]),
            "endSimTime": _sim_time(records[0]),
            "bytes": 6,
        }
        self.metadata["segments"].append(self._segment)
        for record in records:
            self._write(record)

    def _write(self, record, invocation_id=None):
        offset = self._file.tell()
        self._file.write(record.encode())
        size = 4 + 9 + len(record.payload)
        self._flush_bytes += size
        self._segment["bytes"] += size
        self._clock = max(self._clock, record.t_ms)
        self._segment["endSampleTimeMs"] = self._clock
        message = json.loads(record.payload) if record.kind == 2 else None
        sim_time = (
            FRAME_HEADER.unpack_from(record.payload)[6]
            if record.kind == 1
            else message.get("simTime")
            if message is not None
            else None
        )
        if sim_time is not None:
            self._segment["endSimTime"] = sim_time
        self._projection.apply(record, message)
        if invocation_id:
            self._unflushed.append(
                (
                    invocation_id,
                    record.payload,
                    (
                        self.directory / self._segment["file"],
                        offset + RECORD_HEADER.size,
                        len(record.payload),
                    ),
                )
            )

    def _flush(self):
        if self._file:
            self._file.flush()
        with self._condition:
            for key, payload, location in self._unflushed:
                self._index[key] = location
                if self._pending.get(key) is payload:
                    self._pending.pop(key)
            self._unflushed.clear()
        self._last_flush, self._flush_bytes = time.monotonic(), 0

    def _close_segment(self):
        if self._file:
            self._flush()
            self._file.close()
            self._file = None
            self._metadata()

    def _metadata(self):
        _duration(self.metadata)
        _atomic_json(self.directory / "recording.json", self.metadata)

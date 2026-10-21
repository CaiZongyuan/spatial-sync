"""A continuously reopened information recording with one bounded disk owner."""

import asyncio
import json
import os
import re
import time
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.info.log import (
    Record,
    SessionLog,
    _atomic_json,
    _Entry,
    _json_bytes,
    _Projection,
    recover_recording,
)
from packages.info.recording import recording_metadata
from packages.protocol.schema import EntitySnapshot


class _LiveProjection(_Projection):
    @property
    def coherent(self):
        return True

    def snapshots(self, clock):
        info = EntitySnapshot(
            sceneRevision=self.store.scene_revision, epoch=self.store.epoch,
            sampleTimeMs=clock, simTime=self.sim_time,
            entities=list(self.store.entities.values()), invocations=list(self.invocations.values()),
        )
        records = [Record(2, clock, _json_bytes(info.model_dump(mode="json")))]
        for full in self.plans.values():
            records.append(Record(2, clock, _json_bytes(
                full | {"sampleTimeMs": clock, "simTime": info.simTime, "epoch": self.store.epoch}
            )))
        return records

    def apply(self, record, message=None):
        value = message if message is not None else json.loads(record.payload)
        self.sim_time = value.get("simTime", getattr(self, "sim_time", 0))
        super().apply(record, value)


class LiveLog(SessionLog):
    """UTC partitions are assigned on admission; timestamps remain gateway clocks."""

    def __init__(self, directory, recording_id, live_name, *, state_directory, retention_days=7,
                 utc_clock=lambda: datetime.now(UTC), clock=time.monotonic, **kwargs):
        if type(retention_days) is not int or retention_days < 1:
            raise ValueError("Live retention days must be a positive integer")
        self._utc_clock, self.retention_days = utc_clock, retention_days
        self.state_directory = Path(state_directory)
        self._raw_clock, self._offset_ms = clock, 0
        self._ready = Future()
        self._owner, self._prepared = None, False
        self._live_state = {"version": 1, "nextSegment": 1, "nextRevision": 1, "days": {}}
        self._day = None
        self._readers = 0
        self._maintenance_at = None
        kwargs.setdefault("open_segment", lambda path: path.open("xb"))
        super().__init__(directory, recording_id, live_name, scene_version=None,
                         scene_content_hash=None, kind="live", **kwargs)

    async def ready(self):
        await asyncio.shield(asyncio.wrap_future(self._ready))

    def map_time(self, raw_ms):
        """Map raw gateway-receive milliseconds once, without changing their scale."""
        return raw_ms + self._offset_ms

    def sample_time_ms(self):
        return self.map_time(self._raw_clock() * 1000)

    def maintain(self):
        now = self._utc_clock()
        if (self._maintenance_at is None or now.date() != self._maintenance_at.date()
                or (now - self._maintenance_at).total_seconds() >= 60):
            self._maintenance_at = now
            return self._offer(_Entry())
        return True

    def _offer(self, entry):
        with self._condition:
            entry.admitted_day = self._utc_clock().astimezone(UTC).date().isoformat()
            if entry.revision:
                entry.revision = (*entry.revision, entry.admitted_day)
            return super()._offer(entry)

    def add_revision(self, catalog, entity_snapshot):
        return self._offer(_Entry(revision=(catalog, entity_snapshot)))

    def append_frame(self, *args, **kwargs):
        raise ValueError("Live recordings do not have a spatial stream")

    def _prepare(self):
        if self.directory.is_symlink():
            raise ValueError("Live recording directory cannot be a symbolic link")
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.state_directory.is_symlink():
            raise ValueError("Live state directory cannot be a symbolic link")
        self.state_directory.mkdir(parents=True, exist_ok=True)
        lock_path = self.state_directory / "owner.lock"
        if lock_path.is_symlink():
            raise ValueError("Live recording lock cannot be a symbolic link")
        self._owner = lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._owner.seek(0)
                msvcrt.locking(self._owner.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._owner.close()
            self._owner = None
            raise OSError("Live recording already has an active owner") from None
        metadata_path = self.directory / "recording.json"
        if metadata_path.exists():
            previous = recording_metadata(json.loads(metadata_path.read_bytes()))
            if (previous["kind"], previous["sessionId"], previous["sceneId"]) != (
                "live", self.metadata["sessionId"], self.metadata["sceneId"],
            ):
                raise ValueError("Live recording identity does not match")
            self.metadata = previous
            self._live_state = json.loads((self.state_directory / "recording.json").read_bytes())
            if (self._live_state["version"] != 1
                    or type(self._live_state["nextSegment"]) is not int
                    or self._live_state["nextSegment"] < 1
                    or type(self._live_state["nextRevision"]) is not int
                    or self._live_state["nextRevision"] < 1
                    or not isinstance(self._live_state["days"], dict)):
                raise ValueError("Invalid Live recording ownership state")
            for name, day in self._live_state["days"].items():
                if not re.fullmatch(r"seg-[0-9]{6}\.bin", name):
                    raise ValueError("Invalid Live segment ownership")
                datetime.strptime(day, "%Y-%m-%d")
            recover_recording(self.directory, allowed_segments=self._live_state["days"])
            self.metadata = recording_metadata(json.loads(metadata_path.read_bytes()))
            self._clock = max((item["endSampleTimeMs"] for item in self.metadata["segments"]), default=0)
        allocated = [int(path.stem[4:]) for path in self.directory.glob("seg-*.bin")
                     if re.fullmatch(r"seg-[0-9]{6}\.bin", path.name)]
        self._live_state["nextSegment"] = max(self._live_state["nextSegment"], max(allocated, default=0) + 1)
        raw_ms = self._raw_clock() * 1000
        previous = self._live_state.get("clock")
        if previous is not None:
            elapsed = max(0, self._utc_clock().timestamp() * 1000 - previous["utcMs"])
            self._offset_ms = max(raw_ms, self._clock + 1, previous["sampleTimeMs"] + elapsed) - raw_ms
        self.metadata["status"] = "recording"
        self.metadata.pop("error", None)
        self._prepared = True
        self._metadata()
        self._ready.set_result(None)

    def _finished(self):
        if not self._ready.done():
            self._ready.set_exception(OSError(self.error or "Live recording did not start"))
        if self._owner is not None:
            # A cancelled HTTP task can leave an actual disk reader running.
            # Keep exclusive ownership until that reader releases its own pin.
            with self._condition:
                self._condition.wait_for(lambda: self._readers == 0)
            self._owner.close()
            self._owner = None

    def _revision(self, revision):
        catalog, snapshot, day = revision
        snapshot = EntitySnapshot.model_validate_json(snapshot)
        key = snapshot.sceneRevision
        if catalog["sceneRevision"] != key or key in self.metadata["revisions"]:
            raise ValueError("Live recording needs a new matching catalog revision")
        self._close_segment()
        self._day = day
        number = self._live_state["nextRevision"]
        self._live_state["nextRevision"] += 1
        self._save_state()
        name = "catalog.json" if number == 1 else f"catalog-{number:06d}.json"
        _atomic_json(self.directory / name, catalog)
        self.metadata["revisions"][key] = {"catalog": name, "revisionNumericId": number}
        self.metadata.setdefault("sceneRevision", key)
        self._projection = _LiveProjection(key)
        self._clock = max(self._clock, snapshot.sampleTimeMs)
        # A valid segment prefix must never reach disk before its catalog
        # attribution is durable in the public recording document.
        self._metadata()
        self._open_new_segment(key, [Record(2, snapshot.sampleTimeMs, snapshot.model_dump_json().encode())])
        self._metadata()
        self._prune()

    def _consume(self, entry):
        if self._file is None:
            raise ValueError("Live recording requires an initial catalog revision")
        if entry.admitted_day != self._day or self._segment["bytes"] >= self.segment_bytes:
            clock = entry.record.t_ms if entry.record is not None else self.sample_time_ms()
            snapshots = self._projection.snapshots(max(self._clock, clock))
            self._close_segment()
            self._day = entry.admitted_day
            self._open_new_segment(self._projection.store.scene_revision, snapshots)
        if entry.record is not None:
            self._write(entry.record, entry.invocation_id)
        self._prune()

    def _next_segment_name(self):
        number = self._live_state["nextSegment"]
        if number > 999999:
            raise ValueError("Live recording segment number limit reached")
        name = f"seg-{number:06d}.bin"
        if (self.directory / name).exists():
            raise ValueError("Live recording segment already exists")
        self._live_state["nextSegment"] += 1
        self._live_state["days"][name] = self._day
        # Persist the allocation/day before opening; a crash must never reuse a number.
        self._save_state()
        return name

    @contextmanager
    def reader(self):
        """Pin before requesting a checkpoint, through completion of its actual file reads."""
        with self._condition:
            self._readers += 1
        try:
            yield
        finally:
            with self._condition:
                self._readers -= 1
                self._condition.notify_all()

    async def read_pinned(self, callback, *args):
        """The executor job owns its pin, even when its requester is cancelled."""
        pin = self.reader()
        pin.__enter__()

        def read():
            try:
                return callback(*args)
            finally:
                pin.__exit__(None, None, None)

        try:
            # Submit synchronously: there is no cancellable coroutine between
            # acquiring the pin and starting the independent executor job.
            reading = asyncio.get_running_loop().run_in_executor(None, read)
        except BaseException:
            pin.__exit__(None, None, None)
            raise
        # Retrieve background failures if the request has already gone away.
        reading.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(reading)

    async def lookup_invocation(self, invocation_id):
        with self.reader():
            return await super().lookup_invocation(invocation_id)

    async def _read_archived_payload(self, location):
        return await self.read_pinned(self._read_payload, location)

    def _prune(self):
        cutoff = (self._utc_clock().astimezone(UTC).date()
                  - timedelta(days=self.retention_days - 1)).isoformat()
        with self._condition:
            if self._readers:
                return
            queued_days = {entry.admitted_day for entry in self._queue if entry.admitted_day}
        expired = [segment for segment in self.metadata["segments"]
                   if segment is not self._segment
                   and (day := self._live_state["days"].get(segment["file"])) is not None
                   and day < cutoff and day not in queued_days]
        if not expired:
            return
        names = {segment["file"] for segment in expired}
        self.metadata["segments"] = [segment for segment in self.metadata["segments"]
                                     if segment["file"] not in names]
        for name in names:
            self._live_state["days"].pop(name, None)
        # This retained-file allowlist also prevents crash recovery from
        # resurrecting a pruned file whose physical unlink was interrupted.
        self._save_state()
        # Publish the smaller discoverable set before deleting expired files.
        self._metadata()
        for segment in expired:
            for name in (segment["file"], segment.get("propertyIndex")):
                if name:
                    (self.directory / name).unlink(missing_ok=True)
        with self._condition:
            self._index = {key: location for key, location in self._index.items()
                           if location[0].name not in names}
        self._save_state()

    def _save_state(self):
        self._live_state["clock"] = {
            "sampleTimeMs": max(self._clock, self.sample_time_ms()),
            "utcMs": self._utc_clock().timestamp() * 1000,
        }
        _atomic_json(self.state_directory / "recording.json", self._live_state)

    def _metadata(self):
        if self._prepared:
            if not self.error:
                self.metadata["status"] = "recording"
            self._save_state()
            super()._metadata()

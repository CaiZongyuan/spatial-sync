"""Laboratory recording policy: session counts and continuous Live ownership."""

import asyncio
import shutil
from hashlib import sha256
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import Response

from apps.api.app.recordings import RecordingRepository
from packages.info.live_log import LiveLog
from packages.info.log import _json_bytes, recover_recording


class LiveRecordingRepository(RecordingRepository):
    def __init__(self, settings, asset_registry):
        super().__init__(settings, asset_registry)
        self.live_state_root = Path(settings.state_root) / "lives"
        self.live_retention_days = settings.live_retention_days
        self._live_logs = {}

    async def startup(self):
        # A LiveLog acquires its own exclusive owner before recovering that lab.
        # Session startup must never recover or count-retain an active LiveLog.
        for value in await asyncio.to_thread(self.list):
            if value["kind"] == "session":
                await asyncio.to_thread(recover_recording, self.root / value["sessionId"])
        await asyncio.to_thread(self.retain)

    def retain(self):
        values = [item for item in self.list() if item["kind"] == "session"
                  and item.get("status") in ("complete", "incomplete")]
        for value in values[self.keep:]:
            directory = self.root / value["sessionId"]
            if not directory.is_symlink() and directory.resolve().parent == self.root:
                shutil.rmtree(directory)

    def create_live(self, live_name, *, clock=None):
        if not self.enabled:
            return None
        identity = "live-" + sha256(live_name.encode()).hexdigest()[:24]
        previous = self._live_logs.get(identity)
        if previous is not None and previous.thread.is_alive():
            raise OSError("Live recording already has an active owner")
        log = LiveLog(self.root / identity, identity, live_name,
                      state_directory=self.live_state_root / identity,
                      retention_days=self.live_retention_days,
                      **({"clock": clock} if clock is not None else {}))
        self._live_logs[identity] = log
        return log

    @staticmethod
    def _read_prefix(path, size):
        with path.open("rb") as stream:
            payload = stream.read(size)
        if len(payload) != size:
            raise OSError("Live recording checkpoint is truncated")
        return payload

    async def read_response(self, recording_id, relative):
        log = self._live_logs.get(recording_id)
        if log is None or not log.thread.is_alive():
            return await super().read_response(recording_id, relative)
        try:
            with log.reader():
                metadata = await log.checkpoint()
                if relative == "recording.json":
                    payload = _json_bytes(metadata)
                else:
                    allowed = {item["catalog"] for item in metadata["revisions"].values()}
                    for segment in metadata["segments"]:
                        allowed.add(segment["file"])
                        if segment.get("propertyIndex"):
                            allowed.add(segment["propertyIndex"])
                    if relative not in allowed:
                        raise HTTPException(404, {"code": "recording_missing"})
                    path = await asyncio.to_thread(self.resolve, recording_id, relative)
                    segment = next((item for item in metadata["segments"] if item["file"] == relative), None)
                    payload = (await log.read_pinned(self._read_prefix, path, segment["bytes"])
                               if segment is not None else await log.read_pinned(path.read_bytes))
            return Response(payload, media_type="application/json" if relative.endswith(".json")
                            else "application/octet-stream", headers={"Cache-Control": "no-cache"})
        except OSError as error:
            raise HTTPException(503, {"code": "recording_unavailable",
                                      "message": "Live recording is unavailable"}) from error

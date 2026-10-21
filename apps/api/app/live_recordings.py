"""Laboratory recording policy: session counts and continuous Live ownership."""

import asyncio
import shutil
from hashlib import sha256
from pathlib import Path

from apps.api.app.recordings import RecordingRepository
from packages.info.live_log import LiveLog
from packages.info.log import recover_recording


class LiveRecordingRepository(RecordingRepository):
    def __init__(self, settings, asset_registry):
        super().__init__(settings, asset_registry)
        self.live_state_root = Path(settings.state_root) / "lives"
        self.live_retention_days = settings.live_retention_days

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
        return LiveLog(self.root / identity, identity, live_name,
                       state_directory=self.live_state_root / identity,
                       retention_days=self.live_retention_days,
                       **({"clock": clock} if clock is not None else {}))

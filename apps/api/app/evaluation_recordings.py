"""Gateway retention policy for recordings retained by embodied evaluations."""

import asyncio
import json
import shutil

from apps.api.app.recordings import RECORDING_ID, RecordingRepository
from packages.library.transactions import durable_json


class EvaluationRecordingRepository(RecordingRepository):
    def __init__(self, settings, asset_registry):
        super().__init__(settings, asset_registry)
        self.pinned = set()

    async def startup(self):
        path = self.root / ".evaluation-pins.json"
        if path.exists():
            pins = json.loads(await asyncio.to_thread(path.read_bytes))
            if not isinstance(pins, list) or any(
                not isinstance(value, str) or not RECORDING_ID.fullmatch(value) for value in pins
            ):
                raise ValueError("Invalid evaluation recording pins")
            self.pinned = set(pins)
        # Recovery also prunes: load durable ownership before delegating startup.
        await super().startup()

    def pin(self, session_id):
        self.pinned.add(session_id)
        durable_json(self.root / ".evaluation-pins.json", sorted(self.pinned))

    def retain(self):
        values = [
            item
            for item in self.list()
            if item.get("status") in ("complete", "incomplete") and item["sessionId"] not in self.pinned
        ]
        for value in values[self.keep :]:
            directory = self.root / value["sessionId"]
            if directory.is_symlink() or directory.resolve().parent != self.root:
                continue
            shutil.rmtree(directory)

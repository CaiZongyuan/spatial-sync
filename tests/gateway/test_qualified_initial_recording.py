"""Normal Session recording retains genuine Host init events queued before Ready.

The carrier is SyntheticSource; this tests recording transport, not Newton physics.
"""

import json

from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.log import read_segment
from tests.fixtures.qualified_host import build_qualified_host


def test_pre_ready_initial_anchor_events_reach_normal_session_recording(tmp_path):
    (tmp_path / "fixture").mkdir()
    host, _ = build_qualified_host(tmp_path / "fixture")
    initial = [e for e in host.drain_events() if getattr(e, "event", None) == "constraintChanged"]
    assert len(initial) == 2

    class InitialCarrier(SyntheticSource):
        async def start(self, scene_id, options):
            manifest = await super().start(scene_id, options)
            for event in initial:
                self._events.put_nowait(
                    event.model_copy(update={"sceneRevision": self.manifest.sceneRevision})
                )
            return manifest

    root = tmp_path / "recordings"
    app = create_app(Settings(recordings_root=root), source_factory=lambda kind, sid: InitialCarrier(sid))
    with TestClient(app) as client:
        response = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
        assert response.status_code == 201, response.text
        sid = response.json()["sessionId"]
        assert client.delete(f"/api/v1/sessions/{sid}").status_code == 204
    metadata = json.loads((root / sid / "recording.json").read_bytes())
    messages = [
        json.loads(row.payload)
        for segment in metadata["segments"]
        for row in read_segment((root / sid / segment["file"]).read_bytes()).records
        if row.kind == 2
    ]
    recorded = [row for row in messages if row.get("event") == "constraintChanged"]
    assert [row["data"] for row in recorded] == [event.data for event in initial]
    assert all(row["simTime"] == 0 for row in recorded)

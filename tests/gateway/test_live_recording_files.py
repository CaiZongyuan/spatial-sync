"""Real ASGI recording delivery while the actual disk writer continues."""

import asyncio
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from fastapi import FastAPI

from apps.api.app.live_recordings import LiveRecordingRepository
from apps.api.app.recordings import install_recording_routes
from apps.api.app.settings import Settings
from packages.info.live_log import LiveLog
from packages.info.log import read_segment
from packages.live.bindings import live_catalog, load_bindings


async def test_live_file_body_is_the_checkpoint_prefix_even_when_writer_grows_after_headers(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr("apps.api.app.live_recordings.LiveLog", partial(
        LiveLog, utc_clock=lambda: datetime(2026, 10, 21, 12, tzinfo=UTC)))
    repository = LiveRecordingRepository(Settings(
        state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"), None)
    log = repository.create_live("file-lab", clock=lambda: 1)
    await log.ready()
    revision, key = "live-file-revision", "/Real/Incubator2"
    catalog = live_catalog(load_bindings(Path(__file__).parents[2] / "devices/live/lab.yaml"), revision)
    snapshot = {
        "type": "entity.snapshot", "sceneRevision": revision, "epoch": 0,
        "simTime": 0, "sampleTimeMs": 1000, "invocations": [],
        "entities": [{"key": key, "rev": 1, "values": {"temperatureC": 37}, "meta": {
            "temperatureC": {"simTime": 0, "sampleTimeMs": 1000, "source": "device"},
        }}],
    }
    log.add_revision(catalog.model_dump(mode="json"), json.dumps(snapshot))
    before = await log.checkpoint()
    segment = before["segments"][0]
    app = FastAPI()
    install_recording_routes(app, repository)
    headers_sent, release_body = asyncio.Event(), asyncio.Event()
    response_headers, chunks, statuses = {}, [], []
    path = f"/recordings/{log.metadata['sessionId']}/{segment['file']}"

    async def send(message):
        if message["type"] == "http.response.start":
            response_headers.update(dict(message["headers"]))
            statuses.append(message["status"])
            headers_sent.set()
            await release_body.wait()
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    async def receive():
        return {"type": "http.disconnect"}

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "GET", "scheme": "http", "root_path": "",
        "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [],
        "server": ("local", 80), "client": ("local", 1),
    }
    response = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(headers_sent.wait(), 2)
        observation = {
            "type": "entity.observations", "sceneRevision": revision, "epoch": 0,
            "simTime": 1, "sampleTimeMs": 2000, "source": "device",
            "changes": [{"key": key, "rev": 2, "values": {"temperatureC": 38}}],
        }
        log.append_json(json.dumps(observation), 2000)
        after = await log.checkpoint()
        assert after["segments"][0]["bytes"] > segment["bytes"]
        release_body.set()
        await asyncio.wait_for(response, 2)
        body = b"".join(chunks)
        assert statuses == [200]
        assert len(body) == int(response_headers[b"content-length"]) == segment["bytes"]
        records = read_segment(body)
        assert not records.truncated
        assert [json.loads(record.payload)["type"] for record in records.records] == ["entity.snapshot"]
        assert json.loads(records.records[0].payload)["entities"][0]["values"]["temperatureC"] == 37
    finally:
        release_body.set()
        await response
        await log.close()

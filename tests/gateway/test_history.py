"""Independent raw measurements queried through the public HTTP history API."""

import json
import shutil
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.info.log import SessionLog
from packages.protocol.schema import TransformFrame
from packages.transport import FrameCodec

KEY = "/sensor"


def snapshot(t, value, epoch=0):
    return {"type": "entity.snapshot", "sceneRevision": "rev-history", "epoch": epoch,
            "simTime": 0, "sampleTimeMs": t, "invocations": [], "entities": [
                {"key": KEY, "rev": 1, "values": {"temperature": value, "label": "idle"},
                 "meta": {prop: {"simTime": 0, "sampleTimeMs": t, "source": "virtual"}
                          for prop in ("temperature", "label")}}]}


def observation(t, value, rev, *, epoch=0, meta=None):
    return {"type": "entity.observations", "sceneRevision": "rev-history", "epoch": epoch,
            "simTime": 0, "sampleTimeMs": t, "source": "virtual", "changes": [
                {"key": KEY, "rev": rev, "values": {"temperature": value}, "meta": meta or {}}]}


async def recording(root, *, count=None, reset=False, repeated_time=False, reload=False, stale_prefix=False):
    log = SessionLog(root / "history-test", "history-test", "lab-demo", scene_version="lab-demo@1",
                     scene_content_hash="a" * 64, kind="session", segment_seconds=0.6)
    frame = FrameCodec("history-test", "rev-history", 1).encode(TransformFrame(
        type="stream.snapshot", sessionId="history-test", sceneRevision="rev-history", epoch=0,
        seq=1, simTime=0, sampleTimeMs=1000, poses=[(1, 0, 0, 0, 0, 0, 0, 1)]))
    initial = snapshot(1000, 2)
    if stale_prefix:
        initial["entities"][0]["meta"]["temperature"]["stale"] = True
    log.add_revision({"sceneRevision": "rev-history", "revisionNumericId": 1}, {}, frame, json.dumps(initial))
    rows = ([observation(1000 + i, i, i + 1) for i in range(1, count)] if count else [
        observation(1100, 4, 2), observation(1400, 10, 3),
        observation(1450, 777, 1),  # Rejected older entity revision.
        observation(1500, 10, 4, meta={"temperature": {
            "simTime": 0, "sampleTimeMs": 1400, "source": "virtual", "stale": True}}),
        observation(1600, 888, 5, epoch=1),  # No snapshot establishes this epoch.
        observation(1800, 8, 5), observation(1700, 999, 6),  # Rejected older property time.
        observation(2100, 6, 7),
    ])
    for row in rows:
        assert log.append_json(json.dumps(row), row["sampleTimeMs"])
    if reset:
        for row in [snapshot(2200, 20, epoch=1), observation(2300, 40, 2, epoch=1)]:
            assert log.append_json(json.dumps(row), row["sampleTimeMs"])
    if repeated_time:
        batch = observation(2200, 1, 8)
        batch["changes"] += [observation(2200, value, rev)["changes"][0]
                             for rev, value in [(7, 777), (9, 2), (10, 1)]]
        assert log.append_json(json.dumps(batch), 2200)
    if reload:
        revision = "rev-reloaded"
        new_frame = FrameCodec("history-test", revision, 2).encode(TransformFrame(
            type="stream.snapshot", sessionId="history-test", sceneRevision=revision, epoch=0,
            seq=1, simTime=0, sampleTimeMs=2400, poses=[(1, 0, 0, 0, 0, 0, 0, 1)]))
        log.add_revision({"sceneRevision": revision, "revisionNumericId": 2}, {}, new_frame,
                         json.dumps(snapshot(2400, 100) | {"sceneRevision": revision}))
        assert log.append_json(json.dumps(observation(2500, 200, 2) | {"sceneRevision": revision}), 2500)
    await log.close()
    assert log.error is None
    return root / "history-test"


@pytest.mark.parametrize("mode", ["index", "missing", "corrupt", "wrong-segment", "recovered",
                                  "stale-prefix", "stale-prefix-scan"])
async def test_index_old_scan_corrupt_and_recovered_query_the_same_measurements(tmp_path, mode):
    directory = await recording(tmp_path, stale_prefix=mode.startswith("stale-prefix"))
    metadata = json.loads((directory / "recording.json").read_bytes())
    assert len(metadata["segments"]) >= 2
    for segment in metadata["segments"]:
        sidecar = directory / segment["propertyIndex"]
        assert sidecar.is_file()
        if mode in ("missing", "stale-prefix-scan"):
            sidecar.unlink()
        elif mode == "corrupt":
            sidecar.write_text('{"properties": []}')
        elif mode == "wrong-segment":
            sidecar.write_bytes((directory / metadata["segments"][-1]["propertyIndex"]).read_bytes())
    if mode == "recovered":
        metadata.update(status="recording", segments=[])
        (directory / "recording.json").write_text(json.dumps(metadata))
        with (directory / "seg-000002.bin").open("ab") as target:
            target.write(struct.pack("<I", 100) + b"\x01\x00")
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        params = {"key": KEY, "property": "temperature", "recording": "history-test", "since": "0"}
        result = client.get("/api/v1/history", params=params)
        assert result.status_code == 200, result.text
        assert result.json() == [[1000, 2], [1100, 4], [1400, 10], [1800, 8], [2100, 6]]
        assert result.headers["x-history-truncated"] == "false"
        assert float(result.headers["x-history-until-ms"]) == 2100
        for agg, values in {"mean": [16 / 3, 8, 6], "last": [10, 8, 6],
                            "min": [2, 8, 6], "max": [10, 8, 6]}.items():
            response = client.get("/api/v1/history", params=params | {
                "since": "1000", "until": "2100", "every": "500ms", "agg": agg})
            assert response.status_code == 200, response.text
            assert [point[0] for point in response.json()] == [1400, 1800, 2100]
            assert [point[1] for point in response.json()] == pytest.approx(values)
        assert client.get("/api/v1/history", params=params | {"since": "1s"}).json() == [
            [1100, 4], [1400, 10], [1800, 8], [2100, 6]]
        assert client.get("/api/v1/history", params=params | {"property": "missing"}).json() == []
        assert client.get("/api/v1/history", params=params | {
            "property": "label", "every": "1m"}).status_code == 422


@pytest.mark.parametrize("version", [1, 2])
def test_published_recordings_remain_queryable_with_original_measurement_clock(tmp_path, version):
    source = Path(__file__).parents[1] / f"fixtures/recordings/v{version}"
    metadata = json.loads((source / "recording.json").read_bytes())
    sid = metadata["sessionId"]
    shutil.copytree(source, tmp_path / sid)
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        result = client.get("/api/v1/history", params={
            "key": "/World/Lab/Tubes/Tube01", "property": "location", "recording": sid,
            "since": "1h", "every": "1ms", "agg": "last"})
        assert result.status_code == 200, result.text
        points = result.json()
        assert points[0][0] > 1_000_000  # Original monotonic clock, no wall-clock conversion.
        assert points[0][1] == "/World/Lab/Rack/A1"
        assert points[-1][1] == "/World/Lab/Centrifuge/Rotor/Slot1"


async def test_cap_explains_first_points_and_can_be_resolved_by_downsampling(tmp_path):
    await recording(tmp_path, count=2002)
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        params = {"key": KEY, "property": "temperature", "recording": "history-test", "since": "0"}
        result = client.get("/api/v1/history", params=params)
        assert len(result.json()) == 2000
        assert result.json()[-1] == [2999, 1999]
        assert result.headers["x-history-truncated"] == "true"
        assert result.headers["x-history-limit"] == "2000"
        assert "first 2000" in result.headers["x-history-explanation"]
        sampled = client.get("/api/v1/history", params=params | {"every": "1s", "agg": "max"})
        assert sampled.json() == [[1999, 999], [2999, 1999], [3001, 2001]]
        assert sampled.headers["x-history-truncated"] == "false"


def test_active_query_flushes_admitted_samples_and_retained_query_survives_stop(tmp_path):
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        params = {"key": "/World/Lab/Centrifuge", "property": "lidOpen", "since": "1h"}
        assert client.get("/api/v1/history", params=params).status_code == 409
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        sid = session["sessionId"]
        route = "/api/v1/sessions/" + sid
        action = client.post(route + "/actions", json={
            "requestId": "history-close", "entityKey": params["key"], "action": "closeLid", "wait": True})
        assert action.json()["status"] == "succeeded"
        result = client.get("/api/v1/history", params=params)
        assert result.status_code == 200, result.text
        current = client.get(route + "/entity", params={"key": params["key"]}).json()["state"]
        assert result.json()[-1] == [current["meta"]["lidOpen"]["sampleTimeMs"], False]
        client.delete(route).raise_for_status()
        retained = client.get("/api/v1/history", params=params | {"recording": sid})
        assert retained.json() == result.json()
        assert client.get("/api/v1/history", params=params).status_code == 409
        assert client.get("/api/v1/history", params=params | {"recording": "missing"}).status_code == 404
        live = client.get("/api/v1/history", params=params | {"key": "/Real/Incubator2"})
        assert live.status_code == 503 and live.json()["detail"]["code"] == "live_unavailable"


async def test_sidecar_failure_does_not_fail_recording_and_scan_remains_correct(tmp_path, monkeypatch):
    def fail(path):
        raise OSError("Injected optional sidecar failure")

    monkeypatch.setattr("packages.info.history.write_property_index", fail)
    directory = await recording(tmp_path)
    metadata = json.loads((directory / "recording.json").read_bytes())
    assert metadata["status"] == "complete"
    assert all("propertyIndex" not in segment for segment in metadata["segments"])
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        result = client.get("/api/v1/history", params={"key": KEY, "property": "temperature",
                                                      "recording": "history-test", "since": "1h"})
        assert result.json() == [[1000, 2], [1100, 4], [1400, 10], [1800, 8], [2100, 6]]


async def test_reset_splits_aggregation_context_and_retains_original_monotonic_time(tmp_path):
    await recording(tmp_path, reset=True)
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        result = client.get("/api/v1/history", params={"key": KEY, "property": "temperature",
                                                      "recording": "history-test", "since": "2000",
                                                      "until": "2400", "every": "1h", "agg": "mean"})
        assert result.json() == [[2100, 6], [2300, 30]]


@pytest.mark.parametrize("indexed", [True, False])
async def test_equal_timestamp_observations_keep_fifo_last_even_when_value_repeats(tmp_path, indexed):
    directory = await recording(tmp_path, repeated_time=True)
    if not indexed:
        for sidecar in directory.glob("*.index.json"):
            sidecar.unlink()
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        params = {"key": KEY, "property": "temperature", "recording": "history-test", "since": "2200"}
        assert client.get("/api/v1/history", params=params).json() == [[2200, 1], [2200, 2], [2200, 1]]
        last = client.get("/api/v1/history", params=params | {"every": "1m", "agg": "last"})
        assert last.json() == [[2200, 1]]


async def test_reload_revisions_split_buckets_without_using_reset_simulation_time(tmp_path):
    await recording(tmp_path, reload=True)
    with TestClient(create_app(Settings(recordings_root=tmp_path))) as client:
        result = client.get("/api/v1/history", params={"key": KEY, "property": "temperature",
                                                      "recording": "history-test", "since": "2000",
                                                      "every": "1h", "agg": "mean"})
        assert result.json() == [[2100, 6], [2500, 150]]

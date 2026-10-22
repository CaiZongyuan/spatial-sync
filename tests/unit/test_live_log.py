"""Live segments exercise ordinary framing, recovery and history consumers."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from packages.info.history import read_samples
from packages.info.live_log import LiveLog
from packages.info.log import FILE_HEADER, Record, read_segment, recover_recordings
from packages.live.clock import public_message
from packages.protocol.schema import EntitySnapshot


def snapshot(at=1000):
    return {
        "type": "entity.snapshot", "sceneRevision": "live-first", "epoch": 0,
        "sampleTimeMs": at, "simTime": 0,
        "entities": [{"key": "/Real/Incubator2", "rev": 1, "values": {"temperatureC": 37},
                      "meta": {"temperatureC": {"sampleTimeMs": 1000, "simTime": 0,
                                                "source": "device"}}}],
        "invocations": [],
    }


def message_record(value):
    return Record(2, value["sampleTimeMs"], json.dumps(value).encode()).encode()


def live_metadata(directory):
    value = {
        "formatVersion": 2, "protocolVersion": 2, "kind": "live", "sceneId": None,
        "sessionId": directory.name, "sceneVersion": None, "sceneContentHash": None,
        "createdAt": "2026-10-21T23:59:59+00:00", "status": "recording", "segments": [],
        "revisions": {"live-first": {"catalog": "catalog.json", "revisionNumericId": 1}},
    }
    (directory / "recording.json").write_text(json.dumps(value))
    return value


def test_live_recovery_keeps_json_only_snapshot_and_interrupted_tail(tmp_path):
    directory = tmp_path / "live-main-lab"
    directory.mkdir()
    live_metadata(directory)
    first = FILE_HEADER + message_record(snapshot())
    second = FILE_HEADER + message_record(snapshot(2000)) + message_record({
        "type": "entity.observations", "sceneRevision": "live-first", "epoch": 0,
        "sampleTimeMs": 2000, "simTime": 1, "source": "device",
        "changes": [{"key": "/Real/Incubator2", "rev": 2, "values": {"temperatureC": 38}}],
    })
    (directory / "seg-000001.bin").write_bytes(first)
    (directory / "seg-000002.bin").write_bytes(second + message_record(snapshot(3000))[:10])

    recover_recordings(tmp_path)

    recovered = json.loads((directory / "recording.json").read_bytes())
    assert recovered["status"] == "incomplete"
    assert [segment["bytes"] for segment in recovered["segments"]] == [len(first), len(second)]
    assert recovered["durationMs"] == 1000
    samples = read_samples(directory, recovered, "/Real/Incubator2", "temperatureC", 0, 3000)
    assert [(sample.t_ms, sample.value) for sample in samples] == [(1000, 37), (2000, 38)]


@pytest.mark.parametrize("prefix", [b"", message_record({
    "type": "entity.observations", "sceneRevision": "live-first", "epoch": 0,
    "sampleTimeMs": 1000, "simTime": 0, "source": "device", "changes": [],
})])
def test_live_recovery_rejects_a_segment_without_its_snapshot(tmp_path, prefix):
    directory = tmp_path / "live-main-lab"
    directory.mkdir()
    live_metadata(directory)
    (directory / "seg-000001.bin").write_bytes(FILE_HEADER + prefix)
    recover_recordings(tmp_path)
    assert json.loads((directory / "recording.json").read_bytes())["segments"] == []
    assert read_segment((directory / "seg-000001.bin").read_bytes()).valid_bytes == 6 + len(prefix)


def test_live_recovery_accepts_a_real_snapshot_with_no_entities(tmp_path):
    directory = tmp_path / "live-main-lab"
    directory.mkdir()
    live_metadata(directory)
    raw = FILE_HEADER + message_record(snapshot() | {"entities": []})
    (directory / "seg-000001.bin").write_bytes(raw)
    recover_recordings(tmp_path)
    metadata = json.loads((directory / "recording.json").read_bytes())
    assert len(metadata["segments"]) == 1
    assert metadata["segments"][0]["bytes"] == len(raw)


class Clock:
    utc = datetime(2026, 10, 21, 23, 59, 59, tzinfo=UTC)
    raw = 1.0

    def advance(self, seconds):
        self.utc += timedelta(seconds=seconds)
        self.raw += seconds


def writer(tmp_path, clock, **kwargs):
    return LiveLog(tmp_path / "recordings/live-test", "live-test", "main-lab",
                   state_directory=tmp_path / "state/live-test", utc_clock=lambda: clock.utc,
                   clock=lambda: clock.raw, **kwargs)


def start_revision(log, revision="live-first"):
    value = snapshot(log.sample_time_ms()) | {"sceneRevision": revision}
    assert log.add_revision({"sceneRevision": revision}, json.dumps(value))


async def test_daily_writer_uses_admission_day_and_keeps_fifo_measurements(tmp_path):
    clock = Clock()
    entered, release = threading.Event(), threading.Event()

    def blocked_open(path):
        entered.set()
        assert release.wait(5)
        return path.open("wb")

    log = writer(tmp_path, clock, open_segment=blocked_open)
    await log.ready()
    start_revision(log)
    assert await asyncio.to_thread(entered.wait, 5)
    clock.advance(2)
    observation = {
        "type": "entity.observations", "sceneRevision": "live-first", "epoch": 0,
        "sampleTimeMs": 3000, "simTime": 2, "source": "device",
        "changes": [{"key": "/Real/Incubator2", "rev": 2, "values": {"temperatureC": 38}}],
    }
    assert log.append_json(json.dumps(observation), 3000)
    release.set()
    try:
        metadata = await log.checkpoint()
        state = json.loads((tmp_path / "state/live-test/recording.json").read_bytes())
        assert state["days"] == {"seg-000001.bin": "2026-10-21", "seg-000002.bin": "2026-10-22"}
        assert len(metadata["segments"]) == 2
        samples = read_samples(log.directory, metadata, "/Real/Incubator2", "temperatureC", 0, 4000)
        assert [(sample.t_ms, sample.value) for sample in samples] == [(1000, 37), (3000, 38)]
        assert not (log.directory / "live-state.json").exists()
        assert not (log.directory / "owner.lock").exists()
    finally:
        await log.close()
    assert log.error is None


async def test_restart_after_reboot_preserves_logical_downtime_and_old_measurement_stamp(tmp_path):
    clock = Clock()
    clock.raw = 100
    first = writer(tmp_path, clock)
    await first.ready()
    start_revision(first)
    await first.close()
    clock.advance(7200)
    clock.raw = 1  # A reboot resets the operating system's raw clock.
    second = writer(tmp_path, clock)
    await second.ready()
    try:
        assert second.sample_time_ms() == 7_300_000
        start_revision(second, "live-second")
        metadata = await second.checkpoint()
        assert [segment["file"] for segment in metadata["segments"]] == ["seg-000001.bin", "seg-000002.bin"]
        samples = read_samples(second.directory, metadata, "/Real/Incubator2", "temperatureC", 0, 8_000_000)
        assert [(sample.t_ms, sample.value) for sample in samples] == [(1000, 37), (1000, 37)]
        # The second revision intentionally starts with the supplied old measurement;
        # its new envelope must not turn the value into a recent measurement.
        recent = read_samples(second.directory, metadata, "/Real/Incubator2", "temperatureC",
                              second.sample_time_ms() - 3_600_000, second.sample_time_ms())
        assert recent == []
    finally:
        await second.close()
    assert second.error is None


async def test_retention_pins_actual_reads_then_reopens_without_reusing_segment_numbers(tmp_path):
    clock = Clock()
    clock.utc = datetime(2026, 10, 21, 12, tzinfo=UTC)
    log = writer(tmp_path, clock)
    await log.ready()
    start_revision(log)
    await log.checkpoint()
    with log.reader():
        clock.advance(10 * 86400)
        log.maintain()
        pinned = await log.checkpoint()
        assert len(pinned["segments"]) == 2
        assert (log.directory / "seg-000001.bin").is_file()
    clock.advance(60)
    log.maintain()
    retained = await log.checkpoint()
    assert [segment["file"] for segment in retained["segments"]] == ["seg-000002.bin"]
    assert not (log.directory / "seg-000001.bin").exists()
    await log.close()
    reopened = writer(tmp_path, clock)
    await reopened.ready()
    try:
        start_revision(reopened, "live-second")
        metadata = await reopened.checkpoint()
        assert [segment["file"] for segment in metadata["segments"]] == ["seg-000002.bin", "seg-000003.bin"]
    finally:
        await reopened.close()


async def test_second_writer_cannot_recover_or_publish_over_current_owner(tmp_path):
    clock = Clock()
    first = writer(tmp_path, clock)
    await first.ready()
    start_revision(first)
    before = await first.checkpoint()
    second = writer(tmp_path, clock)
    with pytest.raises(OSError, match="active owner"):
        await second.ready()
    await second.close()
    try:
        assert json.loads((first.directory / "recording.json").read_bytes()) == before
        assert first.error is None
    finally:
        await first.close()


def test_public_mapping_owns_copies_and_never_restamps_old_property_measurements():
    original = EntitySnapshot.model_validate(snapshot(3000))
    public = public_message(original, lambda raw: raw + 10_000)
    assert public.sampleTimeMs == 13_000
    assert public.entities[0].meta["temperatureC"].sampleTimeMs == 11_000
    assert original.sampleTimeMs == 3000
    assert original.entities[0].meta["temperatureC"].sampleTimeMs == 1000


def invocation():
    return {
        "type": "invocation.update", "sceneRevision": "live-first", "invocationId": "inv-old",
        "requestId": "request-old", "entityKey": "/Real/Incubator2", "action": "inspect", "params": {},
        "actor": {"kind": "user", "name": "test"}, "epoch": 0, "status": "succeeded",
        "createdSimTime": 0, "createdAtMs": 1,
    }


async def test_actual_archived_invocation_read_survives_retention(tmp_path):
    clock = Clock()
    clock.utc = datetime(2026, 10, 21, 12, tzinfo=UTC)
    log = writer(tmp_path, clock)
    await log.ready()
    start_revision(log)
    log.append_json(json.dumps(invocation()), 1001, invocation_id="inv-old")
    metadata = await log.checkpoint()
    path = log.directory / metadata["segments"][0]["file"]
    entered, release = threading.Event(), threading.Event()
    original = log._read_payload

    def blocked(location):
        entered.set()
        assert release.wait(5)
        return original(location)

    log._read_payload = blocked
    lookup = asyncio.create_task(log.lookup_invocation("inv-old"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        clock.advance(10 * 86400)
        log.maintain()
        await log.checkpoint()
        assert path.exists()
        release.set()
        assert (await lookup).status == "succeeded"
    finally:
        release.set()
        await log.close()


async def test_executor_submission_failure_releases_io_pin(tmp_path, monkeypatch):
    clock = Clock()
    log = writer(tmp_path, clock)
    await log.ready()

    def failed_submission(*_args):
        raise RuntimeError("Injected executor submission failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(asyncio.get_running_loop(), "run_in_executor", failed_submission)
            with pytest.raises(RuntimeError, match="submission failure"):
                await log.read_pinned(lambda: None)
        assert log._readers == 0
    finally:
        await log.close()


async def test_close_holds_owner_until_cancelled_read_job_finishes(tmp_path):
    clock = Clock()
    log = writer(tmp_path, clock)
    await log.ready()
    start_revision(log)
    await log.checkpoint()
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(5)

    reading = asyncio.create_task(log.read_pinned(blocked))
    assert await asyncio.to_thread(entered.wait, 5)
    reading.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reading
    closing = asyncio.create_task(log.close())
    competitor = writer(tmp_path, clock)
    try:
        with pytest.raises(OSError, match="active owner"):
            await competitor.ready()
        await competitor.close()
        assert not closing.done()
        assert log._readers == 1
    finally:
        release.set()
        await closing
    assert log._readers == 0
    reopened = writer(tmp_path, clock)
    await reopened.ready()
    await reopened.close()


async def test_catalog_attribution_is_published_before_segment_can_reach_disk(tmp_path):
    clock = Clock()
    first = writer(tmp_path, clock)
    await first.ready()
    start_revision(first)
    await first.close()
    observed = []

    def boundary_open(path):
        observed.append(json.loads((path.parent / "recording.json").read_bytes()))
        return path.open("xb")

    second = writer(tmp_path, clock, open_segment=boundary_open)
    await second.ready()
    start_revision(second, "live-second")
    await second.close()
    assert set(observed[0]["revisions"]) == {"live-first", "live-second"}
    # Simulate interruption after the valid second prefix reached disk but
    # before its segment entry was published. Keep the real allocation state.
    (second.directory / "recording.json").write_text(json.dumps(observed[0]))
    reopened = writer(tmp_path, clock)
    await reopened.ready()
    try:
        metadata = await reopened.checkpoint()
        assert [segment["sceneRevision"] for segment in metadata["segments"]] == ["live-first", "live-second"]
        assert all(segment["sceneRevision"] in metadata["revisions"] for segment in metadata["segments"])
        assert all((reopened.directory / item["catalog"]).is_file()
                   for item in metadata["revisions"].values())
    finally:
        await reopened.close()


async def test_cancelling_one_closing_checkpoint_cannot_cancel_shared_sealed_prefix(tmp_path):
    clock = Clock()
    log = writer(tmp_path, clock)
    await log.ready()
    start_revision(log)
    await log.checkpoint()
    cancelled = asyncio.create_task(log._closed_checkpoint())
    surviving = asyncio.create_task(log._closed_checkpoint())
    await asyncio.sleep(0)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    assert not log._sealed.cancelled()
    await log.close()
    result = await asyncio.wait_for(surviving, 2)
    assert len(result["segments"]) == 1


async def test_incomplete_log_can_seal_its_valid_flushed_prefix(tmp_path):
    clock = Clock()
    log = writer(tmp_path, clock)
    await log.ready()
    start_revision(log)
    await log.checkpoint()
    log.stop_incomplete("Reliable admission stopped")
    await log.close()
    metadata = await log.checkpoint()
    assert metadata["status"] == "incomplete"
    rows = read_samples(log.directory, metadata, "/Real/Incubator2", "temperatureC", 0, 2000)
    assert [(row.t_ms, row.value) for row in rows] == [(1000, 37)]

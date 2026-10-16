import json
import struct
import threading

import pytest

from packages.info.log import SessionLog, read_segment, recover_recordings
from packages.protocol.schema import TransformFrame
from packages.transport import HEADER as FRAME_HEADER
from packages.transport import FrameCodec


def frame(t=1000, seq=1, epoch=0):
    return FrameCodec("session-test", "rev-test", 1).encode(
        TransformFrame(
            type="stream.snapshot",
            sessionId="session-test",
            sceneRevision="rev-test",
            epoch=epoch,
            seq=seq,
            simTime=(t - 1000) / 1000,
            sampleTimeMs=t,
            poses=[(1, t / 1000, 0, 0, 0, 0, 0, 1)],
        )
    )


def snapshot(t=1000, value="rack"):
    return json.dumps(
        {
            "type": "entity.snapshot",
            "sceneRevision": "rev-test",
            "epoch": 0,
            "simTime": (t - 1000) / 1000,
            "sampleTimeMs": t,
            "entities": [{"key": "/tube", "rev": 1, "values": {"location": value}, "meta": {}}],
            "invocations": [],
        }
    )


def new_log(tmp_path, **kwargs):
    log = SessionLog(tmp_path / "session-test", "session-test", "lab-demo", **kwargs)
    log.add_revision(
        {"sceneRevision": "rev-test", "revisionNumericId": 1},
        {"sceneRevision": "rev-test"},
        frame(),
        snapshot(),
    )
    return log


async def test_literal_v1_bytes_preserve_nsynth_payload_and_fifo(tmp_path):
    log = new_log(tmp_path)
    payload = '{"type":"server.error","code":"test","message":"a"}'
    log.append_json(payload, 900)  # Backward timestamps must not reorder reliable messages.
    await log.close()
    raw = (tmp_path / "session-test/seg-000001.bin").read_bytes()
    expected = b"LWLG\x01\x00" + struct.pack("<IBd", len(frame()) + 9, 1, 1000) + frame()
    assert raw.startswith(expected)
    result = read_segment(raw)
    assert not result.truncated
    assert [item.kind for item in result.records] == [1, 2, 2]
    assert result.records[-1].payload == payload.encode()
    assert result.records[-1].t_ms == 900


async def test_roll_snapshot_uses_written_fifo_position_not_producer_future(tmp_path):
    log = new_log(tmp_path, segment_seconds=1)
    log.append_frame(frame(2100, 2), 2100)
    log.append_json(snapshot(2200, "slot"), 2200)
    await log.close()
    second = read_segment((tmp_path / "session-test/seg-000002.bin").read_bytes()).records
    assert json.loads(second[1].payload)["entities"][0]["values"]["location"] == "rack"
    assert json.loads(second[-1].payload)["entities"][0]["values"]["location"] == "slot"


async def test_pending_then_disk_invocation_lookup_does_not_need_recent_history(tmp_path):
    gate = threading.Event()
    entered = threading.Event()

    def blocked_open(path):
        entered.set()
        assert gate.wait(5)
        return path.open("wb")

    log = new_log(tmp_path, open_segment=blocked_open)
    assert await __import__("asyncio").to_thread(entered.wait, 5)
    invocation = {
        "type": "invocation.update",
        "sceneRevision": "rev-test",
        "invocationId": "inv-old",
        "requestId": "request-old",
        "entityKey": "/tube",
        "action": "inspect",
        "params": {},
        "actor": {"kind": "user", "name": "test"},
        "epoch": 0,
        "status": "succeeded",
        "createdSimTime": 0,
        "createdAtMs": 1,
    }
    log.append_json(json.dumps(invocation), 1001, invocation_id="inv-old")
    assert (await log.lookup_invocation("inv-old")).requestId == "request-old"
    gate.set()
    await log.close()
    assert (await log.lookup_invocation("inv-old")).status == "succeeded"
    assert log.pending_count == 0


async def test_slow_writer_drops_frames_with_gap_but_reliable_overflow_is_incomplete(tmp_path):
    gate = threading.Event()
    entered = threading.Event()

    def blocked_open(path):
        entered.set()
        assert gate.wait(5)
        return path.open("wb")

    log = new_log(tmp_path, queue_size=2, open_segment=blocked_open)
    assert await __import__("asyncio").to_thread(entered.wait, 5)
    assert log.append_frame(frame(1100, 2), 1100)
    assert log.append_frame(frame(1200, 3), 1200)
    assert not log.append_frame(frame(1300, 4), 1300)
    assert log.append_json('{"type":"server.error","message":"one"}', 1400)
    assert log.append_json('{"type":"server.error","message":"two"}', 1500)
    assert not log.append_json('{"type":"server.error","message":"three"}', 1600)
    gate.set()
    await log.close()
    metadata = json.loads((tmp_path / "session-test/recording.json").read_text())
    assert metadata["status"] == "incomplete"
    records = read_segment((tmp_path / "session-test/seg-000001.bin").read_bytes()).records
    assert sum(json.loads(r.payload)["count"] for r in records if r.kind == 3) == 3
    assert [json.loads(r.payload).get("message") for r in records if r.kind == 2][-2:] == ["one", "two"]


async def test_recovery_includes_open_first_segment_and_ignores_truncated_tail(tmp_path):
    log = new_log(tmp_path)
    log.append_frame(frame(4300, 2), 4300)
    await log.close()
    path = tmp_path / "session-test"
    metadata = json.loads((path / "recording.json").read_text())
    metadata.update(status="recording", segments=[])
    (path / "recording.json").write_text(json.dumps(metadata))
    with (path / "seg-000001.bin").open("ab") as output:
        output.write(struct.pack("<I", 100) + b"\x01\x00")
    recover_recordings(tmp_path)
    recovered = json.loads((path / "recording.json").read_text())
    assert recovered["status"] == "incomplete"
    assert recovered["durationMs"] == 3300
    segment = read_segment((path / "seg-000001.bin").read_bytes())
    assert segment.truncated and len(segment.records) == 3
    assert recovered["segments"][0]["bytes"] == segment.valid_bytes


@pytest.mark.parametrize(
    "raw", [b"LWLG\x02\x00", b"WRNG\x01\x00", b"LWLG\x01\x00" + struct.pack("<IBd", 9, 9, 1)]
)
def test_reader_rejects_unknown_versions_or_corrupt_records(raw):
    with pytest.raises(ValueError):
        read_segment(raw)


async def test_reset_roll_defers_until_spatial_and_entity_epochs_match(tmp_path):
    log = new_log(tmp_path, segment_seconds=1)
    log.append_frame(frame(1600, 2, epoch=1), 1600, reliable=True)
    state = json.loads(snapshot(2200, "slot"))
    state["epoch"] = 1
    log.append_json(json.dumps(state), 2200)
    log.append_frame(frame(2300, 3, epoch=1), 2300)
    await log.close()
    metadata = json.loads((tmp_path / "session-test/recording.json").read_text())
    assert len(metadata["segments"]) == 2
    for segment in metadata["segments"]:
        records = read_segment((tmp_path / "session-test" / segment["file"]).read_bytes()).records
        assert FRAME_HEADER.unpack_from(records[0].payload)[4] == json.loads(records[1].payload)["epoch"]


async def test_reload_keeps_revision_documents_and_independent_new_segment(tmp_path):
    log = new_log(tmp_path)
    value = json.loads(snapshot(2000, "new-scene"))
    value["sceneRevision"] = "rev-next"
    encoded = bytearray(frame(2000, 2))
    struct.pack_into("<I", encoded, 8, 2)
    log.add_revision(
        {"sceneRevision": "rev-next", "revisionNumericId": 2},
        {"sceneRevision": "rev-next"},
        bytes(encoded),
        json.dumps(value),
        assets={"assets/shape.glb": b"immutable geometry"},
    )
    await log.close()
    directory = tmp_path / "session-test"
    metadata = json.loads((directory / "recording.json").read_text())
    assert metadata["revisions"]["rev-test"]["manifest"] == "manifest.json"
    assert metadata["revisions"]["rev-next"]["manifest"] == "manifest-000002.json"
    assert json.loads((directory / "catalog-000002.json").read_text())["sceneRevision"] == "rev-next"
    assert (directory / "assets/shape.glb").read_bytes() == b"immutable geometry"
    records = read_segment((directory / "seg-000002.bin").read_bytes()).records
    assert FRAME_HEADER.unpack_from(records[0].payload)[3] == 2
    assert json.loads(records[1].payload)["sceneRevision"] == "rev-next"

import asyncio
import ctypes
import json
import struct
import sys
import threading
from pathlib import Path

import pytest

from packages.info.log import SessionLog, _atomic_json, read_segment, recover_recordings
from packages.library.scenes import SceneLibrary
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
    version = SceneLibrary().resolve("lab-demo@1")
    log = SessionLog(
        tmp_path / "session-test", "session-test", "lab-demo", scene_version=version.id,
        scene_content_hash=version.content_hash, kind="session", **kwargs,
    )
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


async def test_roll_prefix_preserves_stale_meta_and_original_scalar(tmp_path):
    log = new_log(tmp_path, segment_seconds=1)
    log.append_json(json.dumps({
        "type": "entity.observations", "sceneRevision": "rev-test", "epoch": 0,
        "simTime": 0.5, "sampleTimeMs": 1500, "source": "virtual",
        "changes": [{"key": "/tube", "rev": 2, "values": {"location": "rack"}, "meta": {
            "location": {"simTime": 0, "sampleTimeMs": 1000, "source": "virtual", "stale": True}}}],
    }), 1500)
    log.append_frame(frame(2100, 2), 2100)
    await log.close()
    second = read_segment((tmp_path / "session-test/seg-000002.bin").read_bytes()).records
    entity = json.loads(second[1].payload)["entities"][0]
    assert entity["values"]["location"] == "rack"
    assert entity["meta"]["location"] == {
        "simTime": 0, "sampleTimeMs": 1000, "source": "virtual", "stale": True,
    }


async def test_roll_prefix_keeps_the_actual_plan_revision_and_last_step_state(tmp_path):
    log = new_log(tmp_path, segment_seconds=1)
    full = {
        "type": "plan.update", "update": "full", "sceneRevision": "rev-test", "epoch": 0,
        "simTime": 0.1, "sampleTimeMs": 1100,
        "plan": {"id": "plan-one", "kind": "operation", "title": "First revision", "target": "session",
                 "revision": 1, "status": "running", "steps": [
                     {"id": 1, "status": "running", "check": {"entity": "/tube", "property": "location",
                                                               "equals": "rack"}},
                 ]},
    }
    log.append_json(json.dumps(full), 1100)
    log.append_json(json.dumps({
        "type": "plan.update", "update": "step", "sceneRevision": "rev-test", "epoch": 0,
        "simTime": 0.2, "sampleTimeMs": 1200, "planId": "plan-one", "revision": 1,
        "stepId": 1, "status": "done",
    }), 1200)
    log.append_frame(frame(2100, 2), 2100)
    future = full | {"sampleTimeMs": 2200, "simTime": 1.2,
                     "plan": full["plan"] | {"revision": 2, "title": "Later revision"}}
    log.append_json(json.dumps(future), 2200)
    await log.close()
    second = read_segment((tmp_path / "session-test/seg-000002.bin").read_bytes()).records
    plans = [json.loads(item.payload) for item in second if item.kind == 2
             and json.loads(item.payload).get("type") == "plan.update"]
    assert plans[0]["plan"]["revision"] == 1 and plans[0]["plan"]["title"] == "First revision"
    assert plans[0]["plan"]["steps"][0]["status"] == "done"
    assert plans[-1]["plan"]["revision"] == 2


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows share-delete semantics")
async def test_windows_atomic_metadata_retries_real_read_handle_sharing_conflict(tmp_path, monkeypatch):
    path = tmp_path / "recording.json"
    _atomic_json(path, {"version": 1})
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    blocked = threading.Event()
    original_replace = Path.replace

    def observe_replace(temporary, target):
        try:
            return original_replace(temporary, target)
        except PermissionError:
            blocked.set()
            raise

    monkeypatch.setattr(Path, "replace", observe_replace)
    task = asyncio.create_task(asyncio.to_thread(_atomic_json, path, {"version": 2}))
    try:
        assert await asyncio.to_thread(blocked.wait, 5), "Expected a real Windows sharing violation"
    finally:
        kernel.CloseHandle(handle)
    await task
    assert json.loads(path.read_bytes()) == {"version": 2}


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
async def test_persistent_metadata_failure_closes_file_once_and_recovers_incomplete(tmp_path, monkeypatch):
    original_replace = Path.replace

    def fail_existing_metadata(temporary, target):
        if target.name == "recording.json" and target.exists():
            raise PermissionError("Persistent metadata replacement failure")
        return original_replace(temporary, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail_existing_metadata)
        log = new_log(tmp_path)
        await log.close()
        assert "Persistent" in log.error
        assert not log.thread.is_alive()
    recover_recordings(tmp_path)
    value = json.loads((tmp_path / "session-test/recording.json").read_bytes())
    assert value["status"] == "incomplete" and len(value["segments"]) == 1


def invocation_payload(identifier, status):
    return json.dumps(
        {
            "type": "invocation.update",
            "sceneRevision": "rev-test",
            "invocationId": identifier,
            "requestId": "request-" + identifier,
            "entityKey": "/tube",
            "action": "inspect",
            "params": {},
            "actor": {"kind": "user", "name": "test"},
            "epoch": 0,
            "status": status,
            "createdSimTime": 0,
            "createdAtMs": 1,
        }
    )


async def test_roll_retains_last_fifty_terminal_completions_including_early_accepted_long_action(tmp_path):
    log = new_log(tmp_path, segment_seconds=1)
    log.append_json(invocation_payload("long", "accepted"), 1100)
    for i in range(60):
        log.append_json(invocation_payload(f"short-{i}", "succeeded"), 1200 + i * 10)
    log.append_json(invocation_payload("long", "succeeded"), 1850)
    log.append_frame(frame(2100, 2), 2100)
    await log.close()
    records = read_segment((tmp_path / "session-test/seg-000002.bin").read_bytes()).records
    values = json.loads(records[1].payload)["invocations"]
    assert [value["invocationId"] for value in values] == [*[f"short-{i}" for i in range(11, 60)], "long"]


async def test_authoritative_entity_snapshot_removes_old_active_invocation_from_roll_prefix(tmp_path):
    log = new_log(tmp_path, segment_seconds=1)
    log.append_json(invocation_payload("old-active", "accepted"), 1100)
    log.append_json(snapshot(1500), 1500)
    log.append_frame(frame(2100, 2), 2100)
    await log.close()
    records = read_segment((tmp_path / "session-test/seg-000002.bin").read_bytes()).records
    assert json.loads(records[1].payload)["invocations"] == []


@pytest.mark.parametrize("header", [b"", b"LW", b"LWLG\x01"])
def test_crash_before_first_segment_header_flush_still_recovers_incomplete(tmp_path, header):
    directory = tmp_path / "session-crashed"
    directory.mkdir()
    (directory / "recording.json").write_text(
        json.dumps(
            {
                "formatVersion": 1,
                "protocolVersion": 2,
                "sessionId": directory.name,
                "status": "recording",
                "segments": [],
            }
        )
    )
    (directory / "seg-000001.bin").write_bytes(header)
    recover_recordings(tmp_path)
    metadata = json.loads((directory / "recording.json").read_bytes())
    assert metadata["status"] == "incomplete"
    assert metadata["durationMs"] == 0 and metadata["segments"] == []


async def test_crash_in_later_segment_header_keeps_earlier_playable_segment(tmp_path):
    log = new_log(tmp_path)
    log.append_frame(frame(3400, 2), 3400)
    await log.close()
    directory = tmp_path / "session-test"
    metadata = json.loads((directory / "recording.json").read_bytes())
    metadata["status"] = "recording"
    (directory / "recording.json").write_text(json.dumps(metadata))
    (directory / "seg-000002.bin").write_bytes(b"LW")
    recover_recordings(tmp_path)
    recovered = json.loads((directory / "recording.json").read_bytes())
    assert recovered["status"] == "incomplete" and recovered["durationMs"] == 2400
    assert [segment["file"] for segment in recovered["segments"]] == ["seg-000001.bin"]

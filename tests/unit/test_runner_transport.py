import multiprocessing as mp
import struct
import subprocess
import sys
import threading

import pytest

from packages.protocol.schema import TransformFrame
from packages.transport import FrameCodec, LatestSlot, PipeWriter


def test_compact_pipe_frame_preserves_uint64_seq_and_float32_pose():
    codec = FrameCodec("session", "rev", 7)
    frame = TransformFrame(
        sessionId="session",
        sceneRevision="rev",
        epoch=3,
        seq=2**40,
        simTime=1.25,
        sampleTimeMs=2000,
        poses=[(42, 1.5, -2.25, 3, 0, 0, 0, 1)],
    )
    payload = codec.encode(frame)
    assert len(payload) == 44 + 32
    assert payload[:4] == b"NSYN"
    assert payload[44:] == struct.pack("<I7f", 42, 1.5, -2.25, 3, 0, 0, 0, 1)
    decoded = codec.decode(payload)
    assert decoded.seq == 2**40
    assert decoded.epoch == 3
    assert decoded.poses == frame.poses
    with pytest.raises(ValueError, match="revision"):
        FrameCodec("session", "other", 8).decode(payload)
    with pytest.raises(ValueError, match="length"):
        codec.decode(payload[:-1])


def test_latest_slot_retains_only_newest_sample_and_close_unblocks_reader():
    slot = LatestSlot()
    for index in range(10000):
        slot.put(index)
    assert slot.take() == 9999
    assert slot.overwritten == 9999
    slot.close()
    assert slot.take() is None


def test_blocked_frame_pipe_never_blocks_latest_slot_producer():
    read, write = mp.get_context("spawn").Pipe(duplex=False)
    writer = PipeWriter(write, latest_only=True)
    done = threading.Event()

    def produce():
        for _ in range(10000):
            writer.offer(b"newest")
        done.set()

    try:
        writer.offer(b"x" * (4 * 1024 * 1024))
        assert read.poll(2)  # The sending thread is waiting on the unread large frame.
        producer = threading.Thread(target=produce)
        producer.start()
        assert done.wait(2)
        assert read.recv_bytes() == b"x" * (4 * 1024 * 1024)
        producer.join(timeout=1)
    finally:
        read.close()
        writer.close()
        write.close()
    assert not writer.thread.is_alive()


def test_importing_gateway_adapter_and_spawn_target_does_not_load_physics_engine():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import apps.api.app.sources.newton_source; "
            "import apps.api.app.sources.remote_source; import packages.transport.remote_server; "
            "import packages.sim_runtime.runner; assert 'newton' not in sys.modules; "
            "assert 'warp' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr

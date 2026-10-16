"""Measure actual session-log drain/flush throughput and event-loop responsiveness."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.info.log import SessionLog, read_segment
from packages.protocol.schema import Catalog, EntitySnapshot, SceneManifest, TransformFrame
from packages.transport import HEADER, FrameCodec


async def benchmark_log(directory: Path, *, frames=1024, bodies=2048):
    session_id, revision = "session-log-benchmark", "rev-log-benchmark"
    manifest = SceneManifest(
        sessionId=session_id,
        sceneRevision=revision,
        revisionNumericId=1,
        sceneName="Recording benchmark",
        source={"kind": "synthetic", "physicsDt": 1 / 240, "basePublishHz": 30, "realtimeFactor": 1},
        nodes=[
            {"kind": "body", "entityKey": f"/Bench/Body{i}", "streamId": i, "name": f"Body{i}"}
            for i in range(bodies)
        ],
    )
    codec = FrameCodec(session_id, revision, 1)
    initial = codec.encode(
        TransformFrame(
            type="stream.snapshot",
            sessionId=session_id,
            sceneRevision=revision,
            epoch=0,
            seq=0,
            simTime=0,
            sampleTimeMs=1000,
            poses=[(i, float(i % 32), float(i // 32), 0, 0, 0, 0, 1) for i in range(bodies)],
        )
    )
    tail = initial[HEADER.size :]
    snapshot = EntitySnapshot(sceneRevision=revision, epoch=0, sampleTimeMs=1000).model_dump_json()
    lags, peak_depth = [], 0
    done = asyncio.Event()

    async def observe_loop():
        deadline = asyncio.get_running_loop().time() + 0.002
        while not done.is_set():
            await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
            now = asyncio.get_running_loop().time()
            lags.append(max(0, now - deadline) * 1000)
            deadline = now + 0.002

    start = time.perf_counter()
    log = SessionLog(directory, session_id, "recording-benchmark")
    log.add_revision(
        manifest.model_dump(mode="json"),
        Catalog(sceneRevision=revision).model_dump(mode="json"),
        initial,
        snapshot,
    )
    observer = asyncio.create_task(observe_loop())
    for i in range(1, frames + 1):
        t_ms = 1000 + i * 1000 / 30
        payload = HEADER.pack(b"NSYN", 1, 1, 1, 0, i, i / 30, t_ms, bodies) + tail
        if not log.append_frame(payload, t_ms, reliable=True):
            raise RuntimeError("Benchmark recording unexpectedly overflowed")
        peak_depth = max(peak_depth, log.queue_depth)
        await asyncio.sleep(0)
    await log.close()  # Timed scope includes the queue drain, buffered flush and metadata close.
    elapsed = time.perf_counter() - start
    done.set()
    await observer
    if log.error:
        raise RuntimeError(log.error)

    # Validate real disk output after timing; never report admission-only throughput.
    metadata = json.loads((directory / "recording.json").read_bytes())
    written_bytes, data_frames, last_seq, snapshot_count = 0, 0, -1, 0
    for segment in metadata["segments"]:
        raw = (directory / segment["file"]).read_bytes()
        result = read_segment(raw)
        assert not result.truncated and result.valid_bytes == len(raw)
        written_bytes += len(raw)
        for record in result.records:
            if record.kind != 1:
                continue
            header = HEADER.unpack_from(record.payload)
            assert header[0] == b"NSYN" and header[8] == bodies and record.payload[HEADER.size :] == tail
            if header[2] == 1:
                assert header[5] == data_frames + 1
                data_frames += 1
                last_seq = header[5]
            else:
                snapshot_count += 1
    assert metadata["status"] == "complete" and data_frames == frames and last_seq == frames
    ordered = sorted(lags)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0
    return {
        "scope": "writer admission through completed drain/buffered flush/metadata; file output verified; no fsync",
        "frames": frames,
        "bodiesPerFrame": bodies,
        "verifiedDataFrames": data_frames,
        "verifiedSegmentSnapshots": snapshot_count,
        "segments": len(metadata["segments"]),
        "bytesWritten": written_bytes,
        "elapsedSeconds": elapsed,
        "megabytesPerSecond": written_bytes / elapsed / 1_000_000,
        "queueCapacity": log.queue_size,
        "peakQueueDepth": peak_depth,
        "finalQueueDepth": log.queue_depth,
        "loopProbeIntervalMs": 2,
        "loopProbeSamples": len(lags),
        "loopDelayP95Ms": p95,
        "loopDelayMaxMs": max(lags, default=0),
        "meetsThroughputBudget": written_bytes / elapsed >= 5_000_000,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=1024)
    parser.add_argument("--bodies", type=int, default=2048)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.frames < 4096 or args.bodies < 1:
        parser.error("frames must be 1..4095 and bodies must be positive")
    with tempfile.TemporaryDirectory(prefix="spatial-recording-benchmark-") as temporary:
        result = asyncio.run(
            benchmark_log(Path(temporary) / "session-log-benchmark", frames=args.frames, bodies=args.bodies)
        )
    result["platform"] = platform.platform()
    output = json.dumps(result, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    return 0 if result["meetsThroughputBudget"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

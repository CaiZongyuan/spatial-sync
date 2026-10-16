"""Bounded real Warp readback + spawned-process Pipe profiling, with output oracles."""

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.protocol.schema import TransformFrame
from packages.transport import FrameCodec


def poses(count):
    data = np.zeros((count, 7), dtype=np.float32)
    data[:, 0] = np.arange(count) * 0.01
    data[:, 1] = np.sin(np.arange(count) * 0.03)
    data[:, 2] = 0.45
    data[:, 6] = 1
    return data


def targets(count):
    # A full reversed order also catches identity/dense-row confusion.
    return np.arange(count - 1, -1, -1, dtype=np.int32)


def summary(samples):
    return {
        key: {
            "p50": float(np.percentile([row[key] for row in samples], 50)),
            "p95": float(np.percentile([row[key] for row in samples], 95)),
            "min": float(min(row[key] for row in samples)),
            "max": float(max(row[key] for row in samples)),
        }
        for key in samples[0]
    }


def extraction(count, device, mode, iterations, warmup):
    import warp as wp

    from packages.sim_runtime.pose_filter import PoseFilter
    from packages.sim_runtime.transform_extractor import TransformExtractor

    data = poses(count)
    expected = data.astype(np.float64)
    body_q = wp.array(data, dtype=wp.transform, device=device)
    extractor = TransformExtractor(count, device=device)
    pose_filter = PoseFilter(count)
    samples, max_error = [], 0.0
    try:
        # Warm the actual copy path and pinned buffers before timing.
        for iteration in range(iterations + warmup):
            started = perf_counter()
            if mode == "async-bulk":
                result = extractor.capture(body_q, block=True).result(timeout=5)
                metrics = dict(extractor.last_metrics)
            else:
                read = perf_counter()
                bulk = body_q.numpy()
                readback_ms = (perf_counter() - read) * 1000
                filtered = perf_counter()
                result = pose_filter.update(bulk)
                metrics = {
                    "readbackWallMs": readback_ms,
                    "filterMs": (perf_counter() - filtered) * 1000,
                }
            metrics["endToEndMs"] = (perf_counter() - started) * 1000
            # Every timed sample has an independent source-array/order oracle.
            max_error = max(max_error, float(np.max(np.abs(result - expected))))
            if result.shape != expected.shape or max_error > 1e-6:
                raise AssertionError("Readback did not preserve the complete model-index order")
            if iteration >= warmup:
                samples.append(metrics)
        assert extractor.dropped_captures == 0 and extractor.error is None
        return {
            "device": device,
            "mode": mode,
            "modelBodies": count,
            "transferredPoseBytes": count * 28,
            "outputSha256": hashlib.sha256(result.astype(np.float32).tobytes()).hexdigest(),
            "checkedSamples": iterations,
            "maxError": max_error,
            "droppedCaptures": extractor.dropped_captures,
            "distribution": summary(samples),
            "rawSamples": samples,
        }
    finally:
        extractor.close()


def pipe_receiver(connection, acknowledgments, count):
    codec = FrameCodec("profile", "revision", 1)
    indices = targets(count)
    expected = poses(count)[indices]
    expected_ids = 100 + 3 * indices
    try:
        while True:
            started = perf_counter()
            payload = connection.recv_bytes()
            received = perf_counter()
            if payload == b"done":
                return
            decoded = codec.decode(payload)
            decoded_at = perf_counter()
            actual = np.array(decoded.poses)
            assert np.array_equal(actual[:, 0], expected_ids)
            np.testing.assert_allclose(actual[:, 1:], expected, rtol=0, atol=1e-6)
            # Validation is separated from decode and included in roundtrip.
            acknowledgments.send(
                {
                    "seq": decoded.seq,
                    "receiveWaitAndCopyMs": (received - started) * 1000,
                    "decodeMs": (decoded_at - received) * 1000,
                    "checkedBodies": len(actual),
                }
            )
    finally:
        connection.close()
        acknowledgments.close()


def ipc(count, iterations, warmup):
    context = mp.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    ack_reader, ack_writer = context.Pipe(duplex=False)
    process = context.Process(target=pipe_receiver, args=(reader, ack_writer, count))
    process.start()
    reader.close()
    ack_writer.close()
    codec = FrameCodec("profile", "revision", 1)
    indices = targets(count)
    sample = poses(count)[indices]
    stream_ids = 100 + 3 * indices
    samples = []
    try:
        for iteration in range(iterations + warmup):
            started = perf_counter()
            rows = [
                (int(stream_id), *row) for stream_id, row in zip(stream_ids, sample.tolist(), strict=True)
            ]
            staged = perf_counter()
            frame = TransformFrame(
                sessionId="profile",
                sceneRevision="revision",
                epoch=0,
                seq=iteration,
                simTime=iteration / 30,
                sampleTimeMs=started * 1000,
                poses=rows,
            )
            validated = perf_counter()
            payload = codec.encode(frame)
            encoded = perf_counter()
            writer.send_bytes(payload)
            sent = perf_counter()
            if not ack_reader.poll(10):
                raise TimeoutError("Real spawned Pipe receiver did not acknowledge")
            ack = ack_reader.recv()
            finished = perf_counter()
            assert ack["seq"] == iteration and ack["checkedBodies"] == len(indices)
            if iteration >= warmup:
                samples.append(
                    {
                        "rowsMs": (staged - started) * 1000,
                        "frameValidationMs": (validated - staged) * 1000,
                        "encodeMs": (encoded - validated) * 1000,
                        "pipeSendMs": (sent - encoded) * 1000,
                        "pipeRoundtripAndOracleMs": (finished - encoded) * 1000,
                        "totalRowsThroughAckMs": (finished - started) * 1000,
                        "decodeMs": ack["decodeMs"],
                        "receiveWaitAndCopyMs": ack["receiveWaitAndCopyMs"],
                    }
                )
        writer.send_bytes(b"done")
        process.join(timeout=10)
        assert not process.is_alive() and process.exitcode == 0
        return {
            "modelBodies": count,
            "payloadBytes": len(payload),
            "checkedSamples": iterations,
            "childReaped": True,
            "distribution": summary(samples),
            "rawSamples": samples,
        }
    finally:
        writer.close()
        ack_reader.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def main():
    import warp as wp

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", nargs="+", type=int, default=[100, 500, 2000, 10000])
    parser.add_argument("--devices", nargs="+", default=["cpu", "cuda:0"])
    parser.add_argument("--iterations", type=int, default=40)
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        any(count <= 0 for count in args.counts)
        or not 1 <= args.iterations <= 1000
        or not 1 <= args.repeats <= 10
    ):
        parser.error("Require positive counts, 1–1000 iterations and 1–10 repeats")
    wp.init()
    runs, pipe_runs = [], []
    for repeat in range(args.repeats):
        for count in args.counts:
            for device in args.devices:
                for mode in ["sync-bulk", "async-bulk"]:
                    result = extraction(count, device, mode, args.iterations, args.warmup)
                    result["repeat"] = repeat + 1
                    runs.append(result)
                    print(
                        f"{repeat + 1}: {device} {count} {mode} "
                        f"p50={result['distribution']['endToEndMs']['p50']:.3f} ms",
                        flush=True,
                    )
            result = ipc(count, args.iterations, args.warmup)
            result["repeat"] = repeat + 1
            pipe_runs.append(result)
    owned_files = [
        "packages/sim_runtime/transform_extractor.py",
        "packages/sim_runtime/runner.py",
        "packages/transport/__init__.py",
        "tools/profile_extraction.py",
    ]
    report = {
        "status": "measured",
        "configuration": vars(args) | {"output": str(args.output)},
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "sourceSha256": {file: hashlib.sha256(Path(file).read_bytes()).hexdigest() for file in owned_files},
        "hardware": {
            "platform": platform.platform(),
            "cpu": platform.processor(),
            "logicalProcessors": os.cpu_count(),
            "devices": [str(wp.get_device(device)) for device in args.devices],
        },
        "versions": {name: version(name) for name in ["warp-lang", "newton", "numpy", "pydantic"]},
        "methodology": {
            "baseline": "One real bulk body_q.numpy() before CPU filter",
            "candidate": "Actual TransformExtractor bulk staging on sim stream, event-ordered "
            "pinned copy stream and worker filter; every Future is awaited",
            "timing": "CUDA readback fields are device event durations. Enqueue, host copy/filter, "
            "endToEnd and Pipe fields are host wall times. Do not add overlapping clocks.",
            "scope": "Sequential observations measure latency, not solver/readback overlap speedup. "
            "No physics step workload is invented here; "
            "real Runner command smoke separately proves integration.",
            "ipc": "Actual spawned-process multiprocessing Pipe send_bytes/recv_bytes with the production "
            "FrameCodec. Pipe send is blocking-call duration; receiveWaitAndCopy includes producer wait. "
            "Roundtrip includes decode, independent oracle and acknowledgement, and is an upper bound, "
            "not pure transfer latency.",
            "limits": "Warmup excluded; every sample checked; shared workstation. "
            "All captures transfer the complete model body set.",
        },
        "extraction": runs,
        "ipc": pipe_runs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {args.output}")


if __name__ == "__main__":
    main()

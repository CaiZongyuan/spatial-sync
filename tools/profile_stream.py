"""Source-independent codec/network measurements with verified immutable pose corpora."""

import argparse
import asyncio
import hashlib
import json
import multiprocessing as mp
import platform
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from packages.protocol.schema import SceneManifest, TransformFrame
from packages.transport import HEADER, FrameCodec


def corpus(count):
    ids = np.arange(count)
    result = []
    for phase in range(8):
        angle = phase * 2 * np.pi / 8
        z_angle = 0.2 * np.sin(angle + ids * 0.1)
        values = np.column_stack(
            (
                ids,
                (ids % 100) * 0.02 + 0.05 * np.sin(angle + ids * 0.07),
                (ids // 100) * 0.02,
                0.5 + 0.05 * np.cos(angle + ids * 0.13),
                np.zeros(count),
                np.zeros(count),
                np.sin(z_angle / 2),
                np.cos(z_angle / 2),
            )
        )
        result.append([(int(row[0]), *row[1:]) for row in values.tolist()])
    return result


def percentile(values, quantile=95):
    return float(np.percentile(values, quantile)) if values else None


def verify(rows, expected):
    actual, expected = np.asarray(rows), np.asarray(expected)
    if actual.shape != expected.shape or not np.array_equal(actual[:, 0], expected[:, 0]):
        raise AssertionError("decoded body count/IDs differ from the frozen corpus")
    error = float(np.max(np.abs(actual[:, 1:] - expected[:, 1:])))
    if not np.isfinite(actual).all() or error > 1e-6:
        raise AssertionError(f"decoded poses differ from known inputs: {error}")


def codecs(iterations):
    from apps.api.app.session_manager import encode

    results = []
    for count in (35, 100, 500, 2000, 10000):
        rows = corpus(count)[0]
        frame = TransformFrame(
            sessionId="profile",
            sceneRevision="profile",
            epoch=0,
            seq=1,
            simTime=0,
            sampleTimeMs=1000,
            poses=rows,
        )
        codec = FrameCodec("profile", "profile", 1)
        for name, pack, parse in (
            ("json", lambda: encode(frame), TransformFrame.model_validate_json),
            ("binary", lambda: codec.encode(frame), codec.decode),
        ):
            for _ in range(5):
                verify(parse(pack()).poses, rows)
            encode_times, decode_times = [], []
            for _ in range(iterations):
                start = time.perf_counter()
                payload = pack()
                end = time.perf_counter()
                decoded = parse(payload)
                done = time.perf_counter()
                verify(decoded.poses, rows)
                encode_times.append((end - start) * 1000)
                decode_times.append((done - end) * 1000)
            results.append(
                {
                    "bodies": count,
                    "format": name,
                    "validSamples": iterations,
                    "payloadBytes": len(payload.encode() if isinstance(payload, str) else payload),
                    "encodeP50Ms": median(encode_times),
                    "encodeP95Ms": percentile(encode_times),
                    "parseValidateP50Ms": median(decode_times),
                    "parseValidateP95Ms": percentile(decode_times),
                }
            )
    return results


def serve(count, port):
    import uvicorn

    import apps.api.app.session_manager as session_module
    from apps.api.app.main import create_app
    from apps.api.app.settings import Settings
    from apps.api.app.sources.synthetic_source import SyntheticSource

    frozen = corpus(count)
    encode_samples = []
    original_encode = session_module.Session._encode_frame

    def measured_encode(session, value, format_name):
        start = time.perf_counter()
        result = original_encode(session, value, format_name)
        encode_samples.append((time.perf_counter() - start) * 1000)
        return result

    session_module.Session._encode_frame = measured_encode

    class CorpusSource(SyntheticSource):
        def _manifest(self):
            return SceneManifest.model_validate(
                {
                    "sessionId": self.session_id,
                    "sceneRevision": "profile",
                    "revisionNumericId": 1,
                    "sceneName": f"Verified corpus {count}",
                    "source": {"kind": "synthetic", "physicsDt": self.options.physicsDt},
                    "nodes": [
                        {
                            "kind": "body",
                            "entityKey": f"/Profile/Body{i}",
                            "name": f"Body{i}",
                            "parentEntityKey": None,
                            "streamId": i,
                        }
                        for i in range(count)
                    ],
                }
            )

        async def start(self, scene_id, options):
            self.templates = [
                TransformFrame(
                    sessionId=self.session_id,
                    sceneRevision="profile",
                    epoch=0,
                    seq=0,
                    simTime=0,
                    sampleTimeMs=0,
                    poses=rows,
                )
                for rows in frozen
            ]
            manifest = await super().start(scene_id, options)
            # This fixture has no device-state producer or robot joints.
            self._events.get_nowait()
            return manifest

        def _publish(self, kind="transform.frame"):
            self._seq += 1
            self._latest = self.templates[self._step_index % 8].model_copy(
                update={
                    "type": kind,
                    "seq": self._seq,
                    "epoch": self._epoch,
                    "simTime": self.sim_time,
                    "sampleTimeMs": time.monotonic() * 1000,
                }
            )
            return self._latest

    app = create_app(
        Settings(allowed_origins=("http://127.0.0.1:5185",)),
        source_factory=lambda kind, sid: CorpusSource(sid),
    )

    @app.get("/profile")
    async def profile():
        return {
            "encodeSamples": len(encode_samples),
            "encodeP95Ms": percentile(encode_samples),
            "sessions": [session.stats() for session in app.state.manager.sessions.values()],
        }

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", ws_per_message_deflate=False)


def demo_app():
    """The actual production sources, with the isolated browser verification origin."""
    from apps.api.app.main import create_app
    from apps.api.app.settings import Settings

    return create_app(Settings(allowed_origins=("http://127.0.0.1:5185",)))


def reader(url, count, format_name, ready, results, go, stop):
    async def run():
        from websockets.asyncio.client import connect

        expected = [np.asarray(rows) for rows in corpus(count)]
        received, payload_bytes, parse_ms, failures, seq = 0, 0, [], [], -1
        async with connect(url, compression=None, max_size=8 * 1024 * 1024) as ws:
            greeting = json.loads(await ws.recv())
            revision = greeting["sceneRevision"]
            request = {
                "type": "client.subscribe",
                "protocolVersion": 1,
                "sceneRevision": revision,
                "scope": "all",
                "rateHz": 30,
            }
            if format_name != "json":
                request["format"] = format_name
            await ws.send(json.dumps(request))
            announced = False
            while not stop.is_set():
                try:
                    payload = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except TimeoutError:
                    continue
                start = time.perf_counter()
                if isinstance(payload, bytes):
                    magic, protocol, kind, numeric, epoch, sequence, sim_time, sample_time, body_count = (
                        HEADER.unpack_from(payload)
                    )
                    assert magic == b"NSYN" and protocol == 1 and kind in (1, 2)
                    assert numeric == greeting["revisionNumericId"] and len(payload) == 44 + 32 * body_count
                    packed = np.frombuffer(
                        payload, dtype=np.dtype([("id", "<u4"), ("pose", "<f4", (7,))]), offset=44
                    )
                    value = {
                        "type": "stream.snapshot" if kind == 2 else "transform.frame",
                        "seq": sequence,
                        "simTime": sim_time,
                        "poses": np.column_stack((packed["id"], packed["pose"])),
                    }
                else:
                    value = json.loads(payload)
                if value.get("type") == "subscription.ack":
                    assert value["format"] == format_name
                if value.get("type") not in ("stream.snapshot", "transform.frame"):
                    continue
                assert isinstance(payload, bytes) == (format_name == "binary")
                try:
                    phase = round(value["simTime"] * 240) % 8
                    verify(value["poses"], expected[phase])
                    if value["seq"] <= seq:
                        continue
                    seq = value["seq"]
                except Exception as exc:
                    failures.append(str(exc))
                    continue
                elapsed = (time.perf_counter() - start) * 1000
                if not announced:
                    ready.put(True)
                    announced = True
                if go.is_set():
                    received += 1
                    payload_bytes += len(payload.encode() if isinstance(payload, str) else payload)
                    parse_ms.append(elapsed)
        results.put(
            {
                "validAppliedFrames": received,
                "payloadBytes": payload_bytes,
                "parseVerifyP95Ms": percentile(parse_ms),
                "failures": failures,
            }
        )

    try:
        asyncio.run(run())
    except BaseException as exc:
        ready.put(False)
        results.put({"failures": [f"{type(exc).__name__}: {exc}"], "validAppliedFrames": 0})


def network(count, clients, seconds, format_name, port):
    import httpx

    context = mp.get_context("spawn")
    ready, results, go, stop = context.Queue(), context.Queue(), context.Event(), context.Event()
    log_path = ROOT / f".scratch/p2-a/network-{count}-{clients}-{format_name}.log"
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [sys.executable, __file__, "serve", "--count", str(count), "--port", str(port)],
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        readers = []
        try:
            deadline = time.monotonic() + 30
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as client:
                while True:
                    try:
                        if client.get("/api/v1/health").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    if time.monotonic() > deadline:
                        raise TimeoutError("profile server did not start")
                    time.sleep(0.05)
                response = client.post(
                    "/api/v1/sessions", json={"sceneId": "lab-demo", "source": "synthetic"}
                )
                response.raise_for_status()
                sid = response.json()["sessionId"]
                url = f"ws://127.0.0.1:{port}/api/v1/sessions/{sid}/ws"
                for _ in range(clients):
                    process = context.Process(
                        target=reader, args=(url, count, format_name, ready, results, go, stop)
                    )
                    process.start()
                    readers.append(process)
                assert all(ready.get(timeout=30) for _ in readers), "profile reader failed"
                time.sleep(1)  # Explicit warmup, outside the measurement window.
                before = client.get("/profile").json()
                started = time.monotonic()
                go.set()
                time.sleep(seconds)
                after = client.get("/profile").json()
                elapsed = time.monotonic() - started
                stop.set()
                records = [results.get(timeout=30) for _ in readers]
                assert all(
                    record["validAppliedFrames"] > 0 and not record["failures"] for record in records
                ), records
                return {
                    "bodies": count,
                    "clients": clients,
                    "format": format_name,
                    "seconds": elapsed,
                    "compression": "disabled equally at server and clients",
                    "readers": records,
                    "before": before,
                    "after": after,
                }
        finally:
            stop.set()
            for process in readers:
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
            server.terminate()
            server.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("codec", "network", "serve"))
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--clients", type=int, default=8)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--format", choices=("json", "binary"), default="json")
    parser.add_argument("--port", type=int, default=8005)
    parser.add_argument("--iterations", type=int, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.mode == "serve":
        serve(args.count, args.port)
        return
    result = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "versions": {
            name: version(name) for name in ("pydantic", "numpy", "fastapi", "uvicorn", "websockets")
        },
        "corpusHash": hashlib.sha256(
            json.dumps(corpus(args.count), separators=(",", ":")).encode()
        ).hexdigest(),
        "mode": args.mode,
        "results": codecs(args.iterations)
        if args.mode == "codec"
        else network(args.count, args.clients, args.seconds, args.format, args.port),
    }
    rendered = json.dumps(result, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()

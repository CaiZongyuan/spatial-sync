"""Real localhost information-plane benchmark; correctness is checked before budgets.

JSON goes to stdout (and --output); compact human tables go to stderr.
The temporary fixture extends the production Synthetic scene with unplaced devices.
No benchmark HTTP endpoints or alternate gateway paths are installed.
After the Synthetic gateway exits, the real log benchmark runs separately;
--include-newton also measures the real CPU Newton host in its own gateway.
--rate-hz limits publication; --generation-hz controls real per-entity writes
and defaults to the same rate. Pass --generation-hz 60 for the earlier stress.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
import platform
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import orjson
import yaml
from pydantic import PositiveFloat, TypeAdapter
from websockets.asyncio.client import connect

from packages.info.program import ProgramConfig
from packages.protocol.schema import EntityObservations, ServerMessage
from packages.transport import FrameCodec

CONTROL = "/Bench/Controller"
PREFIX = "/Bench/Telemetry/"
CENTRIFUGE = "/World/Lab/Centrifuge"
WAITING = set()


class ControllerProgram:
    id, type, tick_priority = "bench.controller", "bench-controller/v1", 0

    class Config(ProgramConfig):
        pass

    def init(self, ctx):
        ctx.set(enabled=False, calls=0, marker=0)

    def accept(self, inv, ctx):
        return None

    def start(self, inv, ctx):
        ctx.set(calls=ctx.get("calls") + 1)
        if inv.action == "switch":
            ctx.set(enabled=inv.params["enabled"])
        elif inv.action == "mark":
            ctx.set(marker=inv.params["value"])
        inv.succeed({"echo": inv.params.get("echo"), "calls": ctx.get("calls")})

    def tick(self, ctx, dt):
        pass

    def cancel(self, inv, ctx):
        inv.cancelled()


class TelemetryProgram:
    id, type, tick_priority = "bench.telemetry", "bench-telemetry/v1", 1000

    class Config(ProgramConfig):
        controller: str = CONTROL
        generationHz: PositiveFloat = 60

    def init(self, ctx):
        self._elapsed = 0.0
        ctx.set(value=0)

    def accept(self, inv, ctx):
        return None

    def tick(self, ctx, dt):
        if not ctx.read(ctx.config.controller, "enabled"):
            self._elapsed = 0.0
            return
        self._elapsed += dt
        period = 1 / ctx.config.generationHz
        while self._elapsed + 1e-10 >= period:
            self._elapsed -= period
            ctx.set(value=ctx.get("value") + 1)


def telemetry_rows(changes):
    """Identical digest rows from typed Source changes or parsed client JSON."""
    for change in changes:
        document = isinstance(change, dict)
        key = change["key"] if document else change.key
        if key.startswith(PREFIX):
            value = change["values"]["value"] if document else change.values["value"]
            if type(value) is not int or value < 0:
                raise AssertionError("Telemetry did not contain its declared nonnegative integer counter")
            yield int(key.removeprefix(PREFIX)), change["rev"] if document else change.rev, value


def update_digest(digest, rows):
    count = 0
    for index, rev, value in rows:
        digest.update(struct.pack("<QQQ", index, rev, value))
        count += 1
    return count


def fixture(directory, count, rate, generation=None):
    destination = directory / "devices"
    assert destination.resolve().is_relative_to(directory.resolve())
    shutil.copytree(ROOT / "devices", destination)
    controller = {
        "type": ControllerProgram.type,
        "title": "Benchmark controller",
        "properties": {
            "enabled": {"schema": {"type": "boolean"}},
            "calls": {"schema": {"type": "integer", "minimum": 0}},
            "marker": {"schema": {"type": "integer", "minimum": 0}},
        },
        "actions": {
            "switch": {"params": {"enabled": {"type": "boolean"}}, "required": ["enabled"]},
            "ping": {"params": {"echo": {"type": "integer"}}, "required": ["echo"], "concurrent": True},
            "mark": {
                "params": {"value": {"type": "integer", "minimum": 0}},
                "required": ["value"],
                "concurrent": True,
            },
        },
    }
    telemetry = {
        "type": TelemetryProgram.type,
        "title": "Benchmark telemetry",
        "properties": {
            "value": {"schema": {"type": "integer", "minimum": 0}, "unit": "count", "maxRateHz": rate}
        },
    }
    for name, definition in [("bench-controller.v1", controller), ("bench-telemetry.v1", telemetry)]:
        (destination / "types" / f"{name}.yaml").write_text(yaml.safe_dump(definition), encoding="utf-8")
    scene_path = destination / "scenes/lab-demo.yaml"
    scene = yaml.safe_load(scene_path.read_text(encoding="utf-8"))
    scene["entities"].append(
        {"key": CONTROL, "type": ControllerProgram.type, "program": ControllerProgram.id, "unplaced": True}
    )
    scene["entities"].extend(
        {
            "key": f"{PREFIX}{i:08d}",
            "type": TelemetryProgram.type,
            "program": TelemetryProgram.id,
            "config": {"generationHz": rate if generation is None else generation},
            "unplaced": True,
        }
        for i in range(count)
    )
    scene_path.write_text(yaml.safe_dump(scene), encoding="utf-8")
    return destination


def serve(args):
    import uvicorn

    from apps.api.app.main import create_app
    from apps.api.app.sources import synthetic_source
    from packages.info.catalog import load_catalog
    from packages.info.store import StateStore

    original_wait = StateStore.wait

    async def observed_wait(self, key, prop, predicate, timeout):
        token = (id(self), key, prop)
        WAITING.add(token)
        try:
            return await original_wait(self, key, prop, predicate, timeout)
        finally:
            WAITING.discard(token)

    StateStore.wait = observed_wait
    synthetic_source.REGISTRY = {
        **synthetic_source.REGISTRY,
        ControllerProgram.id: ControllerProgram,
        TelemetryProgram.id: TelemetryProgram,
    }
    synthetic_source.load_catalog = lambda manifest, scene, registry: load_catalog(
        manifest, scene, registry, root=args.fixture_dir
    )

    class MeasuredSource(synthetic_source.SyntheticSource):
        async def start(self, scene, options):
            result = await super().start(scene, options)
            self.digest = hashlib.sha256()
            self.changes = self.batches = 0
            self.last_values = [0] * args.entities
            self.readers = [self.host.reader(f"{PREFIX}{i:08d}", "value") for i in range(args.entities)]
            self.calls = self.host.reader(CONTROL, "calls")
            return result

        async def events(self):
            async for event in super().events():
                if isinstance(event, EntityObservations):
                    rows = list(telemetry_rows(event.changes))
                    if rows:
                        self.changes += update_digest(self.digest, rows)
                        self.batches += 1
                        for index, _, value in rows:
                            self.last_values[index] = value
                yield event

        def stats(self):
            current_values = [read() for read in self.readers]
            return {
                "bench": {
                    "clockMs": time.monotonic() * 1000,
                    "changes": self.changes,
                    "batches": self.batches,
                    "digest": self.digest.hexdigest(),
                    "generatedWrites": sum(current_values),
                    "pendingValues": sum(
                        current != value
                        for current, value in zip(current_values, self.last_values, strict=True)
                    ),
                    "calls": self.calls(),
                    "registeredWaiters": len(WAITING),
                }
            }

    app = create_app(source_factory=lambda kind, sid: MeasuredSource(sid))
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", ws_per_message_deflate=False)


class Reader:
    def __init__(self, url, manifest, entities):
        self.url, self.manifest, self.entities = url, manifest, entities
        self.digest = hashlib.sha256()
        self.info_digest = hashlib.sha256()
        self.info_messages = 0
        self.changes = self.batches = 0
        self.revisions = {}
        self.values = {}
        self.invocations = {}
        self.markers = {}
        self.frames = []
        self.latencies = []
        self.change_samples = []
        self.ready = asyncio.Event()
        self.stopped = False
        self.error = None
        self.socket = None

    async def run(self):
        codec = FrameCodec(
            self.manifest["sessionId"], self.manifest["sceneRevision"], self.manifest["revisionNumericId"]
        )
        body_ids = {node["streamId"] for node in self.manifest["nodes"] if node["kind"] == "body"}
        try:
            async with connect(self.url, compression=None, proxy=None, max_size=64 * 1024 * 1024) as ws:
                self.socket = ws
                greeting = orjson.loads(await ws.recv())
                assert greeting["type"] == "server.ready" and greeting["protocolVersion"] == 2
                await ws.send(
                    orjson.dumps(
                        {
                            "type": "client.subscribe",
                            "protocolVersion": 2,
                            "sceneRevision": self.manifest["sceneRevision"],
                            "info": True,
                        }
                    ).decode()
                )
                last_frame = -1
                async for payload in ws:
                    received = time.monotonic() * 1000
                    if isinstance(payload, bytes):
                        frame = codec.decode(payload)
                        assert {row[0] for row in frame.poses} == body_ids and frame.seq > last_frame
                        last_frame = frame.seq
                        self.frames.append(received)
                        continue
                    message = orjson.loads(payload)
                    kind = message["type"]
                    if kind in ("entity.observations", "entity.event", "invocation.update"):
                        packed = orjson.dumps(message, option=orjson.OPT_SORT_KEYS)
                        self.info_digest.update(struct.pack("<I", len(packed)) + packed)
                        self.info_messages += 1
                    if kind == "entity.snapshot":
                        assert message["sceneRevision"] == self.manifest["sceneRevision"]
                        keys = {item["key"] for item in message["entities"] if item["key"].startswith(PREFIX)}
                        assert keys == {f"{PREFIX}{i:08d}" for i in range(self.entities)}
                        self.ready.set()
                    elif kind == "entity.observations":
                        assert (
                            message["epoch"] == 0
                            and message["sceneRevision"] == self.manifest["sceneRevision"]
                        )
                        rows = list(telemetry_rows(message["changes"]))
                        if rows:
                            for index, rev, value in rows:
                                assert 0 <= index < self.entities
                                assert rev > self.revisions.get(index, -1)
                                assert value > self.values.get(index, -1)
                                self.revisions[index], self.values[index] = rev, value
                            self.changes += update_digest(self.digest, rows)
                            self.batches += 1
                            self.change_samples.append((received, len(rows)))
                            latency = received - message["sampleTimeMs"]
                            assert latency >= 0
                            self.latencies.append((message["sampleTimeMs"], latency))
                        for change in message["changes"]:
                            if change["key"] == CONTROL and "marker" in change["values"]:
                                self.markers[change["values"]["marker"]] = message["sampleTimeMs"]
                    elif kind == "invocation.update":
                        previous = self.invocations.get(message["invocationId"])
                        if previous is None:
                            assert message["status"] in ("accepted", "rejected"), message
                        if previous:
                            assert previous["status"] not in ("succeeded", "failed", "cancelled", "rejected")
                            assert not (previous["status"] == "running" and message["status"] == "accepted")
                        self.invocations[message["invocationId"]] = message
                    elif kind == "server.error":
                        raise AssertionError(f"Gateway error: {message}")
        except BaseException as exc:
            if not self.stopped:
                self.error = f"{type(exc).__name__}: {exc}"
            self.ready.set()

    async def close(self):
        self.stopped = True
        if self.socket:
            await self.socket.close()


def distribution(values):
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "p50Ms": ordered[math.ceil(len(ordered) * 0.5) - 1] if ordered else None,
        "p95Ms": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
        "maxMs": ordered[-1] if ordered else None,
    }


async def eventually(predicate, *, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        result = await predicate()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError("Benchmark readiness/flush predicate did not become true")
        await asyncio.sleep(0.005)


async def benchmark(args, client, route, readers):
    sequence = 0
    accepted_ids = set()
    wake_samples = []
    windows = []
    cursor = (await client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0})).json()["lastSeq"]
    assert all(reader.info_messages == 0 for reader in readers), "Work started before the shared cursor"

    async def post(name, params, *, wait=False):
        nonlocal sequence
        sequence += 1
        response = await client.post(
            route + "/actions",
            json={
                "requestId": f"bench-{sequence}",
                "entityKey": CONTROL,
                "action": name,
                "params": params,
                "wait": wait,
                "timeoutSeconds": 10,
            },
        )
        assert response.status_code in (200, 202), response.text
        value = response.json()
        assert value["status"] in ("accepted", "running", "succeeded"), value
        accepted_ids.add(value["invocationId"])
        return value

    async def stats():
        response = await client.get(route + "/stats")
        response.raise_for_status()
        return response.json()

    async def wake():
        token = sequence + 1
        waiter = asyncio.create_task(
            client.get(
                route + "/wait",
                params={
                    "key": CONTROL,
                    "property": "marker",
                    "op": "equals",
                    "value": str(token),
                    "timeoutSeconds": 10,
                },
            )
        )
        try:

            async def registered():
                return (await stats())["bench"]["registeredWaiters"] > 0

            await eventually(registered)
            await post("mark", {"value": token})
            response = await waiter
            completed_at = time.monotonic() * 1000
            assert response.json() == {"satisfied": True, "value": token, "timedOut": False}

            async def marker_seen():
                return all(token in reader.markers for reader in readers)

            await eventually(marker_seen)
            assert readers[0].markers[token] == readers[1].markers[token]
            wake_samples.append(completed_at - readers[0].markers[token])
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)

    async def phase(enabled):
        nonlocal sequence
        await post("switch", {"enabled": enabled}, wait=True)
        before = None
        samples = {
            name: []
            for name in (
                "rejectParams",
                "rejectCondition",
                "accept",
                "getStateOne",
                "getStateAll",
                "describe",
            )
        }
        started, next_request = time.monotonic(), time.monotonic()
        warmup_until = started + args.warmup_seconds
        attempts = late = 0
        operations = 0
        while time.monotonic() < warmup_until + args.seconds:
            if before is None and time.monotonic() >= warmup_until:
                before = await stats()
            name = list(samples)[operations % len(samples)]
            tick = time.monotonic()
            if name == "rejectParams":
                sequence += 1
                response = await client.post(
                    route + "/actions",
                    json={
                        "requestId": f"reject-{sequence}",
                        "entityKey": CONTROL,
                        "action": "ping",
                        "params": {"echo": "invalid"},
                    },
                )
                expected = (422, "invalid_params")
            elif name == "rejectCondition":
                sequence += 1
                response = await client.post(
                    route + "/actions",
                    json={
                        "requestId": f"condition-{sequence}",
                        "entityKey": CENTRIFUGE,
                        "action": "start",
                        "params": {"rpm": 3000, "durationSeconds": 1},
                    },
                )
                expected = (409, "precondition_failed")
            elif name == "accept":
                value = await post("ping", {"echo": operations})
                response, expected = None, None
                assert value["params"]["echo"] == operations
            elif name.startswith("getState"):
                response = await client.get(
                    route + "/state", params={"keys": PREFIX + "00000000"} if name == "getStateOne" else {}
                )
                expected = None
            else:
                response = await client.get(route + "/entity", params={"key": PREFIX + "00000000"})
                expected = None
            if before is not None:
                samples[name].append((time.monotonic() - tick) * 1000)
            if response is not None:
                if expected:
                    assert (
                        response.status_code == expected[0]
                        and response.json()["error"]["code"] == expected[1]
                    )
                else:
                    response.raise_for_status()
                    data = response.json()
                    assert ("entities" in data and PREFIX + "00000000" in data["entities"]) or data.get(
                        "key"
                    ) == PREFIX + "00000000"
            operations += 1
            attempts += before is not None
            next_request += 1 / args.http_hz
            delay = next_request - time.monotonic()
            if delay < 0:
                late += before is not None
                next_request = time.monotonic()
            else:
                await asyncio.sleep(delay)
        after = await stats()
        assert before is not None, "No measured phase followed the warmup"
        elapsed = (after["bench"]["clockMs"] - before["bench"]["clockMs"]) / 1000
        begin_ms, end_ms = before["bench"]["clockMs"], after["bench"]["clockMs"]
        windows.append((begin_ms, end_ms))
        frames = [sum(begin_ms <= stamp <= end_ms for stamp in reader.frames) for reader in readers]
        reader_changes = [
            sum(count for stamp, count in reader.change_samples if begin_ms <= stamp <= end_ms)
            for reader in readers
        ]
        for _ in range(args.wake_samples):
            await wake()
        assert all(values for values in samples.values()), "Every HTTP operation needs actual samples"
        return {
            "telemetryEnabled": enabled,
            "seconds": elapsed,
            "httpAttempts": attempts,
            "httpCompletedHz": attempts / elapsed,
            "missedRequestDeadlines": late,
            "transformFrames": frames,
            "transformHz": [count / elapsed for count in frames],
            "sourceTransformHz": (after["publishedFrames"] - before["publishedFrames"]) / elapsed,
            "propertyChanges": after["bench"]["changes"] - before["bench"]["changes"],
            "propertyChangesPerSecond": (after["bench"]["changes"] - before["bench"]["changes"]) / elapsed,
            "generatedWrites": after["bench"]["generatedWrites"] - before["bench"]["generatedWrites"],
            "generatedWritesPerSecond": (
                after["bench"]["generatedWrites"] - before["bench"]["generatedWrites"]
            )
            / elapsed,
            "readerPropertyChanges": reader_changes,
            "readerPropertyChangesPerSecond": [count / elapsed for count in reader_changes],
            "sustainedThroughputBudget": {
                "requiredChangesPerSecond": 10_000,
                "applicable": enabled,
                "satisfied": None
                if not enabled
                else min(
                    (after["bench"]["changes"] - before["bench"]["changes"]) / elapsed,
                    *(count / elapsed for count in reader_changes),
                )
                >= 10_000,
            },
            "http": {name: distribution(values) for name, values in samples.items()},
        }

    repeats = []
    for index in range(args.repeats):
        phases = {}
        for enabled in (False, True) if index % 2 == 0 else (True, False):
            phases["loaded" if enabled else "baseline"] = await phase(enabled)
        degradation = [
            1 - loaded / baseline
            for loaded, baseline in zip(
                phases["loaded"]["transformHz"], phases["baseline"]["transformHz"], strict=True
            )
        ]
        repeats.append({**phases, "transformCadenceDegradation": degradation})
    await post("switch", {"enabled": False}, wait=True)

    async def drained():
        snapshot = await stats()
        for reader in readers:
            assert reader.error is None, reader.error
        completed = all(
            all(reader.invocations.get(key, {}).get("status") == "succeeded" for key in accepted_ids)
            for reader in readers
        )
        if (
            snapshot["bench"]["pendingValues"] == 0
            and completed
            and all(reader.changes == snapshot["bench"]["changes"] for reader in readers)
        ):
            return snapshot
        return None

    final = await eventually(drained, timeout=15)
    assert final["bench"]["changes"] > 0
    assert all(reader.digest.hexdigest() == final["bench"]["digest"] for reader in readers)
    assert all(reader.batches == final["bench"]["batches"] for reader in readers)
    assert final["bench"]["calls"] == len(accepted_ids)
    assert final["bench"]["generatedWrites"] == sum(readers[0].values.values())
    assert all(len(reader.values) == args.entities for reader in readers)
    current = (await client.get(route + "/state")).json()["entities"]
    for index in range(args.entities):
        entity = current[f"{PREFIX}{index:08d}"]
        assert entity["values"]["value"] == readers[0].values[index] == readers[1].values[index]
        assert entity["rev"] == readers[0].revisions[index] == readers[1].revisions[index]
        assert entity["rev"] == entity["values"]["value"] + 1
        assert entity["meta"]["value"]["source"] == "virtual"
    for invocation_id in accepted_ids:
        invocation = readers[0].invocations[invocation_id]
        if invocation["action"] == "ping":
            assert invocation["result"]["echo"] == invocation["params"]["echo"]
    assert all(not item["reliableQueueDepth"] for item in final["clients"])
    history = (await client.get(route + "/events", params={"after": cursor, "timeoutSeconds": 0})).json()
    assert not history["gap"], "Gateway event history overflowed; shorten this correctness cohort"
    gateway_digest = hashlib.sha256()
    adapter = TypeAdapter(ServerMessage)
    for entry in history["events"]:
        normalized = adapter.validate_python(entry["message"]).model_dump(mode="json", exclude_none=True)
        packed = orjson.dumps(normalized, option=orjson.OPT_SORT_KEYS)
        gateway_digest.update(struct.pack("<I", len(packed)) + packed)
    assert all(reader.info_messages == len(history["events"]) for reader in readers)
    assert all(reader.info_digest.hexdigest() == gateway_digest.hexdigest() for reader in readers)
    return {
        "repeats": repeats,
        "observationLatency": [
            distribution(
                [
                    latency
                    for stamp, latency in reader.latencies
                    if any(start <= stamp <= end for start, end in windows)
                ]
            )
            for reader in readers
        ],
        "waitWakeLatency": distribution(wake_samples),
        "correctness": {
            "sourceChanges": final["bench"]["changes"],
            "generatedWrites": final["bench"]["generatedWrites"],
            "generatedCountersMatchRevisions": True,
            "sourceObservationBatches": final["bench"]["batches"],
            "sourceDigest": final["bench"]["digest"],
            "readerChanges": [reader.changes for reader in readers],
            "readerDigests": [reader.digest.hexdigest() for reader in readers],
            "completedHostActions": final["bench"]["calls"],
            "acceptedActions": len(accepted_ids),
            "twoClientReliableLoss": 0,
            "gatewayReliableMessages": len(history["events"]),
            "readerReliableMessages": [reader.info_messages for reader in readers],
            "gatewayReliableDigest": gateway_digest.hexdigest(),
            "invocationOrderValid": True,
            "pendingValues": 0,
        },
    }


async def run(args, directory, log_path):
    fixture_root = fixture(directory, args.entities, args.rate_hz, args.generation_hz)
    process = None
    readers, tasks = [], []
    route = None
    execution = {"gatewayPid": None, "gatewayExited": False}
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", args.port))
    with log_path.open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    __file__,
                    "--serve",
                    "--fixture-dir",
                    str(fixture_root),
                    "--entities",
                    str(args.entities),
                    "--port",
                    str(args.port),
                ],
                cwd=ROOT,
                env=os.environ | {"SPATIAL_RECORDINGS_ROOT": str(directory / "recordings")},
                stdout=log,
                stderr=log,
            )
            execution["gatewayPid"] = process.pid
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{args.port}",
                timeout=30,
                trust_env=False,
                limits=httpx.Limits(max_connections=20),
            ) as client:

                async def healthy():
                    if process.poll() is not None:
                        raise RuntimeError("Benchmark gateway exited during startup")
                    try:
                        return (await client.get("/api/v1/health")).status_code == 200
                    except httpx.ConnectError:
                        return False

                await eventually(healthy, timeout=30)
                created = await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
                created.raise_for_status()
                session_id = created.json()["sessionId"]
                route = f"/api/v1/sessions/{session_id}"
                manifest = (await client.get(route + "/scene")).json()
                catalog = (await client.get(route + "/catalog")).json()
                assert len([key for key in catalog["entities"] if key.startswith(PREFIX)]) == args.entities
                for _ in range(2):
                    reader = Reader(f"ws://127.0.0.1:{args.port}{route}/ws", manifest, args.entities)
                    readers.append(reader)
                    tasks.append(asyncio.create_task(reader.run()))
                await asyncio.wait_for(asyncio.gather(*(reader.ready.wait() for reader in readers)), 10)
                assert all(reader.error is None for reader in readers), [reader.error for reader in readers]
                result = await benchmark(args, client, route, readers)
                result["scene"] = {
                    "telemetryEntities": args.entities,
                    "catalogEntities": len(catalog["entities"]),
                    "streamedBodies": sum(node["kind"] == "body" for node in manifest["nodes"]),
                }
                result["execution"] = execution
                (await client.delete(route)).raise_for_status()
                route = None
                return result
        finally:
            if route and process and process.poll() is None:
                with contextlib.suppress(Exception):
                    async with httpx.AsyncClient(timeout=5, trust_env=False) as cleanup:
                        await cleanup.delete(f"http://127.0.0.1:{args.port}{route}")
            await asyncio.gather(*(reader.close() for reader in readers), return_exceptions=True)
            await asyncio.gather(*tasks, return_exceptions=True)
            if process:
                if process.poll() is None:
                    process.terminate()
                try:
                    await asyncio.wait_for(asyncio.to_thread(process.wait), 5)
                except TimeoutError:
                    process.kill()
                    await asyncio.to_thread(process.wait)
                execution["gatewayExited"] = process.poll() is not None
                execution["exitCode"] = process.returncode


async def postphases(args, directory):
    """Run only after Synthetic cleanup, so the measured workloads do not overlap."""
    from tools.bench_newton_info import probe_newton_info

    newton = {"status": "unavailable", "reason": "not_requested; use --include-newton"}
    if args.include_newton:
        newton = await probe_newton_info(python=args.newton_python, port=args.port)
    log_tool = ROOT / "tools/bench_recording.py"
    if not log_tool.is_file():
        return {"newton": newton, "logThroughput": {"status": "unavailable", "reason": "i4_not_integrated"}}
    from tools.bench_recording import benchmark_log

    log = {
        "status": "unverified",
        "toolSha256": hashlib.sha256(log_tool.read_bytes()).hexdigest(),
        "configuration": {"frames": 1024, "bodiesPerFrame": 2048},
        "method": {"eventLoop": "2ms scheduling probe; report delay without inventing a numeric budget"},
    }
    try:
        log["measurements"] = await benchmark_log(directory / "session-log-benchmark")
        log["budgets"] = {"throughput5MBps": log["measurements"]["meetsThroughputBudget"]}
        log["status"] = "measured"
    except Exception as exc:
        log.update(status="invalid", error=f"{type(exc).__name__}: {exc}")
    return {"newton": newton, "logThroughput": log}


def print_postphases(measurements):
    print("phase | metric | observed | budget passed", file=sys.stderr)
    native = measurements["newton"]
    if native["status"] == "measured":
        measured, budgets = native["measurements"], native["budgets"]
        for label, value, budget in (
            ("HTTP accept p95 ms", measured["accept"]["p95Ms"], "accept25ms"),
            (
                "WS observation p95 ms (worst reader)",
                max(v["p95Ms"] for v in measured["observation"]),
                "observation20ms",
            ),
            (
                "source control tick p95 ms (final window)",
                measured["reportedFinalTickP95Ms"],
                "sourceTick2ms",
            ),
            ("registered wait wake p95 ms", measured["waitWake"]["p95Ms"], "waitWake5ms"),
            (
                "two-client reliable loss",
                measured["correctness"]["twoClientReliableLoss"],
                "twoClientReliableLossZero",
            ),
        ):
            print(f"Newton | {label} | {value:.3f} | {budgets[budget]}", file=sys.stderr)
    else:
        print(
            f"Newton | {native['status']} | {native.get('reason', native.get('error'))} | n/a",
            file=sys.stderr,
        )
    log = measurements["logThroughput"]
    if log["status"] == "measured":
        measured = log["measurements"]
        print(
            f"log | verified buffered MB/s | {measured['megabytesPerSecond']:.3f} | "
            f"{log['budgets']['throughput5MBps']}",
            file=sys.stderr,
        )
        print(
            f"log | event-loop delay p95 ms | {measured['loopDelayP95Ms']:.3f} | no numeric budget",
            file=sys.stderr,
        )
    else:
        print(f"log | {log['status']} | {log.get('reason', log.get('error'))} | n/a", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", type=int, default=1000)
    parser.add_argument("--rate-hz", type=float, default=10)
    parser.add_argument(
        "--generation-hz",
        type=float,
        help="actual writes/s; defaults to --rate-hz (60 reproduces prior stress)",
    )
    parser.add_argument("--http-hz", type=float, default=50)
    parser.add_argument("--seconds", type=float, default=3)
    parser.add_argument("--warmup-seconds", type=float, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--wake-samples", type=int, default=3)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--include-newton", action="store_true", help="After Synthetic cleanup, probe the real CPU host"
    )
    parser.add_argument(
        "--newton-python", type=Path, help="Optional Python with the simulation extra for the Newton gateway"
    )
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--fixture-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.generation_hz is None:
        args.generation_hz = args.rate_hz
    if args.serve:
        serve(args)
        return
    if any(
        not math.isfinite(value) or value <= 0
        for value in (
            args.entities,
            args.rate_hz,
            args.generation_hz,
            args.http_hz,
            args.seconds,
            args.repeats,
            args.wake_samples,
        )
    ):
        parser.error("Workload sizes, rates, durations and sample counts must be positive")
    if not math.isfinite(args.warmup_seconds) or args.warmup_seconds < 0:
        parser.error("Warmup must be finite and nonnegative")
    result = {
        "status": "unverified",
        "configuration": vars(args)
        | {
            "output": str(args.output) if args.output else None,
            "newton_python": str(args.newton_python) if args.newton_python else None,
            "fixture_dir": None,
        },
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "versions": {
                name: version(name) for name in ("pydantic", "httpx", "websockets", "uvicorn", "orjson")
            },
        },
        "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "toolSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "method": {
            "clock": "same-machine system monotonic milliseconds; no cross-host clock subtraction",
            "p95": "nearest-rank; all recorded samples, with setup excluded",
            "http": "request to response; accept includes validation; state one/all distinguished",
            "describe": "one selected entity in the N-entity loaded scene",
            "observation": "host sampleTimeMs to ws.recv completion before JSON parsing; per batch",
            "wait": "registered waiter: host marker sampleTimeMs to HTTP response receipt",
            "load": "same HTTP mix and two readers for baseline/load; alternating repeat order",
            "instrumentation": "source/reader SHA256 of index/rev/value; Source.stats and waiter counters",
            "generation": (
                "dt-accumulated real writes; disabled time resets accumulation; "
                "each write increments counter/revision once"
            ),
            "throughput": (
                "actual generated counter increments and separately published Source/two-reader rows "
                "per measured window; never theoretical entity*Hz"
            ),
            "readerThroughput": (
                "fixed same-machine receive-time window; "
                "receive completion timestamp is taken before JSON parsing"
            ),
            "limits": "CPU contention and instrumentation included; interpret budgets separately",
            "correctnessCohort": "all post-cursor messages through drain, including warmup and controls",
            "phases": "Synthetic cleanup, optional Newton cleanup, then actual log drain/flush",
            "status": "measured means valid work; each unchanged performance budget is evaluated separately",
        },
    }
    try:
        with tempfile.TemporaryDirectory(prefix="spatial-info-bench-") as temporary:
            directory = Path(temporary).resolve()
            assert directory.name.startswith("spatial-info-bench-")
            log_path = directory / "gateway.log"
            try:
                result["measurements"] = asyncio.run(run(args, directory, log_path))
            except Exception:
                result["gatewayLogTail"] = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                raise
            result["measurements"].update(asyncio.run(postphases(args, directory)))
        result["status"] = "measured"
        if any(result["measurements"][name]["status"] == "invalid" for name in ("newton", "logThroughput")):
            result.update(
                status="invalid", error="A sequential phase failed correctness; inspect its receipt"
            )
    except Exception as exc:
        result["status"], result["error"] = "invalid", f"{type(exc).__name__}: {exc}"
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if "measurements" in result:
        print(
            "repeat | baseline transform Hz | loaded transform Hz | changes/s | HTTP accept p95 ms",
            file=sys.stderr,
        )
        for i, item in enumerate(result["measurements"]["repeats"]):
            print(
                f"{i + 1} | {item['baseline']['transformHz']} | {item['loaded']['transformHz']} | "
                f"{item['loaded']['propertyChangesPerSecond']:.1f} | "
                f"{item['loaded']['http']['accept']['p95Ms']:.3f}",
                file=sys.stderr,
            )
        if "newton" in result["measurements"]:
            print_postphases(result["measurements"])
    if result["status"] != "measured":
        raise SystemExit(1)


if __name__ == "__main__":
    main()

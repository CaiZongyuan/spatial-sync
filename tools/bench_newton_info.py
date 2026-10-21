"""Real CPU Newton information-path probe, run separately from other benchmark phases.

The async entrypoint owns and cleans up its gateway. Importing it never imports
Newton. The only optional instrumentation observes real StateStore wait entry
through the existing stats route; no product endpoint or alternate Source exists.
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
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import orjson
from pydantic import TypeAdapter
from websockets.asyncio.client import connect

from packages.protocol.schema import ServerMessage
from packages.transport import FrameCodec

ROBOT = "/World/Lab/RobotA"
INFO = {"entity.snapshot", "entity.observations", "entity.event", "invocation.update"}
TERMINAL = {"succeeded", "rejected", "failed", "cancelled"}
ADAPTER = TypeAdapter(ServerMessage)


def distribution(values):
    values = sorted(values)
    return {
        "samples": len(values),
        "p50Ms": values[math.ceil(len(values) * 0.5) - 1] if values else None,
        "p95Ms": values[math.ceil(len(values) * 0.95) - 1] if values else None,
        "maxMs": values[-1] if values else None,
    }


def packed(message):
    normalized = ADAPTER.validate_python(message).model_dump(mode="json", exclude_none=True)
    return orjson.dumps(normalized, option=orjson.OPT_SORT_KEYS)


def digest(messages):
    result = hashlib.sha256()
    for message in messages:
        result.update(struct.pack("<I", len(message)) + message)
    return result.hexdigest()


def match_cohort(messages, expected):
    """Permit only delivery before/after the declared shared public-event cohort."""
    candidates = [index for index, message in enumerate(messages) if message == expected[0]]
    assert len(candidates) == 1, "Missing or duplicated first public cohort message"
    prefix = candidates[0]
    cohort = messages[prefix : prefix + len(expected)]
    assert cohort == expected, "Reliable Native messages were lost, duplicated or reordered"
    return prefix, digest(cohort)


async def eventually(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        value = await predicate()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError("Native benchmark readiness/drain condition was not satisfied")
        await asyncio.sleep(0.005)


class Reader:
    def __init__(self, url, manifest):
        self.url, self.manifest = url, manifest
        self.ready = asyncio.Event()
        self.messages, self.observations, self.frames = [], [], []
        self.entities, self.invocations = {}, {}
        self.socket, self.error = None, None
        self.stopped = False

    def apply_values(self, change, sample_time):
        prior = self.entities.get(change["key"], {"rev": -1, "values": {}, "times": {}})
        if change["rev"] < prior["rev"]:
            return
        values, times = dict(prior["values"]), dict(prior["times"])
        for prop, value in change["values"].items():
            if sample_time >= times.get(prop, -1):
                values[prop], times[prop] = value, sample_time
        self.entities[change["key"]] = {"rev": change["rev"], "values": values, "times": times}

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
                last_seq, initial = -1, True
                async for payload in ws:
                    received = time.monotonic() * 1000  # before parsing/validation by contract
                    if isinstance(payload, bytes):
                        frame = codec.decode(payload)
                        assert {row[0] for row in frame.poses} == body_ids and frame.seq > last_seq
                        assert frame.epoch == 0
                        last_seq = frame.seq
                        self.frames.append(received)
                        continue
                    message = orjson.loads(payload)
                    kind = message["type"]
                    if kind == "server.error":
                        raise AssertionError(f"Gateway error: {message}")
                    if kind not in INFO:
                        continue
                    assert (
                        message["sceneRevision"] == self.manifest["sceneRevision"] and message["epoch"] == 0
                    )
                    if kind == "entity.snapshot":
                        self.entities.clear()
                        self.invocations.clear()
                        for entity in message["entities"]:
                            self.entities[entity["key"]] = {
                                "rev": entity["rev"],
                                "values": dict(entity["values"]),
                                "times": {
                                    prop: meta["sampleTimeMs"] for prop, meta in entity["meta"].items()
                                },
                            }
                        for invocation in message["invocations"]:
                            self.invocations[invocation["invocationId"]] = invocation
                        if initial:
                            initial = False
                            assert self.frames, "Subscription must deliver its spatial snapshot first"
                            self.ready.set()
                            continue  # Per-connection initialization is outside the shared event corpus.
                    if kind == "entity.observations":
                        latency = received - message["sampleTimeMs"]
                        assert latency >= 0 and message["source"] == "sim"
                        self.observations.append((message["sampleTimeMs"], latency, message))
                        for change in message["changes"]:
                            self.apply_values(change, message["sampleTimeMs"])
                    elif kind == "invocation.update":
                        self.invocations[message["invocationId"]] = message
                    self.messages.append(packed(message))
        except BaseException as exc:
            if not self.stopped:
                self.error = f"{type(exc).__name__}: {exc}"
            self.ready.set()

    async def close(self):
        self.stopped = True
        if self.socket:
            await self.socket.close()


async def measure(client, route, readers, *, repeats, actions_per_repeat, warmup_actions, wait_samples):
    accepted, accept_times, tick_samples, waits, windows, phases = [], [], [], [], [], []
    actor = {"kind": "cli", "name": "bench_newton_info"}
    cursor = (await client.get(route + "/events", params={"after": 0, "timeoutSeconds": 0})).json()["lastSeq"]
    initial = (await client.get(route + "/state", params={"keys": ROBOT})).json()["entities"][ROBOT]
    width = 0 if initial["values"]["gripperWidth"] > 0.04 else 0.08

    async def stats():
        response = await client.get(route + "/stats")
        response.raise_for_status()
        value = response.json()
        assert value["source"] == "newton" and value["state"] == "RUNNING"
        assert value["controlTickCount"] > 0 and value["tickTimingSamples"] > 0
        return value

    async def action(measured=False):
        nonlocal width
        target = width
        width = 0.08 if width == 0 else 0
        started = time.monotonic() * 1000
        response = await client.post(
            route + "/actions",
            headers={"X-Actor": "cli:bench_newton_info"},
            json={
                "requestId": "native-bench-" + uuid.uuid4().hex,
                "entityKey": ROBOT,
                "action": "setGripper",
                "params": {"width": target},
                "wait": False,
            },
        )
        received = time.monotonic() * 1000
        assert response.status_code == 202, response.text
        invocation = response.json()
        assert invocation["status"] in ("accepted", "running") and invocation["actor"] == actor
        if measured:
            accept_times.append(received - started)
        final = await client.get(
            route + "/invocations/" + invocation["invocationId"],
            params={
                "wait": True,
                "timeoutSeconds": 30,
            },
        )
        final.raise_for_status()
        result = final.json()
        assert result["status"] == "succeeded" and not result["timedOut"], result
        assert result["actor"] == actor and result["params"] == {"width": target}

        async def final_width_published():
            current = (await client.get(route + "/state", params={"keys": ROBOT})).json()["entities"][ROBOT]
            return abs(current["values"]["gripperWidth"] - target) < 0.002

        await eventually(final_width_published)
        accepted.append(result)
        return result, started

    for _ in range(warmup_actions):
        await action()
    for _ in range(repeats):
        before = await stats()
        begin = time.monotonic() * 1000
        index = len(accept_times)
        snapshots = []
        for _ in range(actions_per_repeat):
            await action(measured=True)
            sample = await stats()
            snapshots.append(sample)
            tick_samples.append(sample["tick_ms"])
        await asyncio.sleep(0.15)  # include the real final 10Hz pending publications
        end = time.monotonic() * 1000
        windows.append((begin, end))
        after = await stats()
        assert after["controlTickCount"] > before["controlTickCount"]
        phases.append(
            {
                "seconds": (end - begin) / 1000,
                "accept": distribution(accept_times[index:]),
                "controlTicks": after["controlTickCount"] - before["controlTickCount"],
                "reportedTickP95Ms": after["tickP95Ms"],
                "tickTimingSamples": after["tickTimingSamples"],
                "sampledTickMs": distribution([sample["tick_ms"] for sample in snapshots]),
                "transformHz": [
                    sum(begin <= stamp <= end for stamp in reader.frames) * 1000 / (end - begin)
                    for reader in readers
                ],
            }
        )

    for _ in range(wait_samples):
        closing = width == 0
        op, threshold = ("lt", 0.03) if closing else ("gt", 0.05)
        predicate = (lambda value: value < threshold) if closing else (lambda value: value > threshold)
        current = (await client.get(route + "/state", params={"keys": ROBOT})).json()["entities"][ROBOT]
        assert not predicate(current["values"]["gripperWidth"]), (
            "Wait must begin with an unsatisfied condition"
        )
        waiter = asyncio.create_task(
            client.get(
                route + "/wait",
                params={
                    "key": ROBOT,
                    "property": "gripperWidth",
                    "op": op,
                    "value": str(threshold),
                    "timeoutSeconds": 30,
                },
            )
        )
        action_task = None
        try:

            async def registered():
                return (await stats())["benchNewtonRegisteredWaiters"] > 0

            await eventually(registered)
            registered_at = time.monotonic() * 1000
            action_task = asyncio.create_task(action())
            response = await waiter
            received = time.monotonic() * 1000
            response.raise_for_status()
            assert response.json()["satisfied"] is True and not response.json()["timedOut"]
            assert predicate(response.json()["value"])
            await action_task

            async def crossing():
                for stamp, _, message in readers[0].observations:
                    if stamp < registered_at:
                        continue
                    for change in message["changes"]:
                        if change["key"] == ROBOT and "gripperWidth" in change["values"]:
                            if predicate(change["values"]["gripperWidth"]):
                                return stamp
                return None

            stamp = await eventually(crossing)
            assert received >= stamp
            waits.append(received - stamp)
        finally:
            if not waiter.done():
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
            if action_task is not None:
                if not action_task.done():
                    action_task.cancel()
                await asyncio.gather(action_task, return_exceptions=True)

    await asyncio.sleep(0.2)
    final = (await client.get(route + "/state")).json()["entities"]

    async def drained():
        assert all(reader.error is None for reader in readers), [reader.error for reader in readers]
        return all(
            all(
                reader.invocations.get(item["invocationId"], {}).get("status") == "succeeded"
                for item in accepted
            )
            and all(
                reader.entities[key]["rev"] == value["rev"]
                and reader.entities[key]["values"] == value["values"]
                for key, value in final.items()
            )
            for reader in readers
        )

    await eventually(drained)
    corpus = (await client.get(route + "/events", params={"after": cursor, "timeoutSeconds": 0})).json()
    assert not corpus["gap"] and corpus["events"], (
        "Native correctness cohort needs the complete public corpus"
    )
    expected = [packed(entry["message"]) for entry in corpus["events"]]

    async def cohort_received():
        return all(expected[-1] in reader.messages for reader in readers)

    await eventually(cohort_received)
    prefixes, reader_digests = [], []
    for reader in readers:
        prefix, sha = match_cohort(reader.messages, expected)
        prefixes.append(prefix)
        reader_digests.append(sha)
    orders = {}
    for message in expected:
        value = orjson.loads(message)
        if value["type"] == "invocation.update":
            orders.setdefault(value["invocationId"], []).append(value["status"])
    for invocation in accepted:
        order = orders[invocation["invocationId"]]
        assert order[0] == "accepted" and "running" in order and order[-1] == "succeeded"
    observations = [
        distribution(
            [
                latency
                for stamp, latency, _ in reader.observations
                if any(begin <= stamp <= end for begin, end in windows)
            ]
        )
        for reader in readers
    ]
    assert all(item["samples"] >= len(accept_times) for item in observations)
    gripper_changes = [
        [
            change["values"]["gripperWidth"]
            for stamp, _, message in reader.observations
            if any(begin <= stamp <= end for begin, end in windows)
            for change in message["changes"]
            if change["key"] == ROBOT and "gripperWidth" in change["values"]
        ]
        for reader in readers
    ]
    assert all(
        len(values) >= len(accept_times) and max(values) - min(values) >= 0.07 for values in gripper_changes
    )
    assert final[ROBOT]["meta"]["gripperWidth"]["source"] == "sim"
    return {
        "repeats": phases,
        "accept": distribution(accept_times),
        "observation": observations,
        "waitWake": distribution(waits),
        "sampledCurrentTick": distribution(tick_samples),
        "reportedFinalTickP95Ms": (await stats())["tickP95Ms"],
        "correctness": {
            "executedSuccessfulActions": len(accepted),
            "measured202Responses": len(accept_times),
            "actor": actor,
            "publicReliableMessages": len(expected),
            "publicDigest": digest(expected),
            "readerDigests": reader_digests,
            "outsideCohortPrefixes": prefixes,
            "twoClientReliableLoss": 0,
            "finalValuesAndRevisionsMatch": True,
            "invocationOrderValid": True,
            "measuredGripperChanges": [len(values) for values in gripper_changes],
            "measuredGripperRangeM": [[min(values), max(values)] for values in gripper_changes],
        },
    }


def serve(port):
    import uvicorn

    from apps.api.app.main import create_app
    from apps.api.app.session_manager import Session
    from packages.info.store import StateStore

    waiting = set()
    original_wait, original_stats = StateStore.wait, Session.stats
    environment = {"python": platform.python_version(), "versions": {}}
    for name in ("pydantic", "httpx", "websockets", "uvicorn", "orjson", "newton", "warp-lang"):
        try:
            environment["versions"][name] = version(name)
        except PackageNotFoundError:
            environment["versions"][name] = None

    async def observed_wait(self, key, prop, predicate, timeout):
        token = id(self), key, prop
        waiting.add(token)
        try:
            return await original_wait(self, key, prop, predicate, timeout)
        finally:
            waiting.discard(token)

    def observed_stats(self):
        return original_stats(self) | {
            "benchNewtonRegisteredWaiters": len(waiting),
            "benchNewtonEnvironment": environment,
        }

    StateStore.wait, Session.stats = observed_wait, observed_stats
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning", ws_per_message_deflate=False)


async def probe_newton_info(
    *,
    python=None,
    port=8011,
    repeats=3,
    actions_per_repeat=16,
    warmup_actions=4,
    wait_samples=6,
    output_dir=None,
):
    """Own one real Native-only phase; return measurements without relaxing budgets."""
    result = {
        "status": "unverified",
        "sourceRevision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "toolSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "configuration": {
            "port": port,
            "repeats": repeats,
            "actionsPerRepeat": actions_per_repeat,
            "warmupActions": warmup_actions,
            "waitSamples": wait_samples,
            "device": "cpu",
        },
        "method": {
            "accept": "real POST/actions request to received202; terminal separately awaited",
            "observation": "same-machine monotonic host sampleTimeMs to WS recv completion before parsing",
            "wait": "real registered StateStore waiter; host threshold publication to HTTP receipt",
            "tick": "source window tickP95Ms (NumPy linear) and window count; "
            "point tick_ms sampled after actions",
            "tickScope": "source read/program/diff/commit control window; excludes physics solver time",
            "p95": "nearest-rank for client samples; source window percentile is reported separately",
            "correctness": "complete post-cursor public/events corpus aligned against both real WS readers",
            "isolation": "own defaultSource gateway/CPU session/recording root; "
            "Native phase only, no skipped ticks",
            "compression": "disabled on the real gateway and both WebSocket clients",
        },
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "versions": {}},
    }
    for name in ("pydantic", "httpx", "websockets", "uvicorn", "orjson", "newton", "warp-lang"):
        try:
            result["environment"]["versions"][name] = version(name)
        except PackageNotFoundError:
            result["environment"]["versions"][name] = None
    if not (ROOT / "packages/programs/robot_arm/newton.py").is_file():
        return result | {"status": "unavailable", "reason": "i3_not_integrated"}
    if not all(
        type(value) is int and value > 0 for value in (port, repeats, actions_per_repeat, wait_samples)
    ):
        raise ValueError("Native workload/port counts must be positive integers")
    if type(warmup_actions) is not int or warmup_actions < 0:
        raise ValueError("warmup_actions must be a nonnegative integer")
    process, route, readers, tasks = None, None, [], []
    execution = {"gatewayPid": None, "gatewayExited": False}
    result["execution"] = execution
    with tempfile.TemporaryDirectory(prefix="spatial-native-info-bench-") as temporary:
        directory = Path(output_dir).resolve() / "newton-info" if output_dir else Path(temporary)
        if output_dir:
            directory.mkdir(parents=True, exist_ok=False)
        with socket.socket() as reservation:
            if os.name != "nt":
                # Match Uvicorn's reuse policy for the preceding Synthetic server's TIME_WAIT.
                reservation.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            reservation.bind(("127.0.0.1", port))
        log_path = directory / "gateway.log"
        with log_path.open("w", encoding="utf-8") as log:
            try:
                process = subprocess.Popen(
                    [
                        str(python or sys.executable),
                        str(Path(__file__).resolve()),
                        "--serve",
                        "--port",
                        str(port),
                    ],
                    cwd=ROOT,
                    stdout=log,
                    stderr=log,
                    env=os.environ
                    | {
                        "SPATIAL_RECORDINGS_ROOT": str(directory / "recordings"),
                        "SPATIAL_REMOTE": "0",
                        "SPATIAL_RECORDINGS_ENABLED": "1",
                    },
                    start_new_session=os.name != "nt",
                )
                execution["gatewayPid"] = process.pid
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", timeout=45, trust_env=False
                ) as client:

                    async def healthy():
                        assert process.poll() is None, "Owned Native gateway exited during startup"
                        try:
                            return (await client.get("/api/v1/health", timeout=2)).status_code == 200
                        except httpx.TransportError:
                            return False

                    await eventually(healthy, timeout=30)
                    created = await client.post(
                        "/api/v1/sessions",
                        json={
                            "sceneId": "robot-and-tubes",
                            "source": "newton",
                            "realtimeFactor": 1,
                            "continueWithoutObservers": True,
                        },
                        timeout=120,
                    )
                    created.raise_for_status()
                    route = "/api/v1/sessions/" + created.json()["sessionId"]
                    manifest = (await client.get(route + "/scene")).json()
                    catalog = (await client.get(route + "/catalog")).json()
                    assert (
                        len(catalog["entities"]) == 22
                        and catalog["entities"][ROBOT]["program"] == "robot_arm.newton"
                    )
                    result["scene"] = {
                        "entities": len(catalog["entities"]),
                        "bodies": sum(node["kind"] == "body" for node in manifest["nodes"]),
                    }
                    for _ in range(2):
                        reader = Reader(f"ws://127.0.0.1:{port}{route}/ws", manifest)
                        readers.append(reader)
                        tasks.append(asyncio.create_task(reader.run()))
                    await asyncio.wait_for(asyncio.gather(*(reader.ready.wait() for reader in readers)), 15)
                    assert all(reader.error is None for reader in readers), [
                        reader.error for reader in readers
                    ]
                    result["measurements"] = await measure(
                        client,
                        route,
                        readers,
                        repeats=repeats,
                        actions_per_repeat=actions_per_repeat,
                        warmup_actions=warmup_actions,
                        wait_samples=wait_samples,
                    )
                    result["gatewayEnvironment"] = (await client.get(route + "/stats")).json()[
                        "benchNewtonEnvironment"
                    ]
                    (await client.delete(route)).raise_for_status()
                    route = None
                    measured = result["measurements"]
                    result["budgets"] = {
                        "accept25ms": measured["accept"]["p95Ms"] <= 25,
                        "observation20ms": all(item["p95Ms"] <= 20 for item in measured["observation"]),
                        "sourceTick2ms": measured["reportedFinalTickP95Ms"] <= 2
                        and all(item["reportedTickP95Ms"] <= 2 for item in measured["repeats"]),
                        "waitWake5ms": measured["waitWake"]["p95Ms"] <= 5,
                        "twoClientReliableLossZero": measured["correctness"]["twoClientReliableLoss"] == 0,
                    }
                    result["status"] = "measured"
            except Exception as exc:
                result.update(status="invalid", error=f"{type(exc).__name__}: {exc}")
            finally:
                if route and process and process.poll() is None:
                    with contextlib.suppress(Exception):
                        async with httpx.AsyncClient(timeout=15, trust_env=False) as cleanup:
                            await cleanup.delete(f"http://127.0.0.1:{port}{route}")
                await asyncio.gather(*(reader.close() for reader in readers), return_exceptions=True)
                await asyncio.gather(*tasks, return_exceptions=True)
                if process:
                    if process.poll() is None:
                        if os.name == "nt":
                            await asyncio.to_thread(
                                subprocess.run,
                                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True,
                                timeout=10,
                            )
                        else:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(asyncio.to_thread(process.wait), 10)
                    except TimeoutError:
                        if os.name == "nt":
                            process.kill()
                        else:
                            with contextlib.suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                        await asyncio.wait_for(asyncio.to_thread(process.wait), 5)
                    execution.update(gatewayExited=process.poll() is not None, exitCode=process.returncode)
        if result["status"] == "invalid":
            result["gatewayLogTail"] = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--actions", type=int, default=16, help="real alternating gripper actions per repeat")
    parser.add_argument("--warmup-actions", type=int, default=4)
    parser.add_argument("--wait-samples", type=int, default=6)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.serve:
        serve(args.port)
        return 0
    result = asyncio.run(
        probe_newton_info(
            port=args.port,
            repeats=args.repeats,
            actions_per_repeat=args.actions,
            warmup_actions=args.warmup_actions,
            wait_samples=args.wait_samples,
        )
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "measured" else 1


if __name__ == "__main__":
    raise SystemExit(main())

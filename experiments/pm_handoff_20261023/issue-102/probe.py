"""Issue102 scratch-only client scheduling probe. Performance requires --run-pair.

No product edits. Original Reader.run and benchmark timers/assertions are retained.
--audit and --verify use no Source, server, physics engine, or performance load.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import copy
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import types
import uuid

PIN = "8db36c375d6e6d0da674df091ea4c0fc6a88efa0"
BENCH_BLOB = "84bc1ea4fe04966d9242f19ab58889ef0cedb532"
OVERLAY_SHA = "6db9ba9b34ed5bc6b6e3812718c7e06e2fd7444540df2455d2c51bd58808feee"
HERE = Path(__file__).resolve().parent
SOURCE = Path(os.environ.get("ISSUE102_SOURCE", "/mnt/d/Projects/Robots/spatial-sync/.worktrees/issue-102"))
BENCH = None
ORJSON = None
TOKEN = "DEBUG-issue102-isolation-v1"
BASE_FIELDS = {"ready", "error", "changes", "batches", "digest", "info_messages", "info_digest", "markers", "invocations"}
WINDOW_FIELDS = {"frames", "change_samples"}
FINAL_FIELDS = {"values", "revisions", "latencies"}


def check_source_freeze():
    receipt = json.loads((HERE / "source-freeze.json").read_text())
    assert receipt["head"] == PIN and receipt["benchmarkBlob"] == BENCH_BLOB
    for relative, expected in receipt["filesSha256"].items():
        assert sha(SOURCE / relative) == expected, "frozen source file changed: " + relative
    return receipt


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_bench():
    global BENCH, ORJSON
    if BENCH is None:
        sys.path[:0] = [str(SOURCE), str(SOURCE / "apps/cli")]
        BENCH = importlib.import_module("tools.bench_info")
        ORJSON = importlib.import_module("orjson")
        if Path(BENCH.__file__).resolve() != (SOURCE / "tools/bench_info.py").resolve():
            raise RuntimeError("benchmark module origin mismatch")
        data = (SOURCE / "tools/bench_info.py").read_bytes().replace(b"\r\n", b"\n")
        blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if blob != BENCH_BLOB:
            raise RuntimeError("frozen benchmark Git blob mismatch")
    return BENCH


def identity():
    from importlib.metadata import version
    b = load_bench()
    origins = {}
    for name in ("tools.bench_info", "packages.info.program", "packages.protocol.schema", "packages.transport", "httpx", "websockets", "orjson", "pydantic"):
        module = importlib.import_module(name)
        origins[name] = str(Path(module.__file__).resolve())
    return {
        "pid": os.getpid(), "ppid": os.getppid(), "python": sys.executable,
        "pythonVersion": sys.version, "source": str(SOURCE), "pin": PIN,
        "benchmarkBlob": BENCH_BLOB, "probeSha256": sha(__file__),
        "benchmarkRawSha256": sha(b.__file__), "origins": origins,
        "versions": {name: version(name) for name in ("pydantic", "httpx", "websockets", "uvicorn", "orjson")},
        "clock": vars(time.get_clock_info("monotonic")),
        "clockMs": time.monotonic() * 1000,
        "bootId": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "engineImported": any(name in sys.modules for name in ("newton", "warp", "mujoco", "pxr")),
    }


def capture(reader, scope):
    """Serialize actual original Reader state; no reimplementation of parsing."""
    if scope not in ("status", "window", "full"):
        raise ValueError("unknown aggregate scope")
    value = {name: copy.deepcopy(getattr(reader, name)) for name in BASE_FIELDS - {"ready", "digest", "info_digest"}}
    value.update(ready=reader.ready.is_set(), digest=reader.digest.hexdigest(), info_digest=reader.info_digest.hexdigest())
    for name in (WINDOW_FIELDS if scope in ("window", "full") else set()) | (FINAL_FIELDS if scope == "full" else set()):
        value[name] = copy.deepcopy(getattr(reader, name))
    # Same serialization and parse work in both topologies. JSON keys need explicit
    # restoration: entity indexes and integer marker tokens are not wire strings.
    return ORJSON.dumps(value, option=ORJSON.OPT_NON_STR_KEYS)


class DigestView:
    def __init__(self, value):
        self.value = value

    def hexdigest(self):
        return self.value


class ReaderView:
    def update(self, payload, scope):
        value = ORJSON.loads(payload)
        expected = BASE_FIELDS | (WINDOW_FIELDS if scope in ("window", "full") else set()) | (FINAL_FIELDS if scope == "full" else set())
        if set(value) != expected:
            raise ValueError("missing or extra aggregate field")
        for name in ("values", "revisions", "markers"):
            if name in value:
                value[name] = {int(key): item for key, item in value[name].items()}
        for name in ("digest", "info_digest"):
            if len(value[name]) != 64:
                raise ValueError("invalid SHA256 aggregate")
            value[name] = DigestView(value[name])
        self.__dict__.update(value)
        if self.error is not None:
            raise AssertionError(f"original Reader failed: {self.error}")
        return self


class SharedReader:
    def __init__(self, reader):
        self.reader = reader
        self.task = asyncio.create_task(reader.run())

    async def snapshot(self, scope):
        return capture(self.reader, scope)

    async def close(self):
        await self.reader.close()
        await asyncio.wait_for(self.task, 5)


class ProcessReader:
    def __init__(self, process, log_handle, owner):
        self.process, self.log_handle, self.owner = process, log_handle, owner

    @classmethod
    async def start(cls, config, root, index):
        config_path = root / f"reader-{index}.json"
        dump(config_path, config)
        log_handle = (root / f"reader-{index}.stderr.txt").open("wb")
        process = await asyncio.create_subprocess_exec(sys.executable, str(Path(__file__).resolve()), "--worker", str(config_path), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=log_handle, limit=8 * 1024 * 1024)
        import psutil
        owned = psutil.Process(process.pid)
        result = cls(process, log_handle, {"pid": process.pid, "created": owned.create_time(), "command": owned.cmdline()})
        try:
            before = time.monotonic() * 1000
            response = await result.rpc({"op": "identity"})
            after = time.monotonic() * 1000
            parent = identity()
            assert response["bootId"] == parent["bootId"] and response["clock"] == parent["clock"]
            assert before <= response["clockMs"] <= after, "cross-process monotonic clock is inconsistent"
            for key in ("pin", "source", "probeSha256", "benchmarkBlob", "versions", "origins", "python"):
                assert response[key] == parent[key], (key, "worker identity mismatch")
            result.owner["identity"] = response
            dump(root / f"reader-{index}.owner.json", result.owner)
            return result
        except BaseException:
            await result.close()
            raise

    async def rpc(self, value):
        if self.process.returncode is not None:
            raise RuntimeError("owned reader worker exited")
        self.process.stdin.write(ORJSON.dumps(value) + b"\n")
        await self.process.stdin.drain()
        line = await asyncio.wait_for(self.process.stdout.readline(), 10)
        if not line:
            raise RuntimeError("owned reader worker IPC closed")
        envelope = ORJSON.loads(line)
        if not envelope["ok"]:
            raise RuntimeError(envelope["error"])
        return envelope["result"]

    async def snapshot(self, scope):
        # Envelope contains the exact serialized snapshot as UTF-8 text.
        return (await self.rpc({"op": "snapshot", "scope": scope})).encode()

    async def close(self):
        try:
            if self.process.returncode is None:
                with contextlib.suppress(Exception):
                    await self.rpc({"op": "close"})
                try:
                    await asyncio.wait_for(self.process.wait(), 5)
                except TimeoutError:
                    self.process.terminate()  # current owned Process handle only
                    try:
                        await asyncio.wait_for(self.process.wait(), 3)
                    except TimeoutError:
                        self.process.kill()
                        await self.process.wait()
            if self.process.returncode != 0:
                raise RuntimeError(f"owned reader abnormal exit: {self.process.returncode}")
        finally:
            self.log_handle.close()


class ReaderGroup(list):
    def __init__(self, backends):
        super().__init__([ReaderView() for _ in backends])
        self.backends = backends
        self.calls = []

    async def refresh(self, scope):
        started = time.monotonic() * 1000
        payloads = await asyncio.gather(*(backend.snapshot(scope) for backend in self.backends))
        for view, payload in zip(self, payloads, strict=True):
            view.update(payload, scope)
        self.calls.append({"scope": scope, "startedMs": started, "endedMs": time.monotonic() * 1000, "bytes": [len(item) for item in payloads]})

    async def ready(self):
        async def done():
            await self.refresh("status")
            return all(view.ready for view in self)
        await load_bench().eventually(done)

    async def close(self):
        results = await asyncio.gather(*(backend.close() for backend in self.backends), return_exceptions=True)
        errors = [str(result) for result in results if isinstance(result, BaseException)]
        if errors:
            raise RuntimeError("reader cleanup failed: " + repr(errors))


INSERTIONS = [
    ('    assert all(reader.info_messages == 0 for reader in readers)', '    await readers.refresh("status")\n'),
    ('                return all(token in reader.markers for reader in readers)', '                await readers.refresh("status")\n'),
    ('        frames = [sum(begin_ms <= stamp <= end_ms', '        await readers.refresh("window")\n'),
    ('        for reader in readers:\n            assert reader.error is None', '        await readers.refresh("status")\n'),
    ('    assert final["bench"]["changes"] > 0', '    await readers.refresh("full")\n'),
]


def adapted_benchmark(root):
    b = load_bench()
    original = inspect.getsource(b.benchmark)
    changed = original
    for marker, insertion in INSERTIONS:
        assert changed.count(marker) == 1, "benchmark insertion boundary drift"
        changed = changed.replace(marker, insertion + marker)
    # Prove AST preservation: the only new nodes are these five await-refreshes.
    parsed = ast.parse(changed)
    class RemoveRefresh(ast.NodeTransformer):
        def visit_Expr(self, node):
            value = node.value
            if isinstance(value, ast.Await) and isinstance(value.value, ast.Call) and isinstance(value.value.func, ast.Attribute) and isinstance(value.value.func.value, ast.Name) and value.value.func.value.id == "readers" and value.value.func.attr == "refresh":
                return None
            return self.generic_visit(node)
    restored = RemoveRefresh().visit(copy.deepcopy(parsed))
    assert ast.dump(restored, include_attributes=False) == ast.dump(ast.parse(original), include_attributes=False)
    if root:
        import difflib
        (root / "benchmark-adapter.diff").write_text("".join(difflib.unified_diff(original.splitlines(True), changed.splitlines(True), fromfile="original benchmark", tofile="same benchmark plus five aggregate reads")), encoding="utf-8")
    namespace = dict(vars(b))
    exec(compile(changed, str(SOURCE / "tools/bench_info.py") + ":scratch-adapter", "exec"), namespace)
    return namespace["benchmark"]


def fixture_manifest():
    return {"sessionId": "fixture", "sceneRevision": "fixture-revision", "revisionNumericId": 7, "nodes": [{"kind": "body", "streamId": 1}]}


def fixture_payloads(mutation=None):
    b = load_bench()
    manifest = fixture_manifest()
    messages = [
        {"type": "entity.snapshot", "sceneRevision": manifest["sceneRevision"], "entities": [{"key": b.PREFIX + f"{i:08d}"} for i in range(2)]},
        {"type": "entity.observations", "sceneRevision": manifest["sceneRevision"], "epoch": 0, "sampleTimeMs": 999_000, "changes": [{"key": b.PREFIX + f"{i:08d}", "rev": 2, "values": {"value": 1}} for i in range(2)] + [{"key": b.CONTROL, "rev": 2, "values": {"marker": 17}}]},
        {"type": "invocation.update", "invocationId": "one", "status": "accepted", "action": "ping", "params": {"echo": 41}},
        {"type": "invocation.update", "invocationId": "one", "status": "running", "action": "ping", "params": {"echo": 41}},
        {"type": "invocation.update", "invocationId": "one", "status": "succeeded", "action": "ping", "params": {"echo": 41}, "result": {"echo": 41}},
    ]
    if mutation == "snapshot-missing":
        messages[0]["entities"].pop()
    elif mutation == "epoch":
        messages[1]["epoch"] = 1
    elif mutation == "scene":
        messages[1]["sceneRevision"] = "wrong"
    elif mutation == "duplicate-revision":
        messages.insert(2, copy.deepcopy(messages[1]))
    elif mutation == "value-regression":
        extra = copy.deepcopy(messages[1]); extra["changes"][0]["rev"] = 3
        messages.insert(2, extra)
    elif mutation == "out-of-range":
        messages[1]["changes"][0]["key"] = b.PREFIX + "00000002"
    elif mutation == "negative-value":
        messages[1]["changes"][0]["values"]["value"] = -1
    elif mutation == "noninteger-value":
        messages[1]["changes"][0]["values"]["value"] = 1.5
    elif mutation == "extra-metadata":
        messages[1]["changes"][0]["meta"] = {"value": {"source": "virtual", "quality": "known", "sampleTimeMs": 999000}}
    elif mutation == "running-first":
        messages.pop(2)
    elif mutation == "terminal-then-update":
        messages.append(copy.deepcopy(messages[3]))
    elif mutation == "server-error":
        messages.append({"type": "server.error", "code": "test"})
    frame = types.SimpleNamespace(type="transform.frame", epoch=0, seq=1, simTime=0, sampleTimeMs=999_000, poses=[(1, 0, 0, 0, 0, 0, 0, 1)])
    codec = b.FrameCodec(manifest["sessionId"], manifest["sceneRevision"], manifest["revisionNumericId"])
    if mutation == "body-set":
        frame.poses[0] = (2, 0, 0, 0, 0, 0, 0, 1)
    payloads = [ORJSON.dumps(message).decode() for message in messages]
    payloads.append(codec.encode(frame))
    if mutation == "frame-sequence":
        payloads.append(codec.encode(frame))
    if mutation == "binary-corrupt":
        payloads.append(b"NSYN")
    return payloads


class FixtureSocket:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def recv(self): return '{"type":"server.ready","protocolVersion":2}'
    async def send(self, value): self.subscription = ORJSON.loads(value)
    async def close(self): return None
    def __aiter__(self): return self
    async def __anext__(self):
        try: return next(self.payloads)
        except StopIteration: raise StopAsyncIteration from None


async def fixture_reader(mutation=None):
    b = load_bench()
    ws = FixtureSocket(); ws.payloads = iter(fixture_payloads(mutation))
    saved_connect, saved_time = b.connect, b.time
    b.connect = lambda *args, **kwargs: ws
    b.time = types.SimpleNamespace(monotonic=lambda: 1000.0)
    try:
        reader = b.Reader("fixture://no-source", fixture_manifest(), 2)
        await reader.run()
        return reader
    finally:
        b.connect, b.time = saved_connect, saved_time


async def worker(config_path):
    config = json.loads(Path(config_path).read_text())
    load_bench()
    if config.get("fixture"):
        reader = await fixture_reader(config.get("mutation"))
        task = None
    else:
        reader = BENCH.Reader(config["url"], config["manifest"], config["entities"])
        task = asyncio.create_task(reader.run())
    stdin = asyncio.StreamReader(limit=8 * 1024 * 1024)
    await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin.buffer)
    try:
        while line := await stdin.readline():
            request = ORJSON.loads(line)
            try:
                op = request["op"]
                if op == "identity": result = identity()
                elif op == "snapshot": result = capture(reader, request["scope"]).decode()
                elif op == "close":
                    await reader.close()
                    if task: await asyncio.wait_for(task, 5)
                    result = True
                else: raise ValueError("unknown IPC operation")
                envelope = {"ok": True, "result": result}
            except Exception as exc:
                envelope = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            sys.stdout.buffer.write(ORJSON.dumps(envelope) + b"\n"); sys.stdout.buffer.flush()
            if request.get("op") == "close": break
    finally:
        await reader.close()
        if task:
            await asyncio.gather(task, return_exceptions=True)


def owned_environment(root):
    for name in ("state", "recordings", "library", "previews", "tmp"):
        (root / name).mkdir(parents=True, exist_ok=True)
    # Inherited SPATIAL settings must not activate real live bindings or select
    # user data. Do not inspect their values; replace this task's whole namespace.
    clean = {key: value for key, value in os.environ.items() if not key.startswith("SPATIAL_") and key != "LW_LIBRARY"}
    return clean | {
        "SPATIAL_STATE_ROOT": str(root / "state"), "SPATIAL_RECORDINGS_ROOT": str(root / "recordings"),
        "LW_LIBRARY": str(root / "library"), "SPATIAL_SCENE_PREVIEWS_ROOT": str(root / "previews"),
        "SPATIAL_REMOTE": "0", "SPATIAL_RECORDINGS_ENABLED": "1", "TMPDIR": str(root / "tmp"),
        "PYTHONDONTWRITEBYTECODE": "1", "ISSUE102_SOURCE": str(SOURCE),
    }


def normalized(document):
    value = copy.deepcopy(document)
    for key in ("sessionId", "sceneRevision", "revisionNumericId"):
        value.pop(key, None)
    return value


def check_prepared_environment(prepared):
    """Fresh read-only check immediately before any authorized Source launch."""
    from importlib.metadata import version
    assert sha(prepared["overlay"]) == OVERLAY_SHA
    actual = identity()
    for key in ("pin", "source", "probeSha256", "benchmarkBlob", "versions", "origins", "python", "pythonVersion"):
        assert actual[key] == prepared["identity"][key], "prepared environment drift: " + key
    checked = []
    for item in prepared["requiredPackages"]:
        current = version(item["name"])
        assert current == item["expected"], "installed pin drift: " + item["name"]
        checked.append({"name": item["name"], "actual": current})
    assert len(checked) == 79
    paho = str(Path(importlib.import_module("paho.mqtt.client").__file__).resolve())
    assert paho.startswith(prepared["overlay"] + "/"), "task-owned paho overlay origin drift"
    return {"identity": actual, "pins": checked, "pahoOrigin": paho}


async def run_mode(mode, root, port):
    b = load_bench()
    args = types.SimpleNamespace(entities=1000, rate_hz=15, generation_hz=15, http_hz=10, seconds=12, repeats=3, warmup_seconds=1, wake_samples=3)
    fixture_root = b.fixture(root, args.entities, args.rate_hz, args.generation_hz)
    env = owned_environment(root)
    # Deliberately launch the unchanged original --serve; only PID identity is added externally.
    gateway_log = (root / "gateway.log").open("wb")
    gateway_config = root / "gateway-config.json"
    dump(gateway_config, {"fixture_dir": str(fixture_root), "entities": 1000, "port": port, "evidence": str(root)})
    gateway = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--gateway", str(gateway_config)], cwd=SOURCE, env=env, stdout=gateway_log, stderr=gateway_log)
    import psutil
    process = psutil.Process(gateway.pid)
    owner = {"pid": gateway.pid, "created": process.create_time(), "command": process.cmdline()}
    dump(root / "gateway.owner.json", owner)
    group = None; route = None; result = {"status": "invalid", "mode": mode, "identity": identity(), "configuration": vars(args), "gatewayOwner": owner}
    try:
        async with b.httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False, limits=b.httpx.Limits(max_connections=20)) as client:
            async def healthy():
                if gateway.poll() is not None: raise RuntimeError("owned gateway exited at startup")
                try: return (await client.get("/api/v1/health")).status_code == 200
                except b.httpx.ConnectError: return False
            await b.eventually(healthy, timeout=30)
            actual_gateway = json.loads((root / "gateway-identity.json").read_text())
            for key in ("pin", "source", "probeSha256", "benchmarkBlob", "versions", "python"):
                assert actual_gateway[key] == result["identity"][key], "gateway identity mismatch: " + key
            result["gatewayIdentity"] = actual_gateway
            response = await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}); response.raise_for_status()
            route = "/api/v1/sessions/" + response.json()["sessionId"]
            manifest = (await client.get(route + "/scene")).json()
            catalog = (await client.get(route + "/catalog")).json()
            assert len(catalog["entities"]) == 1023 and sum(node["kind"] == "body" for node in manifest["nodes"]) == 26
            prepared = Path(os.environ["ISSUE102_PREPARED_AUDIT"])
            for name, actual in (("manifest", manifest), ("catalog", catalog)):
                expected = json.loads((prepared / f"fixture-{name}.json").read_text())
                assert normalized(actual) == normalized(expected), "actual producer fixture differs from prepared " + name
            dump(root / "actual-manifest.json", manifest); dump(root / "actual-catalog.json", catalog)
            config = {"url": f"ws://127.0.0.1:{port}{route}/ws", "manifest": manifest, "entities": 1000}
            backends = []
            try:
                for index in range(2):
                    backend = SharedReader(b.Reader(config["url"], manifest, 1000)) if mode == "shared" else await ProcessReader.start(config, root, index)
                    backends.append(backend)
                group = ReaderGroup(backends)
                await group.ready()
                result["measurements"] = await adapted_benchmark(root)(args, client, route, group)
                result["aggregateCalls"] = group.calls
                result["status"] = "measured-diagnostic"
                (await client.delete(route)).raise_for_status(); route = None
            finally:
                if group is None:
                    await ReaderGroup(backends).close()
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        cleanup_errors = []
        if route and gateway.poll() is None:
            try:
                async with b.httpx.AsyncClient(timeout=5, trust_env=False) as cleanup:
                    (await cleanup.delete(f"http://127.0.0.1:{port}{route}")).raise_for_status()
            except Exception as exc: cleanup_errors.append(f"session: {exc}")
        if group:
            try: await group.close()
            except Exception as exc: cleanup_errors.append(str(exc))
        if gateway.poll() is None:
            gateway.terminate()
            try: await asyncio.wait_for(asyncio.to_thread(gateway.wait), 5)
            except TimeoutError:
                gateway.kill(); await asyncio.to_thread(gateway.wait)
        gateway_log.close()
        result["cleanup"] = {"gatewayExited": gateway.poll() is not None, "exitCode": gateway.returncode, "errors": cleanup_errors}
        with socket.socket() as check:
            check.settimeout(.25)
            listener_gone = check.connect_ex(("127.0.0.1", port)) != 0
        result["cleanup"]["listenerGone"] = listener_gone
        if not listener_gone: cleanup_errors.append("owned gateway listener remains")
        dump(root / "receipt.json", result)
        if cleanup_errors: raise RuntimeError("owned cleanup failed")
    return result


def gateway(config_path):
    """Identity-only wrapper around unchanged serve; both modes use this wrapper."""
    b = load_bench()
    import uvicorn
    config = json.loads(Path(config_path).read_text())
    original_run = uvicorn.run
    def recorded_run(*args, **kwargs):
        actual = identity()
        actual["gatewayModuleOrigins"] = {name: str(Path(importlib.import_module(name).__file__).resolve()) for name in ("apps.api.app.main", "apps.api.app.sources.synthetic_source", "packages.info.store", "packages.models", "packages.vault")}
        assert all(Path(path).is_relative_to(SOURCE) for path in actual["gatewayModuleOrigins"].values())
        actual["ownedRoots"] = {key: os.environ[key] for key in ("SPATIAL_STATE_ROOT", "SPATIAL_RECORDINGS_ROOT", "LW_LIBRARY", "SPATIAL_SCENE_PREVIEWS_ROOT", "TMPDIR")}
        dump(Path(config["evidence"]) / "gateway-identity.json", actual)
        return original_run(*args, **kwargs)
    uvicorn.run = recorded_run
    try:
        b.serve(types.SimpleNamespace(fixture_dir=Path(config["fixture_dir"]), entities=config["entities"], port=config["port"]))
    finally:
        uvicorn.run = original_run


def budgets(measurements):
    checks = []
    for index, repeat in enumerate(measurements["repeats"]):
        for phase in ("baseline", "loaded"):
            for operation, distribution in repeat[phase]["http"].items():
                limit = 5 if operation == "accept" else 2
                checks.append({"repeat": index + 1, "phase": phase, "metric": operation, "p95Ms": distribution["p95Ms"], "budgetMs": limit, "passed": distribution["p95Ms"] <= limit})
        assert repeat["loaded"]["sustainedThroughputBudget"]["satisfied"], "actual delivered workload below10000/s"
        assert all(value <= .10 for value in repeat["transformCadenceDegradation"]), "cadence decrease exceeds10%"
    for name, values in (("observation", measurements["observationLatency"]), ("wait", [measurements["waitWakeLatency"]])):
        for distribution in values:
            checks.append({"metric": name, "p95Ms": distribution["p95Ms"], "budgetMs": 5, "passed": distribution["p95Ms"] <= 5})
    return checks


async def pair(root, port):
    results = []
    for index, mode in enumerate(("shared", "isolated")):
        mode_root = root / mode; mode_root.mkdir()
        with socket.socket() as check:
            check.bind(("127.0.0.1", port + index))
        result = await run_mode(mode, mode_root, port + index)
        result["budgetChecks"] = budgets(result["measurements"])
        dump(mode_root / "receipt.json", result)
        results.append(result)
    dump(root / "pair.json", {"status": "measured-diagnostic", "causalClaim": None, "results": results})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path)
    parser.add_argument("--gateway", type=Path)
    parser.add_argument("--run-pair", action="store_true")
    parser.add_argument("--authorized-window", help="PM-supplied exclusive-window receipt path")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--prepared-audit", type=Path)
    parser.add_argument("--port", type=int, default=8021)
    args = parser.parse_args()
    if args.worker:
        asyncio.run(worker(args.worker)); return
    if args.gateway:
        gateway(args.gateway); return
    if not args.run_pair:
        parser.error("performance disabled; use separate audit/verify tools, or PM-authorized --run-pair")
    if not args.authorized_window:
        parser.error("exclusive PM GO receipt required")
    authorization = json.loads(Path(args.authorized_window).read_text())
    assert authorization == {"source": PIN, "probeSha256": sha(__file__), "exclusive": True, "maxRuns": 2, "budgetSeconds": 480}
    assert args.prepared_audit is not None
    prepared = json.loads((args.prepared_audit / "environment-audit.json").read_text())
    assert prepared["identity"]["pin"] == PIN and prepared["identity"]["probeSha256"] == sha(__file__)
    assert prepared["all79PinnedPackagesMatch"] and prepared["sourceStarts"] == 0
    fresh_environment = check_prepared_environment(prepared)
    root = args.output_root.resolve()
    assert not str(root).startswith("/mnt/") and not root.exists(), "fresh owned ext4 root required"
    root.mkdir(mode=0o700, parents=True)
    os.environ.update(owned_environment(root))
    os.environ["ISSUE102_PREPARED_AUDIT"] = str(args.prepared_audit.resolve())
    frozen = check_source_freeze()
    dump(root / "owner.json", {"token": TOKEN, "uuid": str(uuid.uuid4()), "identity": identity(), "sourceFreeze": frozen, "freshEnvironment": fresh_environment})
    def interrupt(signum, frame): raise KeyboardInterrupt(f"owned probe signal {signum}")
    signal.signal(signal.SIGTERM, interrupt)
    try:
        # Reserve60s of the480s envelope for cancellation/owned cleanup.
        asyncio.run(asyncio.wait_for(pair(root, args.port), 420))
        check_source_freeze()
    except BaseException as exc:
        dump(root / "pair-failure.json", {"type": type(exc).__name__, "message": str(exc), "retry": False})
        raise


if __name__ == "__main__":
    main()

"""Scratch common HTTP boundaries. Requires a NEW one-run GO; old pair GO fails.

Importing defines helpers only. No Source, gateway, connection or benchmark is
started by import. Trace spans never replace/subtract original benchmark times.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import contextvars
import copy
import difflib
import inspect
import json
import os
from pathlib import Path
import sys
import time
import types

ENTRY_DIR = Path(__file__).resolve().parent
TOOLS_DIR = ENTRY_DIR.parent
sys.path.insert(0, str(ENTRY_DIR))
sys.path.insert(1, str(TOOLS_DIR))

import hashlib
import importlib
from importlib.metadata import distributions, version

import httpx
import probe as base
from repair_support_v2 import DrainSnapshot, dump_server_trace, guard_export, note_invalid, preserve_benchmark_failure, require_server_trace

assert Path(base.__file__).resolve() == TOOLS_DIR / "probe.py"
assert Path(sys.modules["repair_support_v2"].__file__).resolve() == ENTRY_DIR / "repair_support_v2.py"

BASE_SHA = "25edcd0e5a48b9fdbfab4634c2c08fa8f5ed2931f82fe83e528444eab8e7d355"
HEADER = "x-issue102-trace-id"
LIMIT = 50_000
PERIOD = .005


class TraceInvalid(RuntimeError):
    pass


class Recorder:
    """Fixed event-count/string bounds, one owning event loop, no payload data."""
    def __init__(self, prefix, *, limit=LIMIT, wall=time.monotonic_ns, cpu=time.thread_time_ns):
        if not prefix.isascii() or not prefix.isalnum() or len(prefix) > 32:
            raise ValueError("owned ASCII correlation prefix required")
        self.prefix, self.limit, self.wall, self.cpu = prefix, limit, wall, cpu
        self.events = []
        self.dropped = 0
        self.problems = []
        self.request_seq = self.operation_seq = 0
        self.server_seen = set()
        self.operation = contextvars.ContextVar("issue102_operation", default=0)

    def add(self, event, rid=0, *, at_ns=None, **details):
        if len(self.events) >= self.limit:
            self.dropped += 1
            return
        # Fixed field count and bounded scalar strings. Never retain headers,
        # request/response objects, exceptions, bodies, credentials or Trace info.
        safe = {}
        for key, value in list(details.items())[:8]:
            safe[str(key)[:32]] = value if isinstance(value, (int, float, bool, type(None))) else str(value)[:256]
        self.events.append((self.wall() if at_ns is None else at_ns, self.cpu(), str(event)[:96], rid, self.operation.get(), safe))

    def invalid(self, message):
        if len(self.problems) < 8:
            self.problems.append(str(message)[:160])

    def ensure_valid(self):
        if self.dropped or self.problems:
            raise TraceInvalid("trace incomplete: bounded overflow or correlation error")

    def begin_operation(self, name, original_tick, measured, loaded):
        self.operation_seq += 1
        self.operation.set(self.operation_seq)
        self.add("benchmark.operation.start", originalTickSeconds=original_tick, name=name, measured=measured, loaded=loaded)

    def finish_operation(self, original_tick, original_end, original_duration_ms):
        # These are the original clock reads, with the existing end call's return
        # bound by a walrus inside its unchanged subtraction/multiplication.
        # Never reconstruct an end timestamp by adding a rounded duration.
        self.add("benchmark.operation.end", originalTickSeconds=original_tick, originalEndSeconds=original_end, originalDurationMs=original_duration_ms)
        self.operation.set(0)

    def prepare_request(self, request):
        self.ensure_valid()
        if "issue102_rid" in request.extensions:
            self.invalid("request object reused across sends")
            self.ensure_valid()
        self.request_seq += 1
        rid = self.request_seq
        request.extensions["issue102_rid"] = rid
        request.headers[HEADER] = f"{self.prefix}-{rid}"
        self.add("client.send.enter", rid, method=request.method, path=request.url.path)
        return rid

    def server_id(self, scope):
        values = [value for key, value in scope.get("headers", ()) if key.lower() == HEADER.encode()]
        try:
            if len(values) != 1:
                raise ValueError("missing or duplicate ID")
            prefix, number = values[0].decode("ascii").rsplit("-", 1)
            rid = int(number)
            if prefix != self.prefix or rid <= 0 or rid >= 1_000_000 or str(rid) != number:
                raise ValueError("foreign/malformed ID")
            if rid in self.server_seen or len(self.server_seen) >= self.limit:
                raise ValueError("duplicate ID or bounded correlation storage full")
            self.server_seen.add(rid)
            return rid
        except (UnicodeError, ValueError):
            self.invalid("uncorrelated HTTP request")
            return 0  # preserve the actual app response; invalidate diagnostics

    def document(self):
        return {"schema": 1, "prefix": self.prefix, "eventLimit": self.limit, "events": self.events, "dropped": self.dropped, "problems": self.problems, "clock": vars(time.get_clock_info("monotonic")), "cpuClock": vars(time.get_clock_info("thread_time")), "instrumentSha256": base.sha(__file__), "baseProbeSha256": base.sha(base.__file__), "source": base.PIN}


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, inner, recorder, rid):
        self.inner, self.recorder, self.rid = inner, recorder, rid

    async def __aiter__(self):
        size = 0
        try:
            async for chunk in self.inner:
                size += len(chunk)
                yield chunk
        except BaseException as exc:
            self.recorder.add("client.body.failure", self.rid, category=type(exc).__name__, bytes=size)
            raise
        else:
            self.recorder.add("client.body.iterator_exhausted", self.rid, bytes=size)

    async def aclose(self):
        try:
            await self.inner.aclose()
        finally:
            self.recorder.add("client.body.close", self.rid)


class Transport(httpx.AsyncBaseTransport):
    def __init__(self, inner, recorder):
        self.inner, self.recorder = inner, recorder

    async def handle_async_request(self, request):
        recorder = self.recorder
        rid = request.extensions["issue102_rid"]
        prior = request.extensions.get("trace")
        async def callback(event, info):
            # Actual httpcore hook. Do not retain info (it can hold SSL contexts,
            # headers, network streams, request objects and exception objects).
            category = type(info["exception"]).__name__ if "exception" in info else None
            recorder.add("httpcore." + event, rid, category=category)
            if prior is not None:
                await prior(event, info)
        request.extensions["trace"] = callback
        recorder.add("client.transport.enter", rid)
        try:
            response = await self.inner.handle_async_request(request)
        except BaseException as exc:
            recorder.add("client.transport.failure", rid, category=type(exc).__name__)
            raise
        recorder.add("client.transport.headers_returned", rid, status=response.status_code)
        response.stream = ResponseStream(response.stream, recorder, rid)
        return response

    async def aclose(self):
        await self.inner.aclose()


def client_factory(recorder):
    class Client(httpx.AsyncClient):
        def _init_transport(self, *args, **kwargs):
            # Pinned httpx0.28.1 source confirms all original SSL/proxy/limits
            # arguments are still handled by its original constructor.
            return Transport(super()._init_transport(*args, **kwargs), recorder)

        async def send(self, request, **kwargs):
            rid = recorder.prepare_request(request)
            try:
                response = await super().send(request, **kwargs)
            except BaseException as exc:
                recorder.add("client.send.failure", rid, category=type(exc).__name__)
                raise
            recorder.add("client.send.return", rid, streaming=kwargs.get("stream", False), status=response.status_code)
            return response
    return Client


class ASGIBoundary:
    def __init__(self, app, recorder, *, period=PERIOD, export=None):
        self.app, self.recorder, self.period = app, recorder, period
        self.monitor = None
        self.monitor_stopped = True
        self.monitor_ticks = 0
        self.export = export

    async def monitor_loop(self):
        deadline = time.monotonic() + self.period
        try:
            while True:
                await asyncio.sleep(max(0, deadline - time.monotonic()))
                now = time.monotonic()
                self.monitor_ticks += 1
                late_ns = max(0, round((now - deadline) * 1e9))
                if late_ns >= 1_000_000:
                    self.recorder.add("gateway.loop.late", deadlineNs=round(deadline * 1e9), latenessNs=late_ns)
                # No catch-up burst; monitor remains at most200 wakes/s.
                deadline = now + self.period
        finally:
            self.monitor_stopped = True

    def start_monitor(self):
        if self.monitor is not None:
            raise TraceInvalid("monitor started twice")
        self.monitor_stopped = False
        self.recorder.add("gateway.monitor.start", periodSeconds=self.period)
        self.monitor = asyncio.create_task(self.monitor_loop(), name="issue102-trace-loop")

    async def stop_monitor(self):
        was_running = not self.monitor_stopped
        if self.monitor is not None:
            self.monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor
        self.monitor_stopped = True
        if was_running:
            self.recorder.add("gateway.monitor.stop", ticks=self.monitor_ticks)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            async def lifecycle_send(message):
                nonlocal lifecycle_error
                if message["type"] in ("lifespan.startup.failed", "lifespan.shutdown.failed"):
                    lifecycle_error = message["type"]
                    note_invalid(self.recorder, lifecycle_error)
                if message["type"] == "lifespan.startup.complete":
                    self.start_monitor()
                elif message["type"] == "lifespan.shutdown.complete":
                    await self.stop_monitor()
                    if self.export is not None:
                        guard_export(self.recorder, lambda: self.export("before-forward-shutdown-complete", self.monitor_stopped, lifecycle_error))
                await send(message)
            lifecycle_error = None
            try:
                await self.app(scope, receive, lifecycle_send)
            except BaseException as error:
                lifecycle_error = type(error).__name__
                note_invalid(self.recorder, "lifespan exception: " + lifecycle_error)
                raise
            finally:
                try:
                    await self.stop_monitor()
                finally:
                    if self.export is not None:
                        guard_export(self.recorder, lambda: self.export("lifespan-finally", self.monitor_stopped, lifecycle_error))
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        recorder = self.recorder
        rid = recorder.server_id(scope)
        recorder.add("asgi.enter", rid, method=scope.get("method"), path=scope.get("path"))
        async def observed_receive():
            recorder.add("asgi.receive.enter", rid)
            try:
                message = await receive()
            except BaseException as exc:
                recorder.add("asgi.receive.failure", rid, category=type(exc).__name__)
                raise
            recorder.add("asgi.receive.return", rid, type=message["type"], bytes=len(message.get("body", b"")), more=message.get("more_body", False))
            return message
        async def observed_send(message):
            final = message["type"] == "http.response.body" and not message.get("more_body", False)
            recorder.add("asgi.send.enter", rid, type=message["type"], status=message.get("status"), bytes=len(message.get("body", b"")), more=message.get("more_body", False), final=final)
            try:
                await send(message)
            except BaseException as exc:
                recorder.add("asgi.send.failure", rid, category=type(exc).__name__, final=final)
                raise
            recorder.add("asgi.send.return", rid, type=message["type"], final=final)
        try:
            await self.app(scope, observed_receive, observed_send)
        except BaseException as exc:
            recorder.add("asgi.failure", rid, category=type(exc).__name__)
            raise
        finally:
            recorder.add("asgi.exit", rid)


BENCH_INSERTIONS = [
    ('            tick = time.monotonic()\n', '            TRACE.begin_operation(name, tick, before is not None, enabled)\n', False),
    ('            if response is not None:\n', '            TRACE.finish_operation(tick, TRACE_TIMER_END if before is not None else None, samples[name][-1] if before is not None else None)\n', True),
]


def adapted_benchmark(recorder, output):
    base_fn = base.adapted_benchmark(None)
    # Use the original source plus the exact five already-reviewed aggregate
    # additions. Do not rely on inspect.getsource for dynamically compiled code.
    source = inspect.getsource(base.load_bench().benchmark)
    for marker, insertion in base.INSERTIONS:
        assert source.count(marker) == 1
        source = source.replace(marker, insertion + marker)
    original = source
    for marker, insertion, before in BENCH_INSERTIONS:
        assert source.count(marker) == 1
        source = source.replace(marker, insertion + marker if before else marker + insertion)
    end_call = 'samples[name].append((time.monotonic() - tick) * 1000)'
    assert source.count(end_call) == 1
    source = source.replace(end_call, 'samples[name].append(((TRACE_TIMER_END := time.monotonic()) - tick) * 1000)')
    queue_assert = '    assert all(not item["reliableQueueDepth"] for item in final["clients"])'
    assert source.count(queue_assert) == 1
    source = source.replace(queue_assert, '    DRAIN_FAILURE.observe(final, readers, accepted_ids, cursor, repeats, wake_samples, windows)\n' + queue_assert)
    class StripTrace(ast.NodeTransformer):
        def visit_NamedExpr(self, node):
            if isinstance(node.target, ast.Name) and node.target.id == "TRACE_TIMER_END":
                return self.visit(node.value)
            return self.generic_visit(node)
        def visit_Expr(self, node):
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and isinstance(value.func.value, ast.Name) and value.func.value.id in ("TRACE", "DRAIN_FAILURE"):
                return None
            return self.generic_visit(node)
    restored = StripTrace().visit(ast.parse(source))
    assert ast.dump(restored, include_attributes=False) == ast.dump(ast.parse(original), include_attributes=False)
    failure_snapshot = DrainSnapshot(output, recorder)
    namespace = dict(base_fn.__globals__) | {"TRACE": recorder, "DRAIN_FAILURE": failure_snapshot}
    exec(compile(source, "issue102-common-boundary-benchmark", "exec"), namespace)
    if output:
        (output / "benchmark-trace.diff").write_text("".join(difflib.unified_diff(original.splitlines(True), source.splitlines(True), fromfile="reviewed five-read adapter", tofile="same adapter plus two trace observations")), encoding="utf-8")
    benchmark = namespace["benchmark"]
    async def preserve_failure(*args, **kwargs):
        return await preserve_benchmark_failure(benchmark, failure_snapshot, *args, **kwargs)
    return preserve_failure


def request_views(client_document, server_document):
    """Sparse endpoints only. Missing receive/body boundaries stay None."""
    grouped = {}
    for side, document in (("client", client_document), ("server", server_document)):
        for event in document["events"]:
            wall, cpu, name, rid, operation, details = event
            if not rid:
                continue
            group = grouped.setdefault(rid, {"rid": rid, "client": [], "server": []})
            group[side].append(event)
    result = []
    for rid, group in sorted(grouped.items()):
        server = group["server"]
        def endpoint(name, predicate=lambda details: True, last=False):
            found = [event for event in server if event[2] == name and predicate(event[5])]
            return (found[-1] if last else found[0]) if found else None
        result.append(group | {
            "asgiEntry": endpoint("asgi.enter"),
            "asgiRequestBodyComplete": endpoint("asgi.receive.return", lambda d: d["type"] == "http.request" and not d["more"]),
            "asgiFirstSendCall": endpoint("asgi.send.enter"),
            "asgiFinalSendReturn": endpoint("asgi.send.return", lambda d: d["final"], last=True),
            "asgiExit": endpoint("asgi.exit", last=True),
        })
    return result


def validate_documents(client, server):
    require_server_trace(server)
    for document in (client, server):
        if document["dropped"] or document["problems"]:
            raise TraceInvalid("trace events/correlation incomplete")
    if client["prefix"] != server["prefix"] or client["clock"] != server["clock"]:
        raise TraceInvalid("trace correlation prefix or monotonic clock differs")
    measured_ops = {event[4] for event in client["events"] if event[2] == "benchmark.operation.start" and event[5]["measured"]}
    requests = {event[3] for event in client["events"] if event[2] == "client.send.enter" and event[4] in measured_ops}
    views = {view["rid"]: view for view in request_views(client, server)}
    for rid in requests:
        view = views[rid]
        if view["asgiEntry"] is None or view["asgiFinalSendReturn"] is None or view["asgiExit"] is None:
            raise TraceInvalid("measured HTTP request lacks observed ASGI boundaries")
        # No receive/body-complete requirement: legal GETs may never call it.
    return {"measuredRequests": len(requests), "allRequests": len(views), "views": list(views.values())}


def adapted_run_mode(recorder, output):
    source = inspect.getsource(base.run_mode)
    original = source
    marker = 'str(Path(__file__).resolve()), "--gateway"'
    assert source.count(marker) == 1
    source = source.replace(marker, 'str(TRACE_ENTRY), "--trace-gateway"')
    assert source.count("b.httpx.AsyncClient(") == 2
    source = source.replace("b.httpx.AsyncClient(", "TRACE_CLIENT(")
    namespace = dict(vars(base)) | {"TRACE_ENTRY": Path(__file__).resolve(), "TRACE_CLIENT": client_factory(recorder), "adapted_benchmark": lambda root: adapted_benchmark(recorder, root)}
    exec(compile(source, "issue102-common-boundary-run-mode", "exec"), namespace)
    if output:
        (output / "run-mode-trace.diff").write_text("".join(difflib.unified_diff(original.splitlines(True), source.splitlines(True), fromfile="reviewed run_mode", tofile="same run_mode plus traced HTTP client and gateway entry")), encoding="utf-8")
    return namespace["run_mode"]


def validate_candidate_overlay(prepared):
    """Preflight only: original79 stay pinned; one declared extra in owned image."""
    expected = json.loads((ENTRY_DIR / "overlay-tree-manifest.json").read_text())
    root = Path(prepared["extraPackageOverlay"]).resolve()
    assert not str(root).startswith("/mnt/") and root.stat().st_uid == os.getuid() == 1000
    assert root.stat().st_mode & 0o077 == 0, "candidate overlay must be owned0700"
    rows = []
    for path in sorted(root.rglob("*")):
        assert not path.is_symlink(), "overlay symlink rejected"
        if path.is_file():
            rows.append({"path": path.relative_to(root).as_posix(), "sha256": base.sha(path), "bytes": path.stat().st_size})
    rows.sort(key=lambda row: row["path"])
    assert rows == expected["files"], "overlay membership or bytes drift"
    tree = hashlib.sha256("".join(row["path"] + "\0" + row["sha256"] + "\n" for row in rows).encode()).hexdigest()
    assert tree == expected["treeSha256"]
    assert Path(prepared["overlay"]).resolve() == root / "paho_mqtt-2.1.0-py3-none-any.whl"
    assert base.sha(prepared["overlay"]) == expected["originalPahoSha256"]
    for locator in (prepared["overlay"], str(root)):
        if locator not in sys.path:
            sys.path.insert(0, locator)
    module = importlib.import_module("sniffio")
    actual_origin = Path(module.__file__).resolve()
    declared = expected["declaredExtraPackage"]
    assert actual_origin == root / declared["originRelative"]
    assert version("sniffio") == declared["version"] == "1.3.1"
    overlay_distributions = sorted((item.metadata["Name"].lower().replace("_", "-"), item.version) for item in distributions(path=[str(root), prepared["overlay"]]))
    assert overlay_distributions == [("paho-mqtt", "2.1.0"), ("sniffio", "1.3.1")]
    pins = [{"name": item["name"], "expected": item["expected"], "actual": version(item["name"])} for item in prepared["requiredPackages"]]
    assert len(pins) == 79 and all(item["expected"] == item["actual"] for item in pins)
    return {"overlayRoot": str(root), "overlayTreeSha256": tree, "files": rows, "original79Pins": pins,
            "pahoSha256": base.sha(prepared["overlay"]), "declaredExtraPackage": declared,
            "extraPackageVersion": version("sniffio"), "extraPackageOrigin": str(actual_origin),
            "extraPackageOriginSha256": base.sha(actual_origin), "pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()}


def validate_go(document):
    expected = {"kind": "issue102-sniffio-overlay-fullwork-v4", "source": base.PIN, "baseProbeSha256": BASE_SHA, "instrumentSha256": base.sha(__file__), "supportSha256": base.sha(Path(__file__).with_name("repair_support_v2.py")), "outerSupervisorSha256": base.sha(ENTRY_DIR / "supervise_overlay_trace.py"), "overlayTreeSha256": json.loads((ENTRY_DIR / "overlay-tree-manifest.json").read_text())["treeSha256"], "extraPackage": json.loads((ENTRY_DIR / "overlay-tree-manifest.json").read_text())["declaredExtraPackage"], "uid": 1000, "gid": 1000, "exclusive": True, "maxRuns": 1, "outerBudgetSeconds": 480}
    if document != expected:
        raise TraceInvalid("fresh one-run trace GO required; old pair GO is consumed/incompatible")


def execution_identity():
    import pwd
    return {"uid": os.getuid(), "gid": os.getgid(), "username": pwd.getpwuid(os.getuid()).pw_name}


def require_execution_identity():
    if execution_identity() != {"uid": 1000, "gid": 1000, "username": "caii"}:
        raise TraceInvalid("future trace must use explicit caii UID/GID1000, matching the sole pair")


def trace_gateway(config_path):
    # This private child entry is reachable only from a fresh-go work process.
    validate_go(json.loads(Path(os.environ["ISSUE102_TRACE_GO"]).read_text()))
    require_execution_identity()
    b = base.load_bench()
    import uvicorn
    config = json.loads(Path(config_path).read_text())
    root = Path(config["evidence"])
    recorder = Recorder(os.environ["ISSUE102_TRACE_PREFIX"])
    original_run = uvicorn.run
    wrapped = None
    def run(app, *args, **kwargs):
        nonlocal wrapped
        identity = base.identity()
        identity["gatewayModuleOrigins"] = {name: str(Path(__import__(name, fromlist=["__name__"]).__file__).resolve()) for name in ("apps.api.app.main", "apps.api.app.sources.synthetic_source", "packages.info.store", "packages.models", "packages.vault")}
        assert all(Path(path).is_relative_to(base.SOURCE) for path in identity["gatewayModuleOrigins"].values())
        identity["candidateOverlay"] = validate_candidate_overlay(json.loads(Path(os.environ["ISSUE102_PREPARED_AUDIT"]).joinpath("environment-audit.json").read_text()))
        identity["ownedRoots"] = {key: os.environ[key] for key in ("SPATIAL_STATE_ROOT", "SPATIAL_RECORDINGS_ROOT", "LW_LIBRARY", "SPATIAL_SCENE_PREVIEWS_ROOT", "TMPDIR")}
        identity["instrumentSha256"] = base.sha(__file__)
        identity["executionIdentity"] = execution_identity()
        base.dump(root / "gateway-identity.json", identity)
        def export(phase, stopped, lifecycle_error):
            return dump_server_trace(lambda: root / "server-common-trace.json", recorder, stopped, execution_identity, phase, lifecycle_error)
        wrapped = ASGIBoundary(app, recorder, export=export)
        return original_run(wrapped, *args, **kwargs)
    uvicorn.run = run
    try:
        b.serve(types.SimpleNamespace(fixture_dir=Path(config["fixture_dir"]), entities=config["entities"], port=config["port"]))
    finally:
        uvicorn.run = original_run
        guard_export(recorder, lambda: dump_server_trace(lambda: root / "server-common-trace.json", recorder, wrapped.monitor_stopped if wrapped else True, execution_identity, "outer-finally-fallback", None))


async def run_one(root, port):
    recorder = Recorder(os.environ["ISSUE102_TRACE_PREFIX"])
    try:
        result = await adapted_run_mode(recorder, root)("shared", root, port)
        result["budgetChecks"] = base.budgets(result["measurements"])
        recorder.ensure_valid()
        server = json.loads((root / "server-common-trace.json").read_text())
        require_server_trace(server)
        if server["dropped"] or server["problems"] or not server["monitorStopped"]:
            raise TraceInvalid("server trace incomplete or monitor not stopped")
        if server["instrumentSha256"] != base.sha(__file__):
            raise TraceInvalid("server instrumentation identity drift")
        if result["gatewayIdentity"]["bootId"] != result["identity"]["bootId"] or result["gatewayIdentity"]["clock"] != result["identity"]["clock"]:
            raise TraceInvalid("gateway/client monotonic clock or boot mismatch")
        if server["executionIdentity"] != execution_identity():
            raise TraceInvalid("gateway/client UID/GID differs")
        base.dump(root / "common-request-boundaries.json", validate_documents(recorder.document(), server))
        result["traceStatus"] = "complete-instrumented-diagnostic"
        result["instrumentSha256"] = base.sha(__file__)
        base.dump(root / "receipt.json", result)
    except BaseException as exc:
        path = root / "receipt.json"
        result = json.loads(path.read_text()) if path.exists() else {"status": "invalid"}
        result["traceStatus"] = "invalid"
        result["traceFailureCategory"] = type(exc).__name__
        base.dump(path, result)
        raise
    finally:
        base.dump(root / "client-common-trace.json", recorder.document() | {"executionIdentity": execution_identity()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-gateway", type=Path)
    parser.add_argument("--work-one", type=Path)
    parser.add_argument("--go", type=Path)
    parser.add_argument("--prepared-audit", type=Path)
    parser.add_argument("--port", type=int, default=8023)
    args = parser.parse_args()
    if args.trace_gateway:
        trace_gateway(args.trace_gateway)
        return
    if not args.work_one:
        parser.error("preparation only: future outer supervisor must invoke --work-one with a new GO")
    validate_go(json.loads(args.go.read_text()))
    require_execution_identity()
    assert base.sha(base.__file__) == BASE_SHA
    assert not str(args.work_one.resolve()).startswith("/mnt/") and not args.work_one.exists()
    args.work_one.mkdir(parents=True, mode=0o700)
    os.environ.update(base.owned_environment(args.work_one))
    os.environ["ISSUE102_PREPARED_AUDIT"] = str(args.prepared_audit.resolve())
    os.environ["ISSUE102_TRACE_GO"] = str(args.go.resolve())
    os.environ["ISSUE102_TRACE_PREFIX"] = "i102" + __import__("uuid").uuid4().hex[:12]
    base.check_source_freeze()
    prepared = json.loads((args.prepared_audit / "environment-audit.json").read_text())
    candidate_overlay = validate_candidate_overlay(prepared)
    base.dump(args.work_one / "candidate-overlay-work.json", candidate_overlay)
    base.check_prepared_environment(prepared)
    # 420s internal reserve is not an outer480s guarantee. A separately reviewed
    # owned supervisor is mandatory and must include setup/cleanup/raw readback.
    if os.environ.get("ISSUE102_TRACE_OUTER") != "owned-480s-one-run":
        raise TraceInvalid("outer supervisor receipt required before Source launch")
    asyncio.run(asyncio.wait_for(run_one(args.work_one, args.port), 420))


if __name__ == "__main__":
    main()

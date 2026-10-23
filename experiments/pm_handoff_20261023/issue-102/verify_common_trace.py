"""Controlled Source-free trace checks; no sockets, Source, gateway or benchmark."""
import ast
import asyncio
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import time

import httpcore
from httpcore._trace import Trace
import httpx

import common_trace as trace


class Clock:
    def __init__(self): self.wall = 1_000_000_000; self.cpu = 1_000
    def now(self): self.wall += 1_000; return self.wall
    def thread(self): self.cpu += 100; return self.cpu


class Sentinel(Exception): pass


class Body(httpx.AsyncByteStream):
    def __init__(self, failure=None): self.failure = failure; self.closed = 0
    async def __aiter__(self):
        yield b"abc"
        if self.failure: raise self.failure
        yield b"123"
    async def aclose(self): self.closed += 1


class MemoryTransport(httpx.AsyncBaseTransport):
    """Actual httpcore.Trace context, fake byte work, zero network code."""
    def __init__(self, failure=None, body_failure=None):
        self.failure, self.body_failure = failure, body_failure
        self.requests = []; self.closed = False
    async def handle_async_request(self, request):
        self.requests.append(request)
        raw = await request.aread()
        core_request = httpcore.Request("POST", "http://controlled/", extensions=request.extensions)
        logger = logging.getLogger("httpcore.http11")
        for name in ("send_request_headers", "send_request_body", "receive_response_headers"):
            async with Trace(name, logger, core_request, {"request": "never retained"}):
                if self.failure and name == "send_request_body": raise self.failure
                await asyncio.sleep(0)
        self.body = Body(self.body_failure)
        return httpx.Response(202, headers={"x-controlled": "unchanged"}, stream=self.body, extensions={"http_version": b"HTTP/1.1", "echoBytes": raw})
    async def aclose(self): self.closed = True


async def verify(root):
    checks = []
    def passed(name): checks.append(name)
    # Literal clock and retained-RAM bounds. No payload/header object survives.
    clock = Clock(); bounded = trace.Recorder("owned", limit=3, wall=clock.now, cpu=clock.thread)
    for i in range(20): bounded.add("test", i, oversized="a" * 10000)
    assert len(bounded.events) == 3 and bounded.dropped == 17
    assert all(len(item[5]["oversized"]) == 256 for item in bounded.events)
    assert [item[0] for item in bounded.events] == [1000001000, 1000002000, 1000003000]
    assert [item[1] for item in bounded.events] == [1100, 1200, 1300]
    try: bounded.ensure_valid()
    except trace.TraceInvalid: passed("bounded-memory-overflow-invalidates-diagnostic")
    else: raise AssertionError("overflow accepted")
    passed("literal-wall-threadcpu-endpoints-and-string-bound")

    recorder = trace.Recorder("owned")
    inner = MemoryTransport()
    Client = trace.client_factory(recorder)
    prior_events = []
    async def prior(event, info): prior_events.append(event)
    async with Client(transport=inner, trust_env=False) as client:
        recorder.begin_operation("accept", 12.125, True, True)
        request = client.build_request("POST", "http://controlled/action", json={"echo": 41}, extensions={"trace": prior})
        original_body = request.content
        response = await client.send(request)
        assert response.status_code == 202 and response.content == b"abc123"
        assert response.headers["x-controlled"] == "unchanged"
        assert response.extensions["echoBytes"] == original_body == b'{"echo":41}'
        assert inner.requests[0] is request and request.headers[trace.HEADER] == "owned-1"
    assert inner.closed and inner.body.closed == 1
    names = [item[2] for item in recorder.events]
    expected = ["client.send.enter", "client.transport.enter", "httpcore.http11.send_request_headers.started", "httpcore.http11.send_request_headers.complete", "httpcore.http11.send_request_body.started", "httpcore.http11.send_request_body.complete", "httpcore.http11.receive_response_headers.started", "httpcore.http11.receive_response_headers.complete", "client.transport.headers_returned", "client.body.iterator_exhausted", "client.body.close", "client.send.return"]
    assert [name for name in names if name != "benchmark.operation.start"] == expected
    assert len(prior_events) == 6
    assert all(item[3] == 1 and item[4] == 1 for item in recorder.events if item[3])
    assert "never retained" not in json.dumps(recorder.document())
    passed("actual-installed-httpcore-trace-hook-order-and-prior-hook-preserved")
    passed("httpx-response-body-headers-status-extensions-and-request-bytes-unchanged")
    passed("client-operation-request-correlation-and-stream-close")

    for kind, failure, body in (("transport-error", Sentinel("not retained"), False), ("transport-cancel", asyncio.CancelledError("not retained"), False), ("body-error", Sentinel("not retained"), True), ("body-cancel", asyncio.CancelledError("not retained"), True)):
        rec = trace.Recorder("owned")
        transport = MemoryTransport(None if body else failure, failure if body else None)
        async with trace.client_factory(rec)(transport=transport, trust_env=False) as client:
            try: await client.get("http://controlled/no-connection")
            except BaseException as observed: assert observed is failure
            else: raise AssertionError("original failure swallowed")
        assert any(event[2] == "client.send.failure" and event[5]["category"] == type(failure).__name__ for event in rec.events)
        assert "not retained" not in json.dumps(rec.document())
        passed(kind + "-same-exception-and-cleanup")

    # Interleaving tasks have distinct ContextVar operations and HTTP IDs.
    concurrent = trace.Recorder("owned")
    async with trace.client_factory(concurrent)(transport=MemoryTransport(), trust_env=False) as client:
        async def call(name):
            concurrent.begin_operation(name, 1.0, False, False)
            await client.get("http://controlled/" + name)
        await asyncio.gather(call("one"), call("two"))
    starts = [event for event in concurrent.events if event[2] == "client.send.enter"]
    assert {(event[3], event[4]) for event in starts} == {(1, 1), (2, 2)}
    passed("concurrent-request-and-operation-identities-do-not-cross")

    async def collect(app, *, method="GET", incoming=None, receive_failure=None, send_failure=None, limit=1000, scope_type="http"):
        rec = trace.Recorder("owned", limit=limit)
        wrapped = trace.ASGIBoundary(app, rec)
        received = []; outgoing = []
        sequence = iter(incoming or [])
        async def receive():
            received.append(True)
            if receive_failure: raise receive_failure
            return next(sequence)
        async def send(message):
            if send_failure: raise send_failure
            outgoing.append(copy.deepcopy(message))
        scope = {"type": scope_type, "method": method, "path": "/controlled", "headers": [(trace.HEADER.encode(), b"owned-1")]}
        error = None
        try: await wrapped(scope, receive, send)
        except BaseException as exc: error = exc
        return rec, received, outgoing, error

    async def get_no_receive(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": [(b"x", b"unchanged")]})
        await send({"type": "http.response.body", "body": b"abc", "more_body": True})
        await send({"type": "http.response.body", "body": b"123", "more_body": False})
    server, received, outgoing, error = await collect(get_no_receive)
    assert error is None and received == [] and len(outgoing) == 3
    assert b"".join(item.get("body", b"") for item in outgoing) == b"abc123"
    view = trace.request_views({"events": []}, server.document())[0]
    assert view["asgiRequestBodyComplete"] is None
    assert view["asgiFirstSendCall"][5]["type"] == "http.response.start"
    assert view["asgiFinalSendReturn"][5]["final"] is True
    assert len([event for event in server.events if event[2] == "asgi.send.enter"]) == 3
    assert not any(event[2].startswith("asgi.receive") for event in server.events)
    passed("GET-never-receives-no-prefetch-unobserved-body-remains-null")
    passed("multiple-response-chunks-final-endpoint-and-output-preserved")
    # Move the real header emitted by our actual Client into the ASGI scope;
    # no socket or gateway is involved. Correlate two complete controlled calls.
    linked_client, linked_server = trace.Recorder("owned"), trace.Recorder("owned")
    app = trace.ASGIBoundary(get_no_receive, linked_server)
    class LinkedMemoryTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            sent = []
            async def receive(): raise AssertionError("GET body prefetched")
            async def send(message): sent.append(message)
            await app({"type": "http", "method": "GET", "path": request.url.path, "headers": request.headers.raw}, receive, send)
            return httpx.Response(sent[0]["status"], content=b"".join(message.get("body", b"") for message in sent))
    async with trace.client_factory(linked_client)(transport=LinkedMemoryTransport(), trust_env=False) as client:
        for index in range(2):
            linked_client.begin_operation("getStateOne", 1.0 + index, True, False)
            assert (await client.get("http://controlled/state")).content == b"abc123"
            linked_client.finish_operation(1.0 + index, 1.001 + index, 1.0)
    linked = trace.validate_documents(linked_client.document(), linked_server.document())
    assert linked["measuredRequests"] == 2
    assert all(view["asgiRequestBodyComplete"] is None for view in linked["views"])
    passed("actual-client-header-to-ASGI-scope-correlation-two-GETs-no-receive")
    missing_server = linked_server.document() | {"events": []}
    try: trace.validate_documents(linked_client.document(), missing_server)
    except trace.TraceInvalid: passed("missing-measured-request-boundary-fails-closed")
    else: raise AssertionError("missing measured ASGI evidence accepted")
    wrong_clock = linked_server.document() | {"clock": {"implementation": "different"}}
    try: trace.validate_documents(linked_client.document(), wrong_clock)
    except trace.TraceInvalid: passed("different-monotonic-clock-fails-closed")
    else: raise AssertionError("clock mismatch accepted")
    async def post_two(scope, receive, send):
        a, b = await receive(), await receive()
        assert a is incoming[0] and b is incoming[1]
        await send({"type": "http.response.body", "body": a["body"] + b["body"]})
    incoming = [{"type": "http.request", "body": b"ab", "more_body": True}, {"type": "http.request", "body": b"cd", "more_body": False}]
    server, receives, output, error = await collect(post_two, method="POST", incoming=incoming)
    assert error is None and len(receives) == 2 and output == [{"type": "http.response.body", "body": b"abcd"}]
    assert trace.request_views({"events": []}, server.document())[0]["asgiRequestBodyComplete"][5]["bytes"] == 2
    passed("request-chunks-preserve-calls-identities-order-and-final-boundary")
    for kind in ("app", "receive", "send"):
        for cancelled in (False, True):
            failure = asyncio.CancelledError("private") if cancelled else Sentinel("private")
            async def app(scope, receive, send):
                if kind == "app": raise failure
                if kind == "receive": await receive()
                else: await send({"type": "http.response.body", "body": b"x"})
            rec, _, _, error = await collect(app, receive_failure=failure if kind == "receive" else None, send_failure=failure if kind == "send" else None)
            assert error is failure and rec.events[-1][2] == "asgi.exit"
            assert any(event[2] == "asgi.failure" and event[5]["category"] == type(failure).__name__ for event in rec.events)
            assert "private" not in json.dumps(rec.document())
            passed("ASGI-" + kind + ("-cancel" if cancelled else "-exception") + "-preserved")
    # Overflow cannot change app output, but it makes diagnostic validity false.
    over, _, output, error = await collect(get_no_receive, limit=2)
    assert error is None and output == outgoing and over.dropped > 0
    try: over.ensure_valid()
    except trace.TraceInvalid: passed("ASGI-overflow-preserves-output-invalidates-trace")
    else: raise AssertionError("server overflow accepted")
    async def non_http(scope, receive, send): await send({"type": "controlled.pass"})
    non, received, output, error = await collect(non_http, scope_type="websocket")
    assert not non.events and output == [{"type": "controlled.pass"}]
    passed("websocket-scope-directly-delegated-unchanged")
    corr = trace.Recorder("owned", limit=2)
    scope = {"headers": [(trace.HEADER.encode(), b"owned-1")]}
    assert corr.server_id(scope) == 1 and corr.server_id(scope) == 0
    assert corr.server_id({"headers": [(trace.HEADER.encode(), b"foreign-2")]}) == 0
    try: corr.ensure_valid()
    except trace.TraceInvalid: passed("foreign-duplicate-correlation-fails-closed-with-bounded-state")
    else: raise AssertionError("correlation errors accepted")

    lifecycle = trace.ASGIBoundary(non_http, trace.Recorder("owned"), period=.001)
    lifecycle.start_monitor(); await asyncio.sleep(.003); await lifecycle.stop_monitor()
    assert lifecycle.monitor.done() and lifecycle.monitor_stopped
    passed("sparse-monitor-cancels-and-leaves-no-task")
    for cancel in (False, True):
        failure = asyncio.CancelledError("lifecycle") if cancel else Sentinel("lifecycle")
        async def lifespan(scope, receive, send):
            await send({"type": "lifespan.startup.complete"})
            raise failure
        lifecycle = trace.ASGIBoundary(lifespan, trace.Recorder("owned"))
        async def receive_unused(): raise AssertionError("unexpected lifecycle receive")
        async def send_unchanged(message): assert message == {"type": "lifespan.startup.complete"}
        try: await lifecycle({"type": "lifespan"}, receive_unused, send_unchanged)
        except BaseException as error: assert error is failure
        else: raise AssertionError("lifespan error swallowed")
        assert lifecycle.monitor.done() and lifecycle.monitor_stopped
        passed("lifespan-" + ("cancel" if cancel else "exception") + "-stops-owned-monitor")
    # Original sampling expression: one existing monotonic read, same exact
    # arithmetic, raw start/end values retained; no approximate reconstruction.
    calls = []
    exact_end = 12.1325
    def monotonic(): calls.append(True); return exact_end
    namespace = {"samples": {"accept": []}, "name": "accept", "tick": 12.125, "time": type("T", (), {"monotonic": staticmethod(monotonic)})}
    exec('samples[name].append(((TRACE_TIMER_END := time.monotonic()) - tick) * 1000)', namespace)
    assert len(calls) == 1 and namespace["TRACE_TIMER_END"] == exact_end
    assert namespace["samples"]["accept"] == [(exact_end - 12.125) * 1000]
    literal = trace.Recorder("owned")
    literal.begin_operation("accept", 12.125, True, True)
    literal.finish_operation(12.125, exact_end, namespace["samples"]["accept"][0])
    assert literal.events[-1][5]["originalEndSeconds"] == exact_end
    assert literal.events[-1][5]["originalTickSeconds"] == 12.125
    passed("original-timer-values-single-read-and-identical-arithmetic")
    # Reuse52 prior checks by frozen code identity plus exact AST reduction, not
    # a blind rerun of the original process/Reader fixture suite.
    assert trace.base.sha(trace.base.__file__) == trace.BASE_SHA
    trace.adapted_benchmark(trace.Recorder("owned"), root)
    trace.adapted_run_mode(trace.Recorder("owned"), root)
    prior52 = json.loads((trace.base.HERE / "method-review-20261023-final/verification/verification.json").read_text())
    assert prior52["count"] == 52 and prior52["identity"]["probeSha256"] == trace.BASE_SHA
    trace.base.check_source_freeze()
    prepared = json.loads(Path(os.environ.get("ISSUE102_COMMON_AUDIT", "/tmp/spatial-issue102-common-devcc-context-20261023") + "/environment-audit.json").read_text())
    environment = trace.base.check_prepared_environment(prepared)
    passed("base52-reused-by-exact-probe-identity-and-assertion-timer-AST-equivalence")
    old_go = json.loads((trace.base.HERE / "root-go-20261023.json").read_text())
    try: trace.validate_go(old_go)
    except trace.TraceInvalid: passed("consumed-pair-GO-cannot-authorize-new-trace")
    else: raise AssertionError("old GO accepted")
    supervisor_tree = ast.parse((trace.base.HERE / "supervise_common_trace.py").read_text())
    calls = [node for node in ast.walk(supervisor_tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "supervise"]
    assert len(calls) == 1
    helper = trace.base.HERE / "run_authorized_pair.py"
    old_selftest = json.loads((trace.base.HERE / "outer-supervisor-selftest.json").read_text())
    assert old_selftest["deadlineSatisfied"] and not old_selftest["survivorsAfterParent"]
    actual_outer = json.loads((trace.base.HERE / "pair-20261023T000920Z/outer-window.json").read_text())
    assert trace.base.sha(helper) == actual_outer["supervisorSha256"]
    passed("one-child-supervisor-call-and-prior-owned-timeout-proof-reused")
    httpcore_root = Path(inspect_trace_path()).parent
    httpx_root = Path(httpx.__file__).parent
    hook_sources = {"httpcoreTrace": Path(inspect_trace_path()), "httpcoreHTTP11": httpcore_root / "_async/http11.py", "httpxDefaultTransport": httpx_root / "_transports/default.py", "httpxClient": httpx_root / "_client.py"}
    assert not any(name in sys.modules for name in ("newton", "warp", "mujoco", "pxr"))
    result = {"status": "passed", "checks": checks, "count": len(checks), "sourceStarts": 0, "gatewayStarts": 0, "socketConnections": 0, "benchmarkRuns": 0, "base52Rerun": False, "baseProbeSha256": trace.BASE_SHA, "instrumentSha256": trace.base.sha(trace.__file__), "supervisorSha256": trace.base.sha(trace.base.HERE / "supervise_common_trace.py"), "reusedDeadlineHelperSha256": trace.base.sha(helper), "elapsedSeconds": time.monotonic() - STARTED, "environment": environment | {"python": sys.executable, "httpx": httpx.__version__, "httpcore": httpcore.__version__, "clock": vars(time.get_clock_info("monotonic")), "hookSources": {name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for name, path in hook_sources.items()}}, "ownedTasksLeft": [task.get_name() for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]}
    assert not result["ownedTasksLeft"] and result["elapsedSeconds"] <= 300
    (root / "trace-verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "count", "sourceStarts", "gatewayStarts", "socketConnections", "benchmarkRuns", "elapsedSeconds", "instrumentSha256")}))


def inspect_trace_path():
    import inspect
    return inspect.getsourcefile(Trace)


if __name__ == "__main__":
    STARTED = time.monotonic()
    root = Path(sys.argv[1]).resolve()
    assert not root.exists() and not str(root).startswith("/mnt/")
    root.mkdir(parents=True, mode=0o700)
    asyncio.run(verify(root))

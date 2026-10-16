"""Real authenticated loopback TLS at the SpatialSource/public Gateway boundary."""

import asyncio
import json
import secrets
import socket
import ssl
import struct
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from websockets.asyncio.client import connect

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.remote_source import RemoteSource
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.schema import Command, SceneManifest, SessionOptions, TransformFrame
from packages.transport import FrameCodec
from packages.transport.remote import (
    FRAME,
    JSON,
    PACKET_HEADER,
    Attached,
    Authenticated,
    Hello,
    Limits,
    Ready,
    RemoteConfig,
    Request,
    ServerConfig,
    Start,
    close_stream,
    json_bytes,
    parse_json,
    receive,
    send,
)
from packages.transport.remote_server import RunnerServer


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    directory = tmp_path_factory.mktemp("runner-tls")
    openssl = Path("C:/Program Files/Git/usr/bin/openssl.exe")
    if not openssl.exists():
        import shutil

        openssl = shutil.which("openssl")
    assert openssl, "OpenSSL is required to generate disposable test certificates"
    cert, key = directory / "cert.pem", directory / "private.pem"
    subprocess.run(
        [
            str(openssl),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )

    class DisposableToken(str):
        def __repr__(self):
            return "'<redacted disposable token>'"

    try:
        yield cert, key, DisposableToken(secrets.token_urlsafe(32))
    finally:
        key.unlink(missing_ok=True)
        cert.unlink(missing_ok=True)


@pytest.mark.parametrize("unicode_token", [False, True])
async def test_real_tls_source_preserves_authority_and_gateway_receipt_clock(tls_material, unicode_token):
    cert, key, token = tls_material
    if unicode_token:
        token += "令牌"
    closed = []

    class RemoteClockSynthetic(SyntheticSource):
        def _publish(self, kind="transform.frame"):
            frame = super()._publish(kind)
            self._latest = frame.model_copy(update={"sampleTimeMs": 1.0})
            return self._latest

        async def close(self):
            await super().close()
            closed.append(self.session_id)

    async with RunnerServer(
        ServerConfig(cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic"),
        source_factory=RemoteClockSynthetic,
    ) as server:
        source = RemoteSource(
            "remote-clock", RemoteConfig(port=server.port, ca_file=cert, token=token, source_kind="synthetic")
        )
        before = time.monotonic() * 1000
        try:
            manifest = await source.start("lab-demo", SessionOptions())
            assert manifest.source.sampleTimeClock == "gateway-receive"
            initial = source.latest_frame()
            assert before <= initial.sampleTimeMs <= time.monotonic() * 1000
            async with asyncio.timeout(2):
                while source.latest_frame().seq <= initial.seq:
                    await asyncio.sleep(0.01)
            assert before <= source.latest_frame().sampleTimeMs <= time.monotonic() * 1000
            paused = await source.execute(Command(requestId="pause", command="pause"))
            assert paused.state.state == "PAUSED"
            assert paused.frame.seq == paused.state.seq
            assert before <= paused.frame.sampleTimeMs <= time.monotonic() * 1000
            step = await source.execute(Command(requestId="step", command="step", args={"count": 24}))
            assert step.ack.simTime == pytest.approx(paused.ack.simTime + 0.1)
            assert step.frame.seq > paused.frame.seq
            replay = await source.execute(Command(requestId="step", command="step", args={"count": 24}))
            assert replay.frame.seq == step.frame.seq and replay.frame.simTime == step.frame.simTime
            reset = await source.execute(Command(requestId="reset", command="reset"))
            assert reset.frame.epoch == 1 and reset.frame.simTime == 0
            assert reset.frame.seq > step.frame.seq
            joint = await source.query_joint_state(manifest.joints[0].entityKey)
            assert joint.epoch == 1
            with pytest.raises(KeyError):
                await source.read_mesh("unknown")
        finally:
            await source.close()
            await source.close()
        assert source.stats()["remoteTasksAlive"] == 0
    assert closed == ["remote-clock"]
    assert server.active_sessions == 0


@asynccontextmanager
async def public_gateway(config):
    app = create_app(
        Settings(
            runner_config=config, allowed_origins=("http://127.0.0.1:5188",), heartbeat_interval_seconds=0.05
        )
    )
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 15)
        sock.close()


async def read_until(ws, predicate):
    seen = []
    async with asyncio.timeout(5):
        while True:
            value = json.loads(await ws.recv())
            seen.append(value)
            if predicate(value):
                return seen


async def public_subscribe(ws, revision):
    await ws.send(json.dumps({"type": "client.subscribe", "sceneRevision": revision, "scope": "all"}))
    await read_until(ws, lambda item: item["type"] == "subscription.ack")
    return (await read_until(ws, lambda item: item["type"] == "stream.snapshot"))[-1]


async def test_actual_tcp_gateway_two_observers_reload_peer_loss_and_restart(tls_material, record_property):
    cert, key, token = tls_material
    gltf = b'{"asset":{"version":"2.0"},"scene":0,"scenes":[{"nodes":[]}]}'
    gltf += b" " * (-len(gltf) % 4)
    mesh = struct.pack("<III", 0x46546C67, 2, 20 + len(gltf)) + struct.pack("<I4s", len(gltf), b"JSON") + gltf
    mesh_id = "remote-fixture"

    class MeshSynthetic(SyntheticSource):
        def _manifest(self):
            data = super()._manifest().model_dump(mode="json")
            body = next(node for node in data["nodes"] if node["kind"] == "body")
            body["visuals"].append(
                {
                    "kind": "mesh",
                    "meshId": mesh_id,
                    "url": f"/meshes/{mesh_id}",
                    "assetUpAxis": "Z",
                    "scale": [1, 1, 1],
                    "localPose": {"p": [0, 0, 0], "q": [0, 0, 0, 1]},
                }
            )
            return SceneManifest.model_validate(data)

        async def read_mesh(self, key):
            if key != mesh_id:
                raise KeyError(key)
            return mesh

    server_config = ServerConfig(cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic")
    async with RunnerServer(server_config, source_factory=MeshSynthetic) as remote:
        port = remote.port
        config = RemoteConfig(port=port, ca_file=cert, token=token, source_kind="synthetic")
        async with public_gateway(config) as base, httpx.AsyncClient(base_url=base) as http:
            created = await http.post("/api/v1/sessions", json={"sceneId": "lab-demo", "source": "synthetic"})
            assert created.status_code == 201, created.text
            sid = created.json()["sessionId"]
            route = f"/api/v1/sessions/{sid}"
            manifest = (await http.get(route + "/scene")).json()
            assert manifest["source"]["sampleTimeClock"] == "gateway-receive"
            ws_url = base.replace("http:", "ws:") + route + "/ws"
            async with (
                connect(ws_url, origin="http://127.0.0.1:5188") as first,
                connect(ws_url, origin="http://127.0.0.1:5188") as second,
            ):
                for ws in (first, second):
                    ready = json.loads(await ws.recv())
                    assert ready["sampleTimeClock"] == "gateway-receive"
                    baseline = await public_subscribe(ws, manifest["sceneRevision"])
                    assert len(baseline["poses"]) == 24
                periodic = (await read_until(first, lambda item: item["type"] == "transform.frame"))[-1]
                assert periodic["seq"] >= baseline["seq"]
                traces = []
                for command in ("pause", "step", "reset", "resume"):
                    await first.send(
                        json.dumps({"type": "command.request", "requestId": command, "command": command})
                    )

                    def matches(item):
                        return item["type"] == "session.state" and item["cause"].get("requestId") == command

                    trace = await read_until(first, matches)
                    other = await read_until(second, matches)
                    state = trace[-1]
                    assert state == other[-1]
                    frame_index = next(
                        i
                        for i, item in enumerate(trace)
                        if item["type"] in ("stream.snapshot", "transform.frame")
                        and item["seq"] == state["seq"]
                    )
                    ack_index = next(i for i, item in enumerate(trace) if item["type"] == "command.ack")
                    assert frame_index < ack_index < len(trace) - 1
                    if command == "pause":
                        snapshot = (await http.get(route + "/snapshot")).json()
                        assert snapshot["seq"] == state["seq"]
                        assert snapshot["poses"] == trace[frame_index]["poses"]
                        joint = await http.get(
                            route + "/joints/state", params={"key": manifest["joints"][0]["entityKey"]}
                        )
                        assert joint.status_code == 200
                        glb = await http.get(route + f"/meshes/{mesh_id}")
                        assert glb.content == mesh and glb.headers["content-type"] == "model/gltf-binary"
                    if command == "reset":
                        assert state["epoch"] == 1 and state["simTime"] == 0
                    traces.append({"command": command, "seq": state["seq"], "epoch": state["epoch"]})
                replaced = await http.post(route + "/reload")
                assert replaced.status_code == 200, replaced.text
                replacement = replaced.json()
                assert replacement["sceneRevision"] != manifest["sceneRevision"]
                assert replacement["revisionNumericId"] > manifest["revisionNumericId"]
                for ws in (first, second):
                    await read_until(ws, lambda item: item["type"] == "scene.replaced")
                    recovered = await public_subscribe(ws, replacement["sceneRevision"])
                    assert recovered["seq"] > traces[-1]["seq"] and len(recovered["poses"]) == 24
                await asyncio.wait_for(remote.close(), 5)
                for ws in (first, second):
                    await read_until(
                        ws, lambda item: item["type"] == "session.state" and item["state"] == "FAILED"
                    )
                assert (await http.get("/api/v1/health")).status_code == 200
                async with RunnerServer(
                    ServerConfig(
                        cert_file=cert, key_file=key, token=token, port=port, source_kind="synthetic"
                    )
                ) as restored:
                    restarted = await http.post(route + "/restart")
                    assert restarted.status_code == 200, restarted.text
                    assert restarted.json()["state"] == "RUNNING"
                    assert restored.active_sessions == 1
                    assert (await http.delete(route)).status_code == 204
                record_property(
                    "remote_public_journey",
                    json.dumps(
                        {
                            "transport": "actual loopback TLS",
                            "gateway": "actual loopback HTTP/WebSocket",
                            "bodies": 24,
                            "observers": 2,
                            "commands": traces,
                            "meshBytes": len(mesh),
                            "reload": True,
                            "peerLossFailed": True,
                            "healthSurvived": True,
                            "restart": True,
                        }
                    ),
                )


async def raw_connection(server, cert):
    return await asyncio.open_connection(
        "127.0.0.1", server.port, ssl=ssl.create_default_context(cafile=cert), server_hostname="localhost"
    )


async def raw_pair(server, cert, token, sid="raw-peer"):
    control, writer = await raw_connection(server, cert)
    await send(writer, JSON, json_bytes(Hello(role="control", token=token)), server.limits)
    _, payload = await receive(control, server.limits, allowed=(JSON,))
    authenticated = parse_json(payload)
    assert isinstance(authenticated, Authenticated)
    frames, frame_writer = await raw_connection(server, cert)
    await send(
        frame_writer,
        JSON,
        json_bytes(Hello(role="frames", token=token, ticket=authenticated.ticket)),
        server.limits,
    )
    await receive(frames, server.limits, allowed=(JSON,))
    await send(
        writer,
        JSON,
        json_bytes(Start(sessionId=sid, sceneId="lab-demo", options=SessionOptions())),
        server.limits,
    )
    await receive(control, server.limits, allowed=(JSON,))
    kind, payload = await receive(control, server.limits, allowed=(FRAME,))
    assert kind == FRAME and payload[:4] == b"NSYN"
    return control, writer, frames, frame_writer, authenticated.ticket


async def eventually(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.parametrize(
    "case", ["unauthorized", "unpaired", "oversized", "malformed", "nonfinite", "pickle"]
)
async def test_untrusted_tls_input_rejected_before_source_start(tls_material, case):
    cert, key, token = tls_material
    starts = []
    limits = Limits(handshake_seconds=0.2, heartbeat_seconds=0.05, read_seconds=0.5, control_bytes=2048)

    def source_factory(sid):
        starts.append(sid)
        return SyntheticSource(sid)

    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=limits
        ),
        source_factory=source_factory,
    ) as server:
        reader, writer = await raw_connection(server, cert)
        try:
            if case == "oversized":
                writer.write(PACKET_HEADER.pack(JSON, limits.control_bytes + 1))
                await writer.drain()
            else:
                payload = {
                    "unauthorized": json_bytes(Hello(role="control", token="x" * 32)),
                    "unpaired": json_bytes(Hello(role="frames", token=token, ticket="unknown-peer")),
                    "malformed": b'{"kind":"hello",broken}',
                    "nonfinite": b'{"kind":"hello","version":NaN}',
                    "pickle": b"\x80\x04cposix\nsystem\n.",
                }[case]
                await send(writer, JSON, payload, limits)
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert starts == []
        finally:
            await close_stream(writer, 1)
    assert server.active_sessions == 0


@pytest.mark.parametrize("case", ["wrong-certificate", "wrong-hostname"])
async def test_certificate_verification_is_mandatory(tls_material, case):
    import certifi

    cert, key, token = tls_material
    starts = []
    async with RunnerServer(
        ServerConfig(cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic"),
        source_factory=lambda sid: starts.append(sid),
    ) as server:
        source = RemoteSource(
            "untrusted-server",
            RemoteConfig(
                port=server.port,
                token=token,
                source_kind="synthetic",
                ca_file=Path(certifi.where()) if case == "wrong-certificate" else cert,
                server_name="localhost" if case == "wrong-certificate" else "not-this-host.invalid",
            ),
        )
        with pytest.raises(ssl.SSLCertVerificationError):
            await source.start("lab-demo", SessionOptions())
        assert starts == [] and source.stats()["remoteTasksAlive"] == 0


async def test_unpaired_control_times_out_and_duplicate_frame_cannot_kill_valid_peer(tls_material):
    cert, key, token = tls_material
    starts, closed = [], asyncio.Event()
    limits = Limits(handshake_seconds=0.15, heartbeat_seconds=0.05, read_seconds=0.5)

    class OwnedSynthetic(SyntheticSource):
        def __init__(self, sid):
            starts.append(sid)
            super().__init__(sid)

        async def close(self):
            await super().close()
            closed.set()

    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=limits
        ),
        source_factory=OwnedSynthetic,
    ) as server:
        reader, writer = await raw_connection(server, cert)
        await send(writer, JSON, json_bytes(Hello(role="control", token=token)), limits)
        await receive(reader, limits, allowed=(JSON,))
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert starts == []
        await close_stream(writer, 1)
        await eventually(lambda: server.active_sessions == 0)
        control, writer, frames, frame_writer, ticket = await raw_pair(server, cert, token)
        rogue, rogue_writer = await raw_connection(server, cert)
        try:
            await send(
                rogue_writer, JSON, json_bytes(Hello(role="frames", token=token, ticket=ticket)), limits
            )
            assert await asyncio.wait_for(rogue.read(), 1) == b""
            assert server.active_sessions == 1 and not closed.is_set()
            await close_stream(frame_writer, 1)
            await asyncio.wait_for(closed.wait(), 1)
            await eventually(lambda: server.active_sessions == 0)
            await asyncio.wait_for(control.read(), 1)  # Drain legal entity/heartbeat packets before EOF.
            assert control.at_eof()
        finally:
            await asyncio.gather(
                *(close_stream(stream, 1) for stream in (writer, frame_writer, rogue_writer))
            )


async def test_remote_request_timeout_fails_and_cleans_both_channels_and_owned_source(tls_material):
    cert, key, token = tls_material
    closed = asyncio.Event()

    class SlowSynthetic(SyntheticSource):
        async def execute(self, command):
            await asyncio.Event().wait()

        async def close(self):
            await super().close()
            closed.set()

    limits = Limits(request_seconds=0.1, heartbeat_seconds=0.05, read_seconds=0.5, close_seconds=0.2)
    async with RunnerServer(
        ServerConfig(cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic"),
        source_factory=SlowSynthetic,
    ) as server:
        source = RemoteSource(
            "request-timeout",
            RemoteConfig(port=server.port, ca_file=cert, token=token, source_kind="synthetic", limits=limits),
        )
        await source.start("lab-demo", SessionOptions())
        try:
            with pytest.raises(TimeoutError):
                await source.execute(Command(requestId="timeout", command="pause"))
            async with asyncio.timeout(1):
                async for event in source.events():
                    if event.type == "server.error":
                        assert event.code == "source_unavailable"
                        break
                else:
                    raise AssertionError("Remote timeout did not emit source_unavailable")
            await asyncio.wait_for(closed.wait(), 1)
        finally:
            await source.close()
        assert source.stats()["remoteTasksAlive"] == 0
        await eventually(lambda: server.active_sessions == 0)


@pytest.mark.parametrize("corruption", ["oversized", "truncated", "magic", "revision", "count", "nonfinite"])
async def test_gateway_rejects_malformed_nsynthrough_actual_tls_frame_channel(tls_material, corruption):
    cert, key, token = tls_material
    config = ServerConfig(cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic")
    limits = Limits(frame_bytes=4096, heartbeat_seconds=0.05, read_seconds=0.5)
    attached, baseline_ready = asyncio.Event(), asyncio.Event()
    ticket = secrets.token_urlsafe(32)
    tasks, streams = set(), set()
    malicious = None

    async def accept(reader, writer):
        nonlocal malicious
        tasks.add(asyncio.current_task())
        streams.add(writer)
        try:
            _, payload = await receive(reader, limits, allowed=(JSON,))
            hello = parse_json(payload)
            if hello.role == "control":
                await send(writer, JSON, json_bytes(Authenticated(ticket=ticket)), limits)
                await attached.wait()
                _, payload = await receive(reader, limits, allowed=(JSON,))
                start = parse_json(payload)
                fixture = SyntheticSource(start.sessionId)
                manifest = fixture._manifest()
                frame = TransformFrame(
                    type="stream.snapshot",
                    sessionId=start.sessionId,
                    sceneRevision=manifest.sceneRevision,
                    epoch=0,
                    seq=1,
                    simTime=0,
                    sampleTimeMs=1,
                    poses=fixture._poses(),
                )
                malicious = bytearray(FrameCodec(start.sessionId, manifest.sceneRevision, 1).encode(frame))
                if corruption == "magic":
                    malicious[:4] = b"BAD!"
                if corruption == "revision":
                    struct.pack_into("<I", malicious, 8, 2)
                if corruption == "count":
                    struct.pack_into("<I", malicious, 40, 100)
                if corruption == "nonfinite":
                    struct.pack_into("<f", malicious, 48, float("nan"))
                await send(writer, JSON, json_bytes(Ready(manifest=manifest)), limits)
                await send(
                    writer,
                    FRAME,
                    FrameCodec(start.sessionId, manifest.sceneRevision, 1).encode(frame),
                    limits,
                )
                baseline_ready.set()
                await reader.read()  # Keep control alive so failure is attributable to the frame channel.
            else:
                assert hello.ticket == ticket
                await send(writer, JSON, json_bytes(Attached()), limits)
                attached.set()
                await baseline_ready.wait()
                if corruption == "oversized":
                    writer.write(PACKET_HEADER.pack(FRAME, limits.frame_bytes + 1))
                elif corruption == "truncated":
                    writer.write(PACKET_HEADER.pack(FRAME, len(malicious)))
                    writer.write(malicious[:10])
                    await writer.drain()
                    return
                else:
                    await send(writer, FRAME, bytes(malicious), limits)
                await writer.drain()
                await reader.read()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            await close_stream(writer, 1)
            streams.discard(writer)

    server = await asyncio.start_server(accept, "127.0.0.1", 0, ssl=config.tls_context())
    source = RemoteSource(
        "malformed-frame",
        RemoteConfig(
            port=server.sockets[0].getsockname()[1],
            ca_file=cert,
            token=token,
            source_kind="synthetic",
            limits=limits,
        ),
    )
    try:
        await source.start("lab-demo", SessionOptions())
        async with asyncio.timeout(1):
            async for event in source.events():
                if event.type == "server.error":
                    assert event.code == "source_unavailable"
                    assert (
                        "IncompleteReadError"
                        if corruption == "truncated"
                        else "ValidationError"
                        if corruption == "nonfinite"
                        else "ValueError"
                    ) in event.message
                    break
            else:
                raise AssertionError("Malformed periodic packet was accepted")
    finally:
        await source.close()
        server.close()
        await asyncio.gather(*(close_stream(writer, 1) for writer in list(streams)), return_exceptions=True)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()
    assert source.stats()["remoteTasksAlive"] == 0


async def test_source_start_timeout_releases_authenticated_owner(tls_material):
    cert, key, token = tls_material
    closed = asyncio.Event()

    class SlowStart(SyntheticSource):
        async def start(self, scene_id, options):
            await super().start(scene_id, options)
            await asyncio.Event().wait()

        async def close(self):
            await super().close()
            closed.set()

    limits = Limits(start_seconds=0.1, heartbeat_seconds=0.05, read_seconds=0.5)
    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=limits
        ),
        source_factory=SlowStart,
    ) as server:
        source = RemoteSource(
            "start-timeout",
            RemoteConfig(ca_file=cert, token=token, port=server.port, source_kind="synthetic"),
        )
        with pytest.raises(ValueError, match="ready"):
            await source.start("lab-demo", SessionOptions())
        await asyncio.wait_for(closed.wait(), 1)
        await eventually(lambda: server.active_sessions == 0)
        assert source.stats()["remoteTasksAlive"] == 0


async def test_stalled_tls_handshake_has_finite_connect_timeout(tls_material):
    cert, _, token = tls_material
    closed = asyncio.Event()

    async def stall(reader, writer):
        try:
            await reader.read()
        finally:
            await close_stream(writer, 1)
            closed.set()

    listener = await asyncio.start_server(stall, "127.0.0.1", 0)
    source = RemoteSource(
        "handshake-timeout",
        RemoteConfig(
            ca_file=cert,
            token=token,
            port=listener.sockets[0].getsockname()[1],
            limits=Limits(handshake_seconds=0.1),
        ),
    )
    started = time.monotonic()
    try:
        with pytest.raises((TimeoutError, ConnectionAbortedError)):
            await source.start("falling-boxes", SessionOptions())
        assert time.monotonic() - started < 1
        await asyncio.wait_for(closed.wait(), 1)
        assert source.stats()["remoteTasksAlive"] == 0
    finally:
        await source.close()
        listener.close()
        await listener.wait_closed()


async def test_slow_tls_control_peer_times_out_without_blocking_source_or_next_peer(
    tls_material, record_property
):
    cert, key, token = tls_material
    closed = asyncio.Event()
    data = b"x" * (16 * 1024 * 1024)

    class LargeMesh(SyntheticSource):
        async def read_mesh(self, mesh_id):
            return data

        async def close(self):
            await super().close()
            closed.set()

    limits = Limits(request_seconds=0.1, heartbeat_seconds=0.05, read_seconds=0.5, close_seconds=0.2)
    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=limits
        ),
        source_factory=LargeMesh,
    ) as server:
        _, writer, _, frame_writer, _ = await raw_pair(server, cert, token, "slow-peer")
        try:
            await send(writer, JSON, json_bytes(Request(kind="mesh", token="blocked", key="large")), limits)
            # Stop consuming control while its sole sender writes a bounded binary mesh.
            await asyncio.wait_for(closed.wait(), 2)
            await eventually(lambda: server.active_sessions == 0)
        finally:
            await asyncio.gather(close_stream(writer, 0.2), close_stream(frame_writer, 0.2))
        replacement = RemoteSource(
            "healthy-peer",
            RemoteConfig(port=server.port, ca_file=cert, token=token, source_kind="synthetic", limits=limits),
        )
        try:
            await replacement.start("lab-demo", SessionOptions())
            assert len(replacement.latest_frame().poses) == 24
        finally:
            await replacement.close()
        record_property(
            "slow_peer",
            json.dumps(
                {
                    "meshBytes": len(data),
                    "sendTimeoutSeconds": 0.1,
                    "ownedSourceClosed": True,
                    "nextPeerHealthy": True,
                }
            ),
        )


async def test_remote_source_preserves_no_observer_auto_pause_policy(tls_material):
    cert, key, token = tls_material
    async with RunnerServer(
        ServerConfig(cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic")
    ) as remote:
        config = RemoteConfig(port=remote.port, ca_file=cert, token=token, source_kind="synthetic")
        async with public_gateway(config) as base, httpx.AsyncClient(base_url=base) as http:
            created = await http.post(
                "/api/v1/sessions",
                json={"sceneId": "lab-demo", "source": "synthetic", "continueWithoutObservers": False},
            )
            assert created.status_code == 201 and created.json()["state"] == "PAUSED"
            route = f"/api/v1/sessions/{created.json()['sessionId']}"
            async with connect(
                base.replace("http:", "ws:") + route + "/ws", origin="http://127.0.0.1:5188"
            ) as ws:
                ready = json.loads(await ws.recv())
                assert ready["sessionState"] == "PAUSED"
                await public_subscribe(ws, created.json()["sceneRevision"])
                async with asyncio.timeout(2):
                    while (await http.get(route)).json()["state"] != "RUNNING":
                        await asyncio.sleep(0.01)
            async with asyncio.timeout(2):
                while (await http.get(route)).json()["state"] != "PAUSED":
                    await asyncio.sleep(0.01)
            assert (await http.delete(route)).status_code == 204


async def test_read_only_query_timeout_keeps_authority_and_http_504_behavior(tls_material):
    cert, key, token = tls_material

    class SlowJoint(SyntheticSource):
        async def query_joint_state(self, key):
            await asyncio.Event().wait()

    limits = Limits(request_seconds=0.08)
    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=limits
        ),
        source_factory=SlowJoint,
    ) as remote:
        config = RemoteConfig(port=remote.port, ca_file=cert, token=token, source_kind="synthetic")
        async with public_gateway(config) as base, httpx.AsyncClient(base_url=base) as http:
            created = await http.post("/api/v1/sessions", json={"sceneId": "lab-demo", "source": "synthetic"})
            route = f"/api/v1/sessions/{created.json()['sessionId']}"
            manifest = (await http.get(route + "/scene")).json()
            response = await http.get(
                route + "/joints/state", params={"key": manifest["joints"][0]["entityKey"]}
            )
            assert response.status_code == 504
            assert (await http.get(route)).json()["state"] == "RUNNING"
            assert remote.active_sessions == 1
            assert (await http.get(route + "/snapshot")).status_code == 200
            async with connect(
                base.replace("http:", "ws:") + route + "/ws", origin="http://127.0.0.1:5188"
            ) as ws:
                await ws.recv()
                baseline = await public_subscribe(ws, manifest["sceneRevision"])
                periodic = (
                    await read_until(
                        ws, lambda item: item["type"] == "transform.frame" and item["seq"] > baseline["seq"]
                    )
                )[-1]
                await ws.send(
                    json.dumps(
                        {"type": "command.request", "requestId": "after-query-timeout", "command": "pause"}
                    )
                )
                trace = await read_until(
                    ws,
                    lambda item: (
                        item["type"] == "session.state"
                        and item["cause"].get("requestId") == "after-query-timeout"
                    ),
                )
                assert trace[-1]["state"] == "PAUSED" and trace[-1]["seq"] > periodic["seq"]
                assert any(item["type"] == "command.ack" and item["status"] == "applied" for item in trace)
            await http.delete(route)


@pytest.mark.newton
async def test_optional_actual_newton_cpu_remote_gateway_and_cleanup(tls_material, record_property):
    import importlib.util
    import multiprocessing as mp
    import os

    if importlib.util.find_spec("newton") is None:
        pytest.skip("optional simulation extra is not installed")
    cert, key, token = tls_material
    async with RunnerServer(ServerConfig(cert_file=cert, key_file=key, token=token, port=0)) as server:
        config = RemoteConfig(ca_file=cert, token=token, port=server.port)
        async with public_gateway(config) as base, httpx.AsyncClient(base_url=base, timeout=180) as http:
            response = await http.post(
                "/api/v1/sessions", json={"sceneId": "falling-boxes", "source": "newton"}
            )
            assert response.status_code == 201, response.text
            sid = response.json()["sessionId"]
            route = f"/api/v1/sessions/{sid}"
            manifest = (await http.get(route + "/scene")).json()
            stats = (await http.get(route + "/stats")).json()
            pid = stats["runnerPID"]
            assert pid != os.getpid() and stats["runnerPIDIsRemote"] is True
            async with connect(
                base.replace("http:", "ws:") + route + "/ws", origin="http://127.0.0.1:5188"
            ) as ws:
                assert json.loads(await ws.recv())["sampleTimeClock"] == "gateway-receive"
                await public_subscribe(ws, manifest["sceneRevision"])
                samples = []
                for command in ("pause", "step", "reset", "resume"):
                    await ws.send(
                        json.dumps(
                            {
                                "type": "command.request",
                                "requestId": command,
                                "command": command,
                                "args": {"count": 24} if command == "step" else {},
                            }
                        )
                    )
                    trace = await read_until(
                        ws,
                        lambda item: (
                            item["type"] == "session.state" and item["cause"].get("requestId") == command
                        ),
                    )
                    state = trace[-1]
                    assert any(
                        item["type"] in ("transform.frame", "stream.snapshot") and item["seq"] == state["seq"]
                        for item in trace
                    )
                    if command == "step":
                        assert state["simTime"] == pytest.approx(samples[0]["simTime"] + 0.1)
                    if command == "reset":
                        snapshot = (await http.get(route + "/snapshot")).json()
                        assert snapshot["simTime"] == 0 and snapshot["epoch"] == 1
                        assert snapshot["poses"][0][3] == pytest.approx(1.5)
                        joint = next(joint for joint in manifest["joints"] if joint["type"] == "revolute")
                        queried = await http.get(route + "/joints/state", params={"key": joint["entityKey"]})
                        assert queried.json()["positions"] == pytest.approx([-0.7853981634], abs=1e-6)
                    samples.append(
                        {
                            "command": command,
                            "seq": state["seq"],
                            "simTime": state["simTime"],
                            "epoch": state["epoch"],
                        }
                    )
                assert (await http.delete(route)).status_code == 204
            await eventually(lambda: server.active_sessions == 0)
            assert pid not in [child.pid for child in mp.active_children()]
            assert (await http.get("/api/v1/health")).status_code == 200
            record_property(
                "actual_newton_remote",
                json.dumps(
                    {
                        "device": "cpu",
                        "runnerPID": pid,
                        "transport": "actual loopback TLS",
                        "gateway": "actual HTTP/WebSocket",
                        "commands": samples,
                        "childReaped": True,
                        "sampleTimeClock": "gateway-receive",
                    }
                ),
            )

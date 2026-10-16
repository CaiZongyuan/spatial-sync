"""Actual authenticated TLS for interest barriers, full captures and Runner ages."""

import asyncio
import multiprocessing as mp
from contextlib import asynccontextmanager

import pytest
from test_remote_runner import tls_material as tls_material

from apps.api.app.sources.remote_source import RemoteQueryTimeout, RemoteSource
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.protocol.schema import Command, SessionOptions
from packages.transport import FrameCodec
from packages.transport import remote as remote_wire
from packages.transport.remote import (
    FRAME,
    JSON,
    InterestResult,
    Limits,
    RemoteConfig,
    ServerConfig,
    SnapshotResult,
    json_bytes,
    parse_json,
)
from packages.transport.remote_server import RunnerServer


async def wait_frame(source, after, ids):
    async with asyncio.timeout(2):
        while True:
            frame = source.latest_frame()
            if frame.seq > after and {row[0] for row in frame.poses} == ids:
                return frame
            await asyncio.sleep(0.005)


@asynccontextmanager
async def tls_source(tls_material, *, factory=None, host_limits=None, client_limits=None):
    cert, key, token = tls_material
    host_limits = host_limits or Limits(heartbeat_seconds=0.03, read_seconds=0.8, close_seconds=0.2)
    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=host_limits
        ), source_factory=factory,
    ) as server:
        source = RemoteSource(
            "remote-controlled",
            RemoteConfig(
                port=server.port, ca_file=cert, token=token, source_kind="synthetic",
                limits=client_limits or host_limits,
            ),
        )
        try:
            await source.start("lab-demo", SessionOptions())
            yield server, source
        finally:
            await source.close()


async def test_actual_synthetic_tls_interest_full_snapshots_expansion_reset_and_recovery(
    tls_material, record_property
):
    cert, key, token = tls_material
    limits = Limits(heartbeat_seconds=0.03, read_seconds=0.6, request_seconds=1, close_seconds=0.2)
    async with RunnerServer(
        ServerConfig(
            cert_file=cert, key_file=key, token=token, port=0, source_kind="synthetic", limits=limits
        )
    ) as server:
        source = RemoteSource(
            "remote-interest",
            RemoteConfig(port=server.port, ca_file=cert, token=token, source_kind="synthetic", limits=limits),
        )
        try:
            manifest = await source.start("stress-100", SessionOptions())
            ids = {node.streamId for node in manifest.nodes if node.kind == "body"}
            assert len(ids) == 100 and ids == {100 + 3 * index for index in range(100)}
            first, second = sorted(ids)[:2]
            barrier = await source.set_interest(frozenset([first]))
            assert barrier.type == "stream.snapshot" and {row[0] for row in barrier.poses} == ids
            narrowed = await wait_frame(source, barrier.seq, {first})
            full = await source.snapshot()
            assert full.type == "stream.snapshot" and {row[0] for row in full.poses} == ids
            assert full.seq > narrowed.seq and full.simTime >= narrowed.simTime
            await wait_frame(source, full.seq, {first})
            empty = await source.set_interest(frozenset())
            assert {row[0] for row in empty.poses} == ids
            await wait_frame(source, empty.seq, set())
            expanded = await source.set_interest(frozenset([first, second]))
            assert expanded.seq > full.seq and {row[0] for row in expanded.poses} == ids
            await wait_frame(source, expanded.seq, {first, second})
            paused = await source.execute(Command(requestId="pause", command="pause"))
            assert paused.state.state == "PAUSED"
            assert paused.frame.seq == paused.state.seq
            assert {row[0] for row in paused.frame.poses} == ids
            snapshot = await source.snapshot()
            assert (snapshot.seq, snapshot.epoch, snapshot.simTime) == (
                paused.frame.seq, paused.frame.epoch, paused.frame.simTime
            )
            assert snapshot.poses == paused.frame.poses
            reset = await source.execute(Command(requestId="reset", command="reset"))
            assert reset.frame.type == "stream.snapshot" and reset.frame.epoch == paused.frame.epoch + 1
            assert reset.frame.simTime == 0 and reset.frame.seq > paused.frame.seq
            assert {row[0] for row in reset.frame.poses} == ids
            resumed = await source.execute(Command(requestId="resume", command="resume"))
            await wait_frame(source, resumed.frame.seq, {first, second})
            restored = await source.set_interest(None)
            await wait_frame(source, restored.seq, ids)
            assert source.stats()["remoteConnected"] is True
            assert source.stats().get("runnerHeartbeatSeq") is None
            assert source.stats().get("runnerHeartbeatMs") is None
            record_property("source", "actual SyntheticSource stress-100 over authenticated TLS")
            record_property("interestBarrierSeq", barrier.seq)
            record_property("firstNarrowedSeq", narrowed.seq)
            record_property("fullCaptureSeq", full.seq)
            record_property("scopeCounts", "100 -> 1 -> full100 -> 0 -> 2 -> full100")
        finally:
            await source.close()
        assert source.stats()["remoteTasksAlive"] == 0
    assert server.active_sessions == 0


@pytest.mark.parametrize("deadline", ["client", "host"])
async def test_snapshot_deadline_and_late_full_capture_keep_narrowed_tls_live(
    tls_material, deadline, record_property
):
    class DelayedSnapshot(SyntheticSource):
        def __init__(self, session_id):
            super().__init__(session_id)
            self.late = None
            self.captures = set()

        async def snapshot(self):
            async def complete():
                await asyncio.sleep(0.12)
                frame = await super(DelayedSnapshot, self).snapshot()
                self.late = frame
                # A true late full snapshot stays observable by the host's pulse.
                await asyncio.sleep(0.07)
                self.late = None
                return frame

            task = asyncio.create_task(complete())
            self.captures.add(task)
            task.add_done_callback(self.captures.discard)
            return await asyncio.shield(task)

        def latest_frame(self):
            return self.late or super().latest_frame()

        async def close(self):
            for task in self.captures:
                task.cancel()
            await asyncio.gather(*self.captures, return_exceptions=True)
            await super().close()

    host_limits = Limits(
        request_seconds=0.05 if deadline == "host" else 0.5,
        heartbeat_seconds=0.02, read_seconds=0.8, close_seconds=0.2,
    )
    client_limits = Limits(
        request_seconds=0.05 if deadline == "client" else 0.5,
        heartbeat_seconds=0.02, read_seconds=0.8, close_seconds=0.2,
    )
    async with tls_source(
        tls_material, factory=DelayedSnapshot, host_limits=host_limits, client_limits=client_limits
    ) as (_, source):
        barrier = await source.set_interest(frozenset([1]))
        before = await wait_frame(source, barrier.seq, {1})
        with pytest.raises(RemoteQueryTimeout):
            await source.snapshot()
        # Wait past both the capture and its temporary full latest sample.
        await asyncio.sleep(0.22)
        advanced = await wait_frame(source, before.seq, {1})
        assert advanced.seq > before.seq and source.stats()["remoteConnected"] is True
        paused = await source.execute(Command(requestId="pause-after-query-timeout", command="pause"))
        assert paused.ack.status == "applied" and len(paused.frame.poses) == 24
        assert paused.state.state == "PAUSED"
        record_property("source", "controlled DelayedSnapshot(SyntheticSource) over authenticated TLS")
        record_property("deadline", deadline)
        record_property("beforeSeq", before.seq)
        record_property("afterSeq", advanced.seq)
        record_property("pauseSeq", paused.frame.seq)


@pytest.mark.parametrize("deadline", ["client", "host"])
async def test_interest_unknown_outcome_timeout_fails_and_cleans_up_tls(tls_material, deadline):
    closed = asyncio.Event()

    class DelayedInterest(SyntheticSource):
        async def set_interest(self, stream_ids):
            frame = await super().set_interest(stream_ids)
            await asyncio.sleep(0.15)  # The mutation has happened before the timeout.
            return frame

        async def close(self):
            await super().close()
            closed.set()

    host_limits = Limits(
        request_seconds=0.04 if deadline == "host" else 0.5,
        heartbeat_seconds=0.02, read_seconds=0.8, close_seconds=0.2,
    )
    client_limits = Limits(
        request_seconds=0.04 if deadline == "client" else 0.5,
        heartbeat_seconds=0.02, read_seconds=0.8, close_seconds=0.2,
    )
    async with tls_source(
        tls_material, factory=DelayedInterest, host_limits=host_limits, client_limits=client_limits
    ) as (server, source):
        with pytest.raises((TimeoutError, RuntimeError)):
            await source.set_interest(frozenset([1]))
        assert source.stats()["remoteConnected"] is False
        async with asyncio.timeout(2):
            async for event in source.events():
                if event.type == "server.error":
                    assert event.code == "source_unavailable"
                    break
            else:
                pytest.fail("Mutating timeout did not emit source_unavailable")
        await asyncio.wait_for(closed.wait(), 2)
        async with asyncio.timeout(2):
            while server.active_sessions or source.stats()["remoteTasksAlive"]:
                await asyncio.sleep(0.01)


async def test_tls_runner_heartbeat_age_advances_across_proxy_pause_and_loss(tls_material):
    producers = []

    class ControlledHeartbeat(SyntheticSource):
        def __init__(self, session_id):
            super().__init__(session_id)
            self.heartbeat_seq, self.heartbeat_age = 7, 200.0
            producers.append(self)

        def stats(self):
            # A controlled producer marker tests proxy forwarding, not Newton production.
            return {
                "runnerHeartbeatSeq": self.heartbeat_seq,
                "runnerHeartbeatMs": self.heartbeat_age,
                "runnerAlive": True,
            }

    limits = Limits(heartbeat_seconds=0.2, read_seconds=0.8, close_seconds=0.2)
    async with tls_source(tls_material, factory=ControlledHeartbeat, host_limits=limits) as (server, source):
        paused = await source.execute(Command(requestId="pause-age", command="pause"))
        assert paused.state.state == "PAUSED"
        await asyncio.sleep(0.03)
        initial = source.stats()["runnerHeartbeatMs"]
        assert initial >= 200
        await asyncio.sleep(0.08)  # Elapsed receipt age without a new host heartbeat.
        assert source.stats()["runnerHeartbeatMs"] >= initial + 60
        await asyncio.sleep(0.23)  # Same producer seq across multiple proxy heartbeats.
        assert source.stats()["runnerHeartbeatMs"] >= initial + 280
        assert source.stats()["runnerHeartbeatAlive"] is True
        producers[0].heartbeat_seq, producers[0].heartbeat_age = 8, 10.0
        async with asyncio.timeout(1):
            while source.stats()["runnerHeartbeatSeq"] != 8:
                await asyncio.sleep(0.005)
        assert source.stats()["runnerHeartbeatMs"] < 120
        before_loss = source.stats()["runnerHeartbeatMs"]
        await server.close()
        async with asyncio.timeout(1):
            while source.stats()["remoteConnected"]:
                await asyncio.sleep(0.005)
        assert source.stats()["runnerHeartbeatAlive"] is False
        assert source.stats()["runnerHeartbeatMs"] >= before_loss


async def test_new_periodic_before_interest_ack_and_stale_after_ack_keep_barrier(tls_material, monkeypatch):
    metadata, release, early, stale_sent = (asyncio.Event() for _ in range(4))
    original_send = remote_wire.send
    codec = old = None
    early_seq = None
    inject_stale = False

    async def controlled_send(writer, kind, payload, limits):
        nonlocal early_seq
        if kind == JSON and isinstance(parse_json(payload), InterestResult):
            metadata.set()
            await release.wait()
        if kind == FRAME and codec is not None:
            frame = codec.decode(payload)
            if len(frame.poses) == 1:
                if inject_stale and not stale_sent.is_set():
                    await original_send(writer, FRAME, codec.encode(old), limits)
                    stale_sent.set()
                await original_send(writer, kind, payload, limits)
                if not release.is_set():
                    early_seq = frame.seq
                    early.set()
                return
        await original_send(writer, kind, payload, limits)

    monkeypatch.setattr(remote_wire, "send", controlled_send)
    async with tls_source(tls_material) as (_, source):
        manifest = source.manifest
        codec = FrameCodec(source.session_id, manifest.sceneRevision, manifest.revisionNumericId)
        old = source.latest_frame().model_copy(update={"type": "transform.frame"})
        changing = asyncio.create_task(source.set_interest(frozenset([1])))
        try:
            await asyncio.wait_for(metadata.wait(), 1)
            await asyncio.wait_for(early.wait(), 1)
            await asyncio.sleep(0.02)
            # The real frame channel has sent the new scope while control remains gated.
            assert len(source.latest_frame().poses) == 24
            assert source.stats()["remoteConnected"] is True
            release.set()
            barrier = await asyncio.wait_for(changing, 1)
            assert len(barrier.poses) == 24 and barrier.seq < early_seq
            inject_stale = True
            await asyncio.wait_for(stale_sent.wait(), 1)
            current = await wait_frame(source, barrier.seq, {1})
            assert current.seq > barrier.seq and source.stats()["remoteConnected"] is True
        finally:
            release.set()
            await asyncio.gather(changing, return_exceptions=True)


@pytest.mark.parametrize("malformed", ["unknown-id", "missing-ids", "wrong-scope"])
async def test_transition_drops_legal_sets_but_rejects_malformed_periodic_membership(
    tls_material, monkeypatch, malformed
):
    release, bad_sent = asyncio.Event(), asyncio.Event()
    original_send = remote_wire.send
    codec = None

    async def controlled_send(writer, kind, payload, limits):
        if kind == JSON and isinstance(parse_json(payload), InterestResult):
            await release.wait()
        if kind == FRAME and codec is not None and not bad_sent.is_set():
            frame = codec.decode(payload)
            if len(frame.poses) == 1:
                row = frame.poses[0]
                poses = [] if malformed == "missing-ids" else [
                    ((9999 if malformed == "unknown-id" else 2), *row[1:])
                ]
                payload = codec.encode(frame.model_copy(update={"poses": poses}))
                await original_send(writer, kind, payload, limits)
                bad_sent.set()
                return
        await original_send(writer, kind, payload, limits)

    monkeypatch.setattr(remote_wire, "send", controlled_send)
    async with tls_source(tls_material) as (_, source):
        manifest = source.manifest
        codec = FrameCodec(source.session_id, manifest.sceneRevision, manifest.revisionNumericId)
        changing = asyncio.create_task(source.set_interest(frozenset([1])))
        try:
            await asyncio.wait_for(bad_sent.wait(), 1)
            # Independently bounded below the 0.8 s channel read timeout.
            async with asyncio.timeout(0.15):
                while source.stats()["remoteConnected"]:
                    await asyncio.sleep(0.005)
            with pytest.raises(RuntimeError):
                await changing
        finally:
            release.set()
            await asyncio.gather(changing, return_exceptions=True)


@pytest.mark.parametrize(
    "malformed", ["wrong-echo", "partial-full", "wrong-capture-kind", "wrong-result-kind"]
)
async def test_tls_full_capture_metadata_and_membership_are_strict(tls_material, monkeypatch, malformed):
    original_send = remote_wire.send
    codec = capture_writer = None

    async def controlled_send(writer, kind, payload, limits):
        nonlocal capture_writer
        if kind == JSON:
            packet = parse_json(payload)
            if isinstance(packet, (InterestResult, SnapshotResult)):
                capture_writer = writer
                if malformed == "wrong-echo":
                    payload = json_bytes(packet.model_copy(update={"streamIds": [2]}))
                elif malformed == "wrong-result-kind":
                    payload = json_bytes(SnapshotResult(token=packet.token))
        elif kind == FRAME and writer is capture_writer:
            frame = codec.decode(payload)
            if malformed == "partial-full":
                frame = frame.model_copy(update={"poses": frame.poses[:1]})
            elif malformed == "wrong-capture-kind":
                frame = frame.model_copy(update={"type": "transform.frame"})
            payload = codec.encode(frame)
        await original_send(writer, kind, payload, limits)

    monkeypatch.setattr(remote_wire, "send", controlled_send)
    async with tls_source(tls_material) as (_, source):
        manifest = source.manifest
        codec = FrameCodec(source.session_id, manifest.sceneRevision, manifest.revisionNumericId)
        with pytest.raises(RuntimeError):
            if malformed == "wrong-capture-kind":
                await source.snapshot()
            else:
                await source.set_interest(frozenset([1]))
        assert source.stats()["remoteConnected"] is False


@pytest.mark.newton
async def test_optional_actual_newton_cpu_tls_narrow_readback_full_recovery_and_heartbeat(
    tls_material, record_property
):
    pytest.importorskip("newton", reason="optional real Newton CPU TLS acceptance")
    cert, key, token = tls_material
    limits = Limits(heartbeat_seconds=0.05, read_seconds=3, request_seconds=5, close_seconds=0.5)
    async with RunnerServer(
        ServerConfig(cert_file=cert, key_file=key, token=token, port=0, limits=limits)
    ) as server:
        source = RemoteSource(
            "remote-newton-interest", RemoteConfig(port=server.port, ca_file=cert, token=token, limits=limits)
        )
        try:
            manifest = await source.start("falling-boxes", SessionOptions(device="cpu"))
            ids = {node.streamId for node in manifest.nodes if node.kind == "body"}
            selected = max(ids)
            assert len(ids) > 1 and manifest.source.kind == "newton"
            barrier = await source.set_interest(frozenset([selected]))
            assert barrier.type == "stream.snapshot" and {row[0] for row in barrier.poses} == ids
            await wait_frame(source, barrier.seq, {selected})
            async with asyncio.timeout(3):
                while source.stats().get("readbackBodyCount") != 1:
                    await asyncio.sleep(0.01)
            stats = source.stats()
            assert stats["gatherBodyCount"] == 1 and stats["readbackBytes"] == 28
            assert manifest.source.shapeCount == stats["shapeCount"]
            assert stats["runnerPIDIsRemote"] is True and stats["runnerPID"] > 0
            full = await source.snapshot()
            assert {row[0] for row in full.poses} == ids and full.seq > barrier.seq
            await wait_frame(source, full.seq, {selected})
            expanded = await source.set_interest(frozenset(ids))
            assert {row[0] for row in expanded.poses} == ids
            await wait_frame(source, expanded.seq, ids)
            paused = await source.execute(Command(requestId="pause-cpu", command="pause"))
            assert paused.ack.status == "applied" and {row[0] for row in paused.frame.poses} == ids
            snapshot = await source.snapshot()
            assert (snapshot.seq, snapshot.simTime) == (paused.frame.seq, paused.frame.simTime)
            heartbeat = source.stats()["runnerHeartbeatSeq"]
            async with asyncio.timeout(3):
                while source.stats()["runnerHeartbeatSeq"] == heartbeat:
                    await asyncio.sleep(0.01)
            assert source.stats()["runnerHeartbeatAlive"] is True
            assert 0 <= source.stats()["runnerHeartbeatMs"] < 1500
            restored = await source.set_interest(None)
            assert restored.seq == paused.frame.seq and {row[0] for row in restored.poses} == ids
            reset = await source.execute(Command(requestId="reset-cpu", command="reset"))
            assert reset.frame.epoch == paused.frame.epoch + 1 and reset.frame.simTime == 0
            assert reset.frame.poses[0][3] == pytest.approx(1.5)
            assert {row[0] for row in reset.frame.poses} == ids
            record_property("source", "actual spawned Newton CPU over authenticated TLS")
            record_property("gatherBodyCount", stats["gatherBodyCount"])
            record_property("readbackBytes", stats["readbackBytes"])
            record_property("runnerPID", stats["runnerPID"])
        finally:
            await source.close()
        assert source.stats()["remoteTasksAlive"] == 0
        assert source.stats()["runnerHeartbeatAlive"] is False
        assert stats["runnerPID"] not in {child.pid for child in mp.active_children()}
    assert server.active_sessions == 0

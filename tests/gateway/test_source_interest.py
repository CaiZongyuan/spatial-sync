"""Gateway interest union and complete recovery samples cross the public HTTP boundary."""

import asyncio
import json
import math

import httpx
import pytest

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.api.app.stream_broadcaster import ClientConnection
from packages.protocol.schema import Command, SessionOptions, Subscribe


class RecordingSource(SyntheticSource):
    def __init__(self, session_id):
        super().__init__(session_id)
        self.interests = []
        self.snapshot_calls = 0
        self.execute_calls = 0

    async def snapshot(self):
        self.snapshot_calls += 1
        return await super().snapshot()

    async def execute(self, command):
        self.execute_calls += 1
        return await super().execute(command)

    async def set_interest(self, ids):
        frame = await super().set_interest(ids)
        self.interests.append(ids)
        return frame


def drain(client):
    messages = []
    while not client.reliable.empty():
        messages.append(json.loads(client.reliable.get_nowait().payload))
    return messages


async def wait_partial(source, ids, after):
    async with asyncio.timeout(2):
        while source.latest_frame().seq <= after or {row[0] for row in source.latest_frame().poses} != ids:
            await asyncio.sleep(0.01)


async def test_observer_union_complete_http_freshness_and_honest_failed_recovery():
    app = create_app(Settings(), lambda kind, sid: RecordingSource(sid))
    async with app.router.lifespan_context(app):
        session = await app.state.manager.create("lab-demo", "synthetic", SessionOptions())
        source = session.source
        first, second = (ClientConnection(None, session.settings) for _ in range(2))
        session.clients.update((first, second))
        revision = session.manifest.sceneRevision
        assert source.interests == [frozenset()]

        async def select(client, key, request_id):
            await session.observer_subscribe(
                client,
                Subscribe(sceneRevision=revision, scope="entities", entityKeys=[key], requestId=request_id),
            )

        await select(first, "/World/Lab/Robot/Link1", "first")
        baseline = next(message for message in drain(first) if message["type"] == "stream.snapshot")
        assert source.interests[-1] == frozenset([1])
        await wait_partial(source, {1}, baseline["seq"])
        history = list(source.interests)
        await select(first, "/missing", "rejected")
        assert drain(first)[-1]["code"] == "invalid_subscription"
        assert source.interests == history and first.subscription_id == 1
        before = source.latest_frame()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
            route = f"/api/v1/sessions/{session.session_id}"
            full = (await http.get(route + "/snapshot")).json()
            assert len(full["poses"]) == 24 and full["seq"] > before.seq
            assert full["simTime"] >= before.simTime
            excluded = next(row for row in full["poses"] if row[0] == 31)
            assert excluded[3] == pytest.approx(0.5 + 0.15 * math.sin(full["simTime"]), abs=1e-6)
            pose = (await http.get(route + "/pose", params={"key": "/World/Lab/FallbackBox"})).json()
            assert (pose["seq"], pose["simTime"]) == (full["seq"], full["simTime"])
            assert pose["p"][2] == pytest.approx(0.5 + 0.15 * math.sin(pose["simTime"]), abs=1e-6)
        await wait_partial(source, {1}, pose["seq"])

        drain(first)
        drain(second)
        await select(second, "/World/Lab/FallbackBox", "second")
        added = drain(second)
        existing = drain(first)[-1]
        assert source.interests[-1] == frozenset([1, 31])
        assert [message["type"] for message in added] == [
            "subscription.ack", "stream.snapshot", "entity.state",
        ]
        assert {row[0] for row in added[1]["poses"]} == {31}
        assert existing["type"] == "transform.frame" and existing["seq"] == added[1]["seq"]
        assert {row[0] for row in existing["poses"]} == {1}
        await session.remove_client(second)
        assert source.interests[-1] == frozenset([1])
        session.clients.add(second)
        await session.observer_subscribe(second, Subscribe(sceneRevision=revision))
        assert source.interests[-1] is None
        await session.remove_client(second)
        assert source.interests[-1] == frozenset([1])
        await session.execute(first, Command(requestId="pause", command="pause"))
        paused = await session.snapshot()
        await session.execute(first, Command(requestId="resume", command="resume"))
        complete = session._complete_frame
        await wait_partial(source, {1}, complete.seq)
        assert source.latest_frame().seq > complete.seq > paused.seq
        session.fail("owned failure probe")
        recovered = await session.snapshot()
        assert (recovered.seq, recovered.simTime, recovered.poses) == (
            complete.seq, complete.simTime, complete.poses,
        )
        assert len(recovered.poses) == 24 and recovered.seq < session.seq
        reconnect = ClientConnection(None, session.settings)
        session.clients.add(reconnect)
        history = list(source.interests)
        await select(reconnect, "/World/Lab/FallbackBox", "failed-reconnect")
        messages = drain(reconnect)
        assert messages[0]["type"] == "subscription.ack"
        assert (messages[1]["seq"], messages[1]["simTime"]) == (complete.seq, complete.simTime)
        assert {row[0] for row in messages[1]["poses"]} == {31}
        assert messages[-1]["code"] == "source_unavailable" and session.state == "FAILED"
        assert source.interests == history
        assert session.stats()["counts"]["shapes"] is None
        assert "runnerHeartbeatSeq" not in session.stats()


async def test_readonly_snapshot_deadline_returns_504_without_poisoning_session_or_subscription():
    class SlowSnapshot(RecordingSource):
        slow = False

        async def snapshot(self):
            if self.slow:
                raise TimeoutError("controlled readonly deadline")
            return await super().snapshot()

    app = create_app(Settings(), lambda kind, sid: SlowSnapshot(sid))
    async with app.router.lifespan_context(app):
        session = await app.state.manager.create("lab-demo", "synthetic", SessionOptions())
        client = ClientConnection(None, session.settings)
        session.clients.add(client)
        request = Subscribe(sceneRevision=session.manifest.sceneRevision, requestId="first")
        await session.observer_subscribe(client, request)
        drain(client)
        history = list(session.source.interests)
        session.source.slow = True
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
            response = await http.get(f"/api/v1/sessions/{session.session_id}/snapshot")
        assert response.status_code == 504 and response.json()["detail"]["code"] == "query_timeout"
        await session.observer_subscribe(client, request.model_copy(update={"requestId": "retry"}))
        error = drain(client)[-1]
        assert error["code"] == "session_not_ready" and error["requestId"] == "retry"
        assert session.state == "RUNNING" and client.subscription_id == 1
        assert session.source.interests == history
        session.source.slow = False
        await session.execute(client, Command(requestId="still-live", command="pause"))
        assert session.state == "PAUSED" and len((await session.snapshot()).poses) == 24


async def test_invalid_initial_interest_barrier_cannot_replace_a_valid_revision_or_complete_cache():
    class BadBarrier(RecordingSource):
        async def set_interest(self, ids):
            frame = await super().set_interest(ids)
            return frame.model_copy(update={"poses": []})

    sources = []

    def factory(kind, sid):
        source = RecordingSource(sid) if not sources else BadBarrier(sid)
        sources.append(source)
        return source

    app = create_app(Settings(), factory)
    async with app.router.lifespan_context(app):
        session = await app.state.manager.create("lab-demo", "synthetic", SessionOptions())
        retained = await session.snapshot()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
            response = await http.post(f"/api/v1/sessions/{session.session_id}/reload")
        assert response.status_code == 503 and session.state == "FAILED"
        assert session.manifest.sceneRevision == retained.sceneRevision
        recovered = await session.snapshot()
        assert (recovered.sceneRevision, recovered.seq, recovered.simTime, recovered.poses) == (
            retained.sceneRevision, retained.seq, retained.simTime, retained.poses,
        )
        assert session.source is None and sources[1]._state == "STOPPED"


async def test_running_narrow_pose_reads_complete_cache_without_any_source_control_and_age_increases():
    app = create_app(Settings(), lambda kind, sid: RecordingSource(sid))
    async with app.router.lifespan_context(app):
        session = await app.state.manager.create("lab-demo", "synthetic", SessionOptions())
        source = session.source
        client = ClientConnection(None, session.settings)
        session.clients.add(client)
        await session.observer_subscribe(
            client,
            Subscribe(
                sceneRevision=session.manifest.sceneRevision, scope="entities",
                entityKeys=["/World/Lab/Robot/Link1"], requestId="narrow",
            ),
        )
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
            route = f"/api/v1/sessions/{session.session_id}"
            full = (await http.get(route + "/snapshot")).json()
            await wait_partial(source, {1}, full["seq"])
            calls = (source.snapshot_calls, source.execute_calls, list(source.interests))
            expected = next(row for row in full["poses"] if row[0] == 31)
            first = (await http.get(route + "/pose", params={"key": "/World/Lab/FallbackBox"})).json()
            assert (source.snapshot_calls, source.execute_calls, source.interests) == calls
            await asyncio.sleep(0.03)
            second = (await http.get(route + "/pose", params={"key": "/World/Lab/FallbackBox"})).json()
            assert second["staleMs"] > first["staleMs"]
            for pose in (first, second):
                assert (pose["seq"], pose["simTime"], pose["epoch"], pose["sceneRevision"]) == (
                    full["seq"], full["simTime"], full["epoch"], full["sceneRevision"],
                )
                assert pose["p"] == pytest.approx(expected[1:4], abs=1e-6)
                assert pose["q"] == pytest.approx(expected[4:8], abs=1e-6)
                assert pose["seq"] < source.latest_frame().seq
            missing = await http.get(route + "/pose", params={"key": "/missing"})
            assert missing.status_code == 404 and missing.json()["detail"]["code"] == "unknown_entity"
            session._complete_frame = None
            unavailable = await http.get(route + "/pose", params={"key": "/World/Lab/FallbackBox"})
            assert unavailable.status_code == 503
            assert unavailable.json()["detail"]["code"] == "session_not_ready"
            assert (source.snapshot_calls, source.execute_calls, source.interests) == calls
        assert session.state == "RUNNING"

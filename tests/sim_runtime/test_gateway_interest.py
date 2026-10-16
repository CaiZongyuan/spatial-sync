"""Real Newton producers behind Session/HTTP must gather the accepted observer union."""

import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

wp = pytest.importorskip("warp")
pytest.importorskip("newton")
pytestmark = pytest.mark.newton

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.stream_broadcaster import ClientConnection
from packages.protocol.schema import Command, SessionOptions, Subscribe


def messages(client):
    result = []
    while not client.reliable.empty():
        result.append(json.loads(client.reliable.get_nowait().payload))
    return result


async def gathered(source, expected, after):
    async with asyncio.timeout(4):
        while (
            source.latest_frame().seq <= after
            or {row[0] for row in source.latest_frame().poses} != expected
            or source.stats()["readbackBodyCount"] != len(expected)
        ):
            await asyncio.sleep(0.02)
    assert source.stats()["gatherBodyCount"] == len(expected)
    assert source.stats()["readbackBytes"] == len(expected) * 28


@pytest.mark.parametrize("device", ["cpu", pytest.param("cuda:0", marks=pytest.mark.gpu)])
async def test_real_gateway_union_http_expansion_viewport_reset_and_failed_reconnect(device, monkeypatch):
    wp.init()
    if device.startswith("cuda") and not wp.is_cuda_available():
        pytest.skip("CUDA unavailable")
    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        session = await app.state.manager.create("falling-boxes", "newton", SessionOptions(device=device))
        source = session.source
        bodies = [node for node in session.manifest.nodes if node.kind == "body"]
        all_ids = {node.streamId for node in bodies}
        first, second = (ClientConnection(None, session.settings) for _ in range(2))
        session.clients.update((first, second))
        await gathered(source, set(), session.seq)

        async def select(client, body, request_id):
            await session.observer_subscribe(
                client,
                Subscribe(
                    sceneRevision=session.manifest.sceneRevision, scope="entities",
                    entityKeys=[body.entityKey], requestId=request_id,
                ),
            )
            return next(value for value in messages(client) if value["type"] == "stream.snapshot")

        selected = await select(first, bodies[0], "one")
        await gathered(source, {bodies[0].streamId}, selected["seq"])
        captures = AsyncMock(wraps=source.snapshot)
        interests = AsyncMock(wraps=source.set_interest)
        commands = AsyncMock(wraps=source.execute)
        monkeypatch.setattr(source, "snapshot", captures)
        monkeypatch.setattr(source, "set_interest", interests)
        monkeypatch.setattr(source, "execute", commands)
        before = source.latest_frame()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as http:
            route = f"/api/v1/sessions/{session.session_id}"
            response = await http.get(route + "/snapshot")
            assert response.status_code == 200
            full = response.json()
            assert {row[0] for row in full["poses"]} == all_ids
            assert full["seq"] > before.seq and full["simTime"] >= before.simTime
            await gathered(source, {bodies[0].streamId}, full["seq"])
            calls = (captures.await_count, interests.await_count, commands.await_count)
            pose = (await http.get(route + "/pose", params={"key": bodies[1].entityKey})).json()
            expected = next(row for row in full["poses"] if row[0] == bodies[1].streamId)
            assert (pose["seq"], pose["simTime"], pose["epoch"], pose["sceneRevision"]) == (
                full["seq"], full["simTime"], full["epoch"], full["sceneRevision"],
            )
            assert pose["p"] == pytest.approx(expected[1:4], abs=1e-6)
            assert pose["q"] == pytest.approx(expected[4:8], abs=1e-6)
            await asyncio.sleep(0.03)
            aged = (await http.get(route + "/pose", params={"key": bodies[1].entityKey})).json()
            assert aged["seq"] == full["seq"] and aged["staleMs"] > pose["staleMs"]
            assert (captures.await_count, interests.await_count, commands.await_count) == calls
            stats = (await http.get(route + "/stats")).json()
            assert stats["counts"]["shapes"] == session.manifest.source.shapeCount
            assert stats["counts"]["shapes"] > len(bodies)
            assert stats["readbackBodyCount"] == 1 and stats["readbackBytes"] == 28
        await gathered(source, {bodies[0].streamId}, pose["seq"])
        messages(first)
        expanded = await select(second, bodies[1], "two")
        existing = messages(first)[-1]
        assert existing["type"] == "transform.frame" and existing["seq"] == expanded["seq"]
        assert {row[0] for row in existing["poses"]} == {bodies[0].streamId}
        await gathered(source, all_ids, expanded["seq"])
        await session.remove_client(second)
        await gathered(source, {bodies[0].streamId}, session.seq)
        # A viewport with no visible bodies still gathers every moving entrant.
        await session.observer_subscribe(
            first,
            Subscribe(
                sceneRevision=session.manifest.sceneRevision, scope="viewport", requestId="viewport",
                viewport={"planes": [[1, 0, 0, -100], [-1, 0, 0, 101], [0, 1, 0, 1],
                                     [0, -1, 0, 1], [0, 0, 1, 1], [0, 0, -1, 1]]},
            ),
        )
        viewport = next(value for value in messages(first) if value["type"] == "stream.snapshot")
        assert viewport["poses"] == []
        await gathered(source, all_ids, viewport["seq"])
        narrowed = await select(first, bodies[0], "one-again")
        await gathered(source, {bodies[0].streamId}, narrowed["seq"])
        await session.execute(first, Command(requestId="pause", command="pause"))
        await session.execute(first, Command(requestId="reset", command="reset"))
        complete = await session.snapshot()
        assert complete.epoch == 1 and complete.simTime == 0
        assert {row[0] for row in complete.poses} == all_ids
        heartbeat = source.stats()["runnerHeartbeatSeq"]
        async with asyncio.timeout(3):
            while source.stats()["runnerHeartbeatSeq"] == heartbeat:
                await asyncio.sleep(0.02)
        assert source.latest_frame().seq == complete.seq
        assert source.stats()["runnerHeartbeatAlive"] is True
        source._process.terminate()  # only this test's owned Runner
        async with asyncio.timeout(3):
            while session.state != "FAILED":
                await asyncio.sleep(0.02)
        assert source.stats()["runnerHeartbeatAlive"] is False
        retained = await session.snapshot()
        assert (retained.seq, retained.simTime, retained.poses) == (
            complete.seq, complete.simTime, complete.poses,
        )
        reconnect = ClientConnection(None, session.settings)
        session.clients.add(reconnect)
        recovered = await select(reconnect, bodies[1], "failed-reconnect")
        assert recovered["seq"] == complete.seq
        assert {row[0] for row in recovered["poses"]} == {bodies[1].streamId}

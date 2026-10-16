"""A real source can advance its frame channel while an older control reply is delayed."""

import asyncio
import json
import time

import httpx
import pytest

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.api.app.stream_broadcaster import ClientConnection
from packages.protocol.schema import Command, SessionOptions, Subscribe


class DelayedControlSource(SyntheticSource):
    def __init__(self, session_id):
        super().__init__(session_id)
        self.control_waiting = asyncio.Event()
        self.release_control = asyncio.Event()

    async def execute(self, command):
        result = await super().execute(command)
        if command.requestId == "delayed-control":
            self.control_waiting.set()
            await self.release_control.wait()
        return result


@pytest.mark.parametrize("reader", ["http-snapshot", "subscription"])
async def test_reload_sequence_exceeds_frames_observed_before_delayed_control_reply(reader):
    sources = []

    def factory(kind, session_id):
        source = DelayedControlSource(session_id)
        sources.append(source)
        return source

    app = create_app(Settings(heartbeat_interval_seconds=100), factory)
    async with app.router.lifespan_context(app):
        # Valid 1 Hz publishing gives a wide scheduling window after the monitor
        # skipped its tick due to the command lock. Source physics remains live.
        session = await app.state.manager.create("lab-demo", "synthetic", SessionOptions(basePublishHz=1))
        initial = session.latest_frame()
        client = ClientConnection(None, Settings())
        session.clients.add(client)
        command_task = asyncio.create_task(
            session.execute(
                client,
                Command(requestId="delayed-control", command="set_realtime_factor", args={"value": 1.0}),
            )
        )
        tasks = [command_task]
        source = sources[0]
        try:
            await asyncio.wait_for(source.control_waiting.wait(), timeout=1)
            deadline = time.monotonic() + 3
            while source.latest_frame().seq < initial.seq + 2:
                assert time.monotonic() < deadline, "real source did not publish the advanced frame"
                await asyncio.sleep(0.005)
            observed = None
            if reader == "http-snapshot":
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://test"
                ) as http:
                    response = await http.get(f"/api/v1/sessions/{session.session_id}/snapshot")
                assert response.status_code == 200
                observed = response.json()
            else:
                subscribe_task = asyncio.create_task(
                    session.observer_subscribe(
                        client, Subscribe(sceneRevision=session.manifest.sceneRevision)
                    )
                )
                tasks.append(subscribe_task)
                await asyncio.sleep(0)  # enqueue this public subscription before reload on the same lock
            reload_task = asyncio.create_task(session.reload())
            tasks.append(reload_task)
            await asyncio.sleep(0)  # reload now waits behind the delayed control/subscription
            source.release_control.set()
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
            delivered = []
            while not client.reliable.empty():
                delivery = client.reliable.get_nowait()
                if isinstance(delivery.payload, str):
                    delivered.append(json.loads(delivery.payload))
            if observed is None:
                observed = next(message for message in delivered if message["type"] == "stream.snapshot")
            old_state = next(
                message
                for message in delivered
                if message["type"] == "session.state"
                and message["cause"].get("requestId") == "delayed-control"
            )
            assert observed["seq"] >= initial.seq + 2 and old_state["seq"] == initial.seq
            assert old_state["simTime"] == initial.simTime  # preserve the actual old control sample
            replacement = session.latest_frame()
            assert replacement.sceneRevision != observed["sceneRevision"]
            assert replacement.seq > observed["seq"], (reader, observed["seq"], replacement.seq)
        finally:
            source.release_control.set()
            await asyncio.gather(*tasks, return_exceptions=True)

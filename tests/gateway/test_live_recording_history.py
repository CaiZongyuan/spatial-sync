"""Real Live Host, disk log and public HTTP consumers without sockets or Sources."""

import asyncio
import json
import threading
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from apps.api.app.device_host import DeviceHost
from apps.api.app.history_routes import install_history_routes
from apps.api.app.info_routes import install_info_routes
from apps.api.app.live_recordings import LiveRecordingRepository
from apps.api.app.settings import Settings
from packages.info.approvals import ApprovalStore
from packages.info.live_log import LiveLog
from packages.info.log import read_segment
from packages.info.plans import PlanStore
from packages.live.bindings import load_bindings
from packages.live.ingress import BrokerIngress

KEY = "/Real/Incubator2"


class Clock:
    raw = 500.0
    utc = datetime(2026, 10, 21, 12, tzinfo=UTC)

    def advance(self, seconds):
        self.raw += seconds
        self.utc += timedelta(seconds=seconds)


class LocalTransport:
    """Only the actual ingress queue; no broker/socket/thread is started."""

    def __init__(self, clock):
        self.ingress = BrokerIngress(clock=lambda: clock.raw)

    def start(self):
        self.ingress.connected()

    def publish(self, topic, payload):
        return True

    def close(self):
        self.ingress.close()


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr("packages.info.program.time", SimpleNamespace(monotonic=lambda: clock.raw))
    monkeypatch.setattr("apps.api.app.live_recordings.LiveLog", partial(LiveLog, utc_clock=lambda: clock.utc))
    settings = Settings(recordings_root=tmp_path / "recordings", state_root=tmp_path / "state")
    repository = LiveRecordingRepository(settings, None)
    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    plans = PlanStore(tmp_path / "plans.jsonl", approvals)
    hosts = []

    async def make_host():
        bindings = load_bindings(Path(__file__).parents[2] / "devices/live/lab.yaml")
        host = DeviceHost(bindings, approvals, plans, recordings=repository, clock=lambda: clock.raw,
                          transport_factory=lambda *_args: LocalTransport(clock))
        hosts.append(host)
        await host.start()
        host.advance()
        return host

    try:
        yield SimpleNamespace(clock=clock, repository=repository, make_host=make_host)
    finally:
        for host in hosts:
            if host.state != "STOPPED":
                await host.close()
        await plans.close()


def api(host, repository):
    app = FastAPI()
    manager = SimpleNamespace(sessions={}, live=host)

    async def target():
        return host

    install_info_routes(app, manager, lambda _headers: "operator",
                        prefix="/api/v1/live", resolve_target=target)
    install_history_routes(app, manager, repository)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local")


def telemetry(host, temperature):
    config = host.catalog.program_config(KEY)
    assert host.transport.ingress.message(config.telemetry.topic, json.dumps({
        "temp": temperature, "co2": 5, "humidity": 60, "doorOpen": False, "setpoint": 37,
    }).encode())
    host.advance()


async def test_actual_public_state_and_history_share_continuous_clock_without_mutating_host(setup):
    first = await setup.make_host()
    setup.clock.advance(.1)
    telemetry(first, 37)
    assert first.info.entities[KEY].values["temperatureC"] == 37
    old_id = first.recording.metadata["sessionId"]
    await first.close()
    setup.clock.advance(120)
    setup.clock.raw = 1  # Reboot-style raw origin, real UTC downtime.
    second = await setup.make_host()
    assert second.recording.metadata["sessionId"] == old_id
    assert second.sample_time_ms() == pytest.approx(620_100)
    setup.clock.advance(.1)
    telemetry(second, 38)
    assert second.host.metadata()[KEY]["temperatureC"]["sampleTimeMs"] == pytest.approx(1100)

    assert second.info.entities[KEY].meta["temperatureC"].sampleTimeMs == pytest.approx(620_200)
    assert second.mailboxes[KEY].online(second.now())
    async with api(second, setup.repository) as http:
        state = await http.get("/api/v1/live/state", params={"keys": KEY})
        assert state.status_code == 200, state.text
        assert state.json()["entities"][KEY]["meta"]["temperatureC"]["sampleTimeMs"] == pytest.approx(620_200)
        query = {"key": KEY, "property": "temperatureC", "since": "1m"}
        history = await http.get("/api/v1/history", params=query)
        assert history.status_code == 200, history.text
        assert history.json() == [[620_200, 38]]
        assert history.headers["x-history-recording"] == old_id
        all_rows = await http.get("/api/v1/history", params=query | {"recording": old_id, "since": "0"})
        assert all_rows.json() == [[500_100, 37], [620_200, 38]]
        setup.clock.advance(7200)
        quiet = await http.get("/api/v1/history", params=query | {"since": "1h"})
        assert quiet.status_code == 200, quiet.text
        assert quiet.json() == []
        explicit = await http.get("/api/v1/history", params=query | {"until": "620200"})
        assert explicit.json() == [[620_200, 38]]

    setup.clock.advance(86400)
    second.advance()
    metadata = await second.recording.checkpoint()
    path = second.recording.directory / metadata["segments"][-1]["file"]
    prefix = read_segment(path.read_bytes()).records[0]
    entity = next(value for value in json.loads(prefix.payload)["entities"] if value["key"] == KEY)
    assert entity["meta"]["temperatureC"]["sampleTimeMs"] == pytest.approx(620_200)
    assert second.host.metadata()[KEY]["temperatureC"]["sampleTimeMs"] == pytest.approx(1100)


async def test_cancelled_http_history_keeps_pin_until_actual_disk_read_finishes(setup, monkeypatch):
    from apps.api.app import history_routes

    host = await setup.make_host()
    setup.clock.advance(.1)
    telemetry(host, 37)
    metadata = await host.recording.checkpoint()
    first_path = host.recording.directory / metadata["segments"][0]["file"]
    original = history_routes.read_samples
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    actual_rows = []

    def blocked_read(*args):
        entered.set()
        assert release.wait(5)
        try:
            rows = original(*args)
            actual_rows.extend((row.t_ms, row.value) for row in rows)
            return rows
        finally:
            finished.set()

    monkeypatch.setattr(history_routes, "read_samples", blocked_read)
    async with api(host, setup.repository) as http:
        reading = asyncio.create_task(http.get("/api/v1/history", params={
            "key": KEY, "property": "temperatureC", "since": "0", "until": "500100",
        }))
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            reading.cancel()
            await asyncio.sleep(0)
            reading.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reading
            assert host.recording._readers == 1
            setup.clock.advance(10 * 86400)
            host.advance()
            await host.recording.checkpoint()
            assert first_path.is_file()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
        def unpinned():
            with host.recording._condition:
                return host.recording._condition.wait_for(lambda: host.recording._readers == 0, 5)
        assert await asyncio.to_thread(unpinned)
        assert actual_rows == [(500_100, 37)]
        assert host.recording._readers == 0
        setup.clock.advance(60)
        host.advance()
        await host.recording.checkpoint()
        assert not first_path.exists()


@pytest.mark.parametrize("close_failure", [None, "metadata", "flush"])
async def test_history_admitted_during_close_reads_sealed_prefix_without_waiting_for_its_own_lease(
    tmp_path, monkeypatch, close_failure,
):
    clock = Clock()
    log = LiveLog(tmp_path / "recordings/live-closing", "live-closing", "closing-lab",
                  state_directory=tmp_path / "state/live-closing",
                  utc_clock=lambda: clock.utc, clock=lambda: clock.raw)
    await log.ready()
    initial = {
        "type": "entity.snapshot", "sceneRevision": "live-closing-revision", "epoch": 0,
        "sampleTimeMs": 500_000, "simTime": 0, "invocations": [],
        "entities": [{"key": KEY, "rev": 1, "values": {"temperatureC": 37}, "meta": {
            "temperatureC": {"sampleTimeMs": 500_000, "simTime": 0, "source": "device"},
        }}],
    }
    log.add_revision({"sceneRevision": initial["sceneRevision"]}, json.dumps(initial))
    await log.checkpoint()
    if close_failure == "metadata":
        original_save, attempts = log._save_state, []
        def failed_save():
            attempts.append(True)
            if len(attempts) == 2:
                raise OSError("Injected final metadata failure")
            original_save()
        monkeypatch.setattr(log, "_save_state", failed_save)
    elif close_failure == "flush":
        def failed_flush():
            raise OSError("Injected final flush failure")
        monkeypatch.setattr(log, "_flush", failed_flush)
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(5)

    reading = asyncio.create_task(log.read_pinned(blocked))
    assert await asyncio.to_thread(entered.wait, 5)
    closing = asyncio.create_task(log.close())
    await asyncio.sleep(0)
    target = SimpleNamespace(recording=log, session_id="live-closing-target", state="STOPPING")
    app = FastAPI()
    install_history_routes(app, SimpleNamespace(sessions={}, live=target), None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as http:
        history = asyncio.create_task(http.get("/api/v1/history", params={
            "recording": "live-closing", "key": KEY, "property": "temperatureC", "since": "0",
        }))
        await asyncio.sleep(0)
        release.set()
        try:
            response = await asyncio.wait_for(history, 2)
            if close_failure:
                assert response.status_code == 503, response.text
                assert response.json()["detail"]["code"] == "history_unavailable"
            else:
                assert response.status_code == 200, response.text
                assert response.json() == [[500_000, 37]]
        finally:
            release.set()
            await reading
            await asyncio.wait_for(closing, 2)
        after_close = await http.get("/api/v1/history", params={
            "recording": "live-closing", "key": KEY, "property": "temperatureC", "since": "0",
        })
        assert after_close.status_code == 503

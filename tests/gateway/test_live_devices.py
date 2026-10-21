"""Actual Live Host/HTTP/approval integration with an injected in-process broker."""

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from lw.client import GatewayClient
from lw.mcp_server import create_server
from mcp import Client, Implementation

from apps.api.app.approval_routes import install_approval_routes
from apps.api.app.device_host import DeviceHost
from apps.api.app.info_routes import install_info_routes
from apps.api.app.main import create_app
from apps.api.app.plan_routes import install_plan_routes
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.info.approvals import ApprovalStore
from packages.info.plans import PlanStore
from packages.live.bindings import load_bindings
from packages.live.ingress import BrokerIngress, BrokerReceipt

KEY = "/Real/Incubator2"
EXAMPLE = Path(__file__).parents[2] / "devices/live/lab.yaml"


class FakeBrokerTransport:
    def __init__(self, broker, topics, clock):
        self.ingress = BrokerIngress(clock=clock)
        self.topics = tuple(topics)
        self.publications = []
        self.closed = False

    def start(self):
        self.ingress.connected()

    def publish(self, topic, payload):
        if self.closed:
            return False
        self.publications.append((topic, deepcopy(payload)))
        return True

    def emit(self, topic, payload, **kwargs):
        return self.ingress.message(topic, json.dumps(payload).encode(), **kwargs)

    def close(self):
        self.closed = True
        self.ingress.close()


@pytest.fixture
async def live(tmp_path):
    clock = [10.0]
    transports = []

    def factory(broker, topics):
        transport = FakeBrokerTransport(broker, topics, lambda: clock[0])
        transports.append(transport)
        return transport

    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    plans = PlanStore(tmp_path / "plans.jsonl", approvals)
    host = DeviceHost(load_bindings(EXAMPLE), approvals, plans, transport_factory=factory,
                      clock=lambda: clock[0])
    manager = SimpleNamespace(sessions={}, live=host, approvals=approvals, plans=plans)

    def missing_session(_identity):
        raise HTTPException(404, {"code": "unknown_session"})

    manager.get = missing_session
    app = FastAPI()

    @app.get("/api/v1/health")
    async def health():
        return {"sessions": [], "live": {"enabled": True}}

    async def target():
        return host

    def role(headers):
        return "viewer" if headers.get("authorization") == "viewer" else "operator"
    install_info_routes(app, manager, role)
    install_info_routes(app, manager, role, prefix="/api/v1/live", resolve_target=target)
    install_approval_routes(app, manager, role)
    install_plan_routes(app, manager, role)
    await host.start()
    host.advance()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://live") as http:
        try:
            yield SimpleNamespace(host=host, http=http, approvals=approvals, clock=clock,
                                  transports=transports, manager=manager)
        finally:
            await host.close()
            await plans.close()
    assert all(transport.closed for transport in transports)
    assert host._task is None


def telemetry(live, *, temperature=37):
    live.transports[-1].emit("lab/incubator2/telemetry", {
        "temp": temperature, "co2": 5, "humidity": 60, "doorOpen": False, "setpoint": 37,
    })
    live.clock[0] += .1
    live.host.advance()


def action(identity="request", key=KEY, action="setSetpoint", params=None):
    return {"requestId": identity, "entityKey": key, "action": action,
            "params": params or {"temperatureC": 38}}


async def test_public_http_and_generic_client_require_human_before_wire_dispatch(live):
    telemetry(live)
    assert live.manager.sessions == {}
    assert len((await live.http.get("/api/v1/live/entities")).json()["entities"]) == 3
    async with GatewayClient(http=live.http) as client:
        assert (await client.describe_entity(KEY))["key"] == KEY
        assert (await client.check_condition(KEY, "temperatureC", "equals", 37))["satisfied"]
        invoked = await client.invoke_action(KEY, "setSetpoint", {"temperatureC": 38},
                                             wait=False, actor="agent:test")
        assert invoked["status"] == "pending_approval"
        live.clock[0] += .1
        live.host.advance()
        assert live.transports[-1].publications == []
        approval = next(iter(live.approvals.records.values()))
        path = f"/api/v1/approvals/{approval.approvalId}/decision"
        denied = await live.http.post(path, json={"decision": "approve"}, headers={"X-Actor": "agent:test"})
        assert denied.status_code == 403 and not live.transports[-1].publications
        approved = await live.http.post(path, json={"decision": "approve"}, headers={"X-Actor": "user:test"})
        assert approved.status_code == 200, approved.json()
        assert approved.json()["status"] == "approved"
        assert not live.transports[-1].publications
        live.clock[0] += .1
        live.host.advance()
        [(topic, command)] = live.transports[-1].publications
        assert topic == "lab/incubator2/cmd" and command["requestId"] == invoked["invocationId"]
        broker = live.transports[-1]
        for topic, retained in [("wrong-ack", False), ("lab/incubator2/ack", True)]:
            broker.emit(topic, {"requestId": command["requestId"], "status": "succeeded"}, retained=retained)
        live.clock[0] += .1
        live.host.advance()
        assert (await client.get_invocation(invoked["invocationId"]))["status"] == "running"
        broker.emit("lab/incubator2/ack", {"requestId": command["requestId"], "status": "succeeded"})
        live.clock[0] += .1
        live.host.advance()
        assert (await client.get_invocation(invoked["invocationId"]))["status"] == "succeeded"


async def test_invalid_json_and_individual_silence_do_not_refresh_or_dispatch(live):
    broker = live.transports[-1]
    broker.ingress.message("lab/incubator2/telemetry", b"not-json")
    broker.emit("lab/incubator2/telemetry", {"temp": "invalid"})
    live.clock[0] += .1
    live.host.advance()
    assert live.host.info.values()[KEY] == {}
    offline = await live.http.post("/api/v1/live/actions", json=action())
    assert offline.status_code == 503 and offline.json()["error"]["code"] == "device_unavailable"
    telemetry(live)
    live.clock[0] += 3.1
    broker.emit("lab/environment1/telemetry", {"temp": 22, "humidity": 40})
    live.host.advance()
    assert live.host.info.metadata()[KEY]["temperatureC"].stale
    assert not live.host.info.metadata()["/Real/Environment1"]["temperatureC"].stale
    response = await live.http.post("/api/v1/live/actions", json=action("silence"))
    assert response.json()["error"]["code"] == "device_unavailable"
    assert not broker.publications


async def test_cancel_before_tick_and_reconnect_cannot_replay_owned_commands(live):
    broker = live.transports[-1]
    broker.emit("lab/biosafety1/telemetry", {"inflow": .5, "sashHeight": 0,
                                           "uvOn": False, "blowerOn": False})
    live.clock[0] += .1
    live.host.advance()
    value = (await live.http.post("/api/v1/live/actions", json=action(
        key="/Real/BiosafetyCabinet1", action="setBlower", params={"on": True},
    ))).json()
    assert value["status"] == "accepted"
    await live.http.post(f"/api/v1/live/invocations/{value['invocationId']}/cancel")
    live.clock[0] += .1
    live.host.advance()
    assert broker.publications == []
    telemetry(live)
    value = (await live.http.post("/api/v1/live/actions", json=action("pending-reconnect"))).json()
    broker.ingress.disconnected()
    live.clock[0] += .1
    live.host.advance()
    assert live.host.pipeline.table.get(value["invocationId"]).error.code == "device_unavailable"
    await live.host._connect()
    assert broker.closed and len(live.transports) == 2
    assert not broker.emit("lab/incubator2/ack", {"requestId": value["invocationId"], "status": "succeeded"})
    replacement = live.transports[-1]
    replacement.emit("lab/incubator2/telemetry", {"temp": 37}, retained=True)
    live.clock[0] += .1
    live.host.advance()
    assert live.host.availability(KEY).code == "device_unavailable"
    assert replacement.publications == []
    telemetry(live)
    assert live.host.availability(KEY) is None
    assert replacement.publications == []


async def test_callback_thread_does_not_mutate_host_and_queue_pressure_fails_closed(live):
    broker = live.transports[-1]
    thread = Thread(target=broker.emit, args=("lab/environment1/telemetry", {"temp": 22, "humidity": 40}))
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert live.host.info.values()["/Real/Environment1"] == {}
    live.clock[0] += .1
    live.host.advance()
    assert live.host.info.values()["/Real/Environment1"]["temperatureC"] == 22
    for _ in range(1025):
        broker.emit("lab/environment1/telemetry", {"temp": 23, "humidity": 40})
    live.clock[0] += .1
    live.host.advance()
    assert live.host.availability("/Real/Environment1").code == "device_unavailable"
    assert live.host.info.values()["/Real/Environment1"]["temperatureC"] == 22


async def test_viewer_and_unsupported_live_plan_cannot_enter_transport(live):
    telemetry(live)
    response = await live.http.post("/api/v1/live/actions", json=action(),
                                    headers={"Authorization": "viewer"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "not_permitted"
    response = await live.http.post("/api/v1/live/actions",
                                    json=action("planned") | {"planId": "x", "stepId": 1})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "live_plan_unavailable"
    assert not live.transports[-1].publications and not live.approvals.records


async def test_real_gateway_lifespan_owns_live_without_creating_any_spatial_source(tmp_path):
    transports, sources = [], []

    def factory(broker, topics):
        import time

        transport = FakeBrokerTransport(broker, topics, time.monotonic)
        transports.append(transport)
        return transport

    def forbidden_source(*args):
        sources.append(args)
        raise AssertionError("A live-only gateway must not create a spatial Source")

    class Sampler:
        def sample(self, rows):
            return platform_snapshot(rows, unavailable_system("Test sampler"))

    app = create_app(Settings(live_bindings=EXAMPLE, state_root=tmp_path / "state",
                               recordings_root=tmp_path / "recordings"),
                     source_factory=forbidden_source, live_transport_factory=factory,
                     platform_sampler=Sampler())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://live") as http:
            health = (await http.get("/api/v1/health")).json()
            assert health["sessionCount"] == 0 and health["live"] == {"enabled": True, "state": "RUNNING"}
            assert len((await http.get("/api/v1/live/entities")).json()["entities"]) == 3
            assert (await http.get("/api/v1/live")).json()["layout"] is None
            assert not sources
    assert len(transports) == 1 and transports[0].closed
    assert app.state.manager.live.state == "STOPPED"


async def test_official_mcp_client_keeps_live_action_generic_and_agent_attributed(live):
    telemetry(live)
    async with GatewayClient(http=live.http) as gateway:
        server = await create_server(gateway)
        async with Client(server, client_info=Implementation(name="live-agent", version="1")) as client:
            described = await client.call_tool("describe_entity", {"key": KEY})
            assert not described.is_error
            assert json.loads(described.content[0].text)["key"] == KEY
            invoked = await client.call_tool("invoke_action", {"key": KEY, "action": "setSetpoint",
                                             "params": {"temperatureC": 38}, "wait": False})
            assert not invoked.is_error
            value = json.loads(invoked.content[0].text)
            assert value["status"] == "pending_approval"
            assert value["actor"] == {"kind": "agent", "name": "live-agent"}
            live.clock[0] += .1
            live.host.advance()
            assert not live.transports[-1].publications
            assert "approve_invocation" not in {tool.name for tool in (await client.list_tools()).tools}


async def test_fresh_client_resolves_live_invocation_and_shutdown_closes_approval(live):
    telemetry(live)
    response = await live.http.post("/api/v1/live/actions", json=action("restart-client"))
    invocation = response.json()
    async with GatewayClient(http=live.http, session="unrelated-session") as client:
        assert (await client.get_invocation(invocation["invocationId"]))["status"] == "pending_approval"
        assert (await client.get_state([KEY]))["entities"][KEY]["values"]["temperatureC"] == 37
    await live.host.close()
    approval = live.approvals.for_invocation(invocation["invocationId"])
    assert approval.status == "unavailable"
    assert live.host.pipeline.table.get(invocation["invocationId"]).status == "failed"
    assert live.transports[-1].closed and not live.transports[-1].publications


async def test_actual_host_command_timeout_and_publish_failure_are_audited(live):
    broker = live.transports[-1]
    broker.emit("lab/biosafety1/telemetry", {"inflow": .5, "sashHeight": 0,
                                           "uvOn": False, "blowerOn": False})
    live.clock[0] += .1
    live.host.advance()
    response = await live.http.post("/api/v1/live/actions", json=action(
        "timeout", key="/Real/BiosafetyCabinet1", action="setBlower", params={"on": True},
    ))
    value = response.json()
    live.clock[0] += .1
    live.host.advance()
    assert len(broker.publications) == 1
    live.clock[0] += 5.1
    # Keep the individual device fresh so the exact command deadline wins.
    broker.emit("lab/biosafety1/telemetry", {"inflow": .5, "sashHeight": 0,
                                           "uvOn": False, "blowerOn": False})
    live.host.advance()
    timed = live.host.pipeline.table.get(value["invocationId"])
    assert timed.status == "failed" and timed.error.code == "timeout"
    response = await live.http.post("/api/v1/live/actions", json=action(
        "publish-failure", key="/Real/BiosafetyCabinet1", action="setBlower", params={"on": False},
    ))
    failed = response.json()
    broker.closed = True
    live.clock[0] += .1
    live.host.advance()
    assert live.host.pipeline.table.get(failed["invocationId"]).error.code == "device_unavailable"
    assert len(broker.publications) == 1


async def test_failed_transport_close_retains_the_owner_and_can_retry(live):
    transport = live.transports[-1]
    original_close = transport.close
    calls = []

    def flaky_close():
        calls.append("close")
        if len(calls) == 1:
            raise OSError("Controlled close failure leaves the driver alive")
        original_close()

    transport.close = flaky_close
    try:
        with pytest.raises(OSError):
            await live.host.close()
        assert live.host.transport is transport and live.host.source is live.host
        assert live.host.state == "FAILED" and not transport.closed
        await live.host.close()
        assert calls == ["close", "close"]
        assert transport.closed and live.host.transport is None
        assert live.host.state == "STOPPED" and live.host.source is None
    finally:
        transport.close = original_close
        if live.host.transport is not transport and not transport.closed:
            original_close()  # release only this test's fake if the failing candidate lost its owner


async def test_reconnect_does_not_replace_an_owner_that_still_fails_close(live):
    transport = live.transports[-1]
    calls = []

    def failed_close():
        calls.append("close")
        raise OSError("Driver has not released its worker")

    original_close = transport.close
    transport.close = failed_close
    try:
        for _ in range(2):
            with pytest.raises(OSError):
                await live.host._connect()
        assert live.host.transport is transport and calls == ["close", "close"]
        assert len(live.transports) == 1
    finally:
        transport.close = original_close
        if live.host.transport is not transport and not transport.closed:
            original_close()


async def test_initial_handshake_overflow_reconnects_and_requires_new_valid_telemetry(tmp_path):
    clock, transports = [10.0], []

    def factory(broker, topics):
        transport = FakeBrokerTransport(broker, topics, lambda: clock[0])
        if not transports:
            transport.ingress = BrokerIngress(capacity=1, clock=lambda: clock[0])
        transports.append(transport)
        return transport

    approvals = ApprovalStore(tmp_path / "approvals.jsonl")
    plans = PlanStore(tmp_path / "plans.jsonl", approvals)
    host = DeviceHost(load_bindings(EXAMPLE), approvals, plans, transport_factory=factory,
                      clock=lambda: clock[0])
    try:
        await host.start()  # connected receipt occupies the first generation's only queue slot
        assert not transports[0].emit("lab/incubator2/telemetry", {"temp": 37})
        host.advance()
        assert host._reconnect and host.availability(KEY).code == "device_unavailable"
        clock[0] += 1.1
        await asyncio.sleep(.06)  # run the actual owned reconnect loop, without TCP or a Source
        assert len(transports) == 2 and transports[0].closed
        replacement = SimpleNamespace(host=host, clock=clock, transports=transports)
        host.advance()
        assert host.availability(KEY).code == "device_unavailable"
        transports[-1].emit("lab/incubator2/telemetry", {"temp": 37}, retained=True)
        clock[0] += .1
        host.advance()
        assert host.availability(KEY).code == "device_unavailable"
        telemetry(replacement)
        assert host.availability(KEY) is None
    finally:
        await host.close()
        await plans.close()


async def test_older_connection_generation_cannot_disconnect_current_fresh_device(live):
    broker = live.transports[-1]
    broker.ingress.connected()
    live.host.advance()
    telemetry(live)
    assert live.host._generation == 2 and live.host.availability(KEY) is None
    broker.ingress._queue.put_nowait(BrokerReceipt(1, live.clock[0], "disconnected"))
    live.host.advance()
    assert live.host.availability(KEY) is None and not live.host._reconnect


async def test_other_owned_cleanup_is_attempted_even_if_host_offline_cleanup_raises(live):
    telemetry(live)
    invocation = (await live.http.post("/api/v1/live/actions", json=action("close-error"))).json()
    original = live.host._offline

    def failed_offline():
        raise OSError("Controlled Host cleanup failure")

    live.host._offline = failed_offline
    try:
        with pytest.raises(OSError):
            await live.host.close()
        assert live.transports[-1].closed
        assert not live.host.pipeline._approval_timers
        assert live.approvals.for_invocation(invocation["invocationId"]).status == "unavailable"
        assert live.host.state == "FAILED" and live.host.source is live.host
    finally:
        live.host._offline = original
    await live.host.close()
    assert live.host.state == "STOPPED"


@pytest.mark.parametrize("close_target", ["simulation", "live"])
async def test_real_synthetic_session_and_live_have_independent_actions_and_lifecycles(
    tmp_path, close_target,
):
    import time

    transports = []

    def factory(broker, topics):
        transport = FakeBrokerTransport(broker, topics, time.monotonic)
        transports.append(transport)
        return transport

    class Sampler:
        def sample(self, rows):
            return platform_snapshot(rows, unavailable_system("Coexistence test sampler"))

    async def until(read):
        deadline = asyncio.get_running_loop().time() + 5
        while asyncio.get_running_loop().time() < deadline:
            value = await read()
            if value:
                return value
            await asyncio.sleep(.02)
        raise AssertionError("Public coexistence observation did not converge")

    app = create_app(Settings(live_bindings=EXAMPLE, state_root=tmp_path / "state",
                               recordings_root=tmp_path / "recordings", recordings_enabled=False),
                     live_transport_factory=factory, platform_sampler=Sampler())
    source = None
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://coexist") as http:
            created = await http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@3",
                                                               "source": "synthetic"})
            assert created.status_code == 201, created.text
            session_id = created.json()["sessionId"]
            source = app.state.manager.sessions[session_id].source
            assert isinstance(source, SyntheticSource)
            try:
                health = (await http.get("/api/v1/health")).json()
                assert health["sessionCount"] == 1 and health["live"]["state"] == "RUNNING"
                second = await http.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@1",
                                                                  "source": "synthetic"})
                assert second.status_code == 409 and second.json()["detail"]["code"] == "session_exists"
                transport = transports[0]
                transport.emit("lab/incubator2/telemetry", {"temp": 37, "co2": 5, "humidity": 60,
                                                           "doorOpen": False, "setpoint": 37})
                async with GatewayClient(http=http, session=session_id) as client:
                    async def live_fresh():
                        state = await client.get_state([KEY])
                        return state if state["entities"][KEY]["values"].get("temperatureC") == 37 else None

                    await until(live_fresh)
                    assert (await client.describe_entity(KEY))["key"] == KEY
                    value = await client.invoke_action(KEY, "setSetpoint", {"temperatureC": 38}, wait=False)
                    assert value["status"] == "pending_approval" and not transport.publications
                    approval = app.state.manager.approvals.for_invocation(value["invocationId"])
                    decided = await http.post(f"/api/v1/approvals/{approval.approvalId}/decision",
                                              json={"decision": "approve"}, headers={"X-Actor": "user:test"})
                    assert decided.status_code == 200

                    async def published():
                        return transport.publications

                    await until(published)
                    assert transport.publications[0][1]["requestId"] == value["invocationId"]
                    transport.emit("lab/incubator2/ack", {"requestId": value["invocationId"],
                                                         "status": "succeeded"})

                    async def completed():
                        current = await client.get_invocation(value["invocationId"])
                        return current if current["status"] == "succeeded" else None

                    await until(completed)
                    if close_target == "simulation":
                        assert (await http.delete("/api/v1/sessions/" + session_id)).status_code == 204
                        assert app.state.manager.live.state == "RUNNING" and not transport.closed
                        current = await client.get_state([KEY])
                        assert current["entities"][KEY]["values"]["temperatureC"] == 37
                    else:
                        before = (await http.get("/api/v1/sessions/" + session_id)).json()["simTime"]
                        await app.state.manager.live.close()

                        async def simulation_advanced():
                            state = (await http.get("/api/v1/sessions/" + session_id)).json()
                            advancing = state["state"] == "RUNNING" and state["simTime"] > before
                            return state if advancing else None

                        await until(simulation_advanced)
                        assert source._task is not None and not source._task.done() and transport.closed
            finally:
                assert (await http.delete("/api/v1/sessions/" + session_id)).status_code == 204
    assert source is not None and source._task.done()
    assert app.state.manager.live._task is None and transports[0].closed

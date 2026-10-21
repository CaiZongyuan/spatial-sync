"""Actual Live Host/HTTP/approval integration with an injected in-process broker."""

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
from packages.info.approvals import ApprovalStore
from packages.info.plans import PlanStore
from packages.live.bindings import load_bindings
from packages.live.ingress import BrokerIngress

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

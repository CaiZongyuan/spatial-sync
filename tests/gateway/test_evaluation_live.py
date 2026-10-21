"""The real evaluation lease owns spatial sessions while Live retains its own target."""

import asyncio
import time

import httpx
import pytest
from lw.client import GatewayClient, GatewayError
from test_live_devices import EXAMPLE, KEY, FakeBrokerTransport

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource


async def until(read, predicate):
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        value = await read()
        if predicate(value):
            return value
        await asyncio.sleep(.02)
    raise AssertionError("Evaluation/Live public observation did not converge")


async def test_evaluation_lease_keeps_live_client_approval_and_mqtt_independent(tmp_path):
    transports = []
    episode_entered = asyncio.Event()

    def factory(broker, topics):
        transport = FakeBrokerTransport(broker, topics, time.monotonic)
        transports.append(transport)
        return transport

    async def hold_episode(session, suite, case):
        # Real admission, Suite validation, Source startup and recording precede this seam.
        episode_entered.set()
        await asyncio.Event().wait()

    app = create_app(Settings(live_bindings=EXAMPLE, state_root=tmp_path / "state",
                               recordings_root=tmp_path / "recordings"), live_transport_factory=factory)
    app.state.manager.evaluations._episode = hold_episode
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                      base_url="http://evaluation-live") as http:
            submitted = await http.post("/api/v1/evaluations", json={
                "subject": "robot_arm.virtual", "suite": "suite/lab-demo-pick-place@1",
            })
            assert submitted.status_code == 202, submitted.text
            evaluation = submitted.json()["evaluationId"]
            try:
                await asyncio.wait_for(episode_entered.wait(), 5)
                health = (await http.get("/api/v1/health")).json()
                assert health["evaluation"]["evaluationId"] == evaluation
                [owned] = health["sessions"]
                assert owned["evaluationId"] == evaluation and owned["state"] == "RUNNING"
                session_id = owned["sessionId"]
                source = app.state.manager.sessions[session_id].source
                assert isinstance(source, SyntheticSource)
                broker = transports[0]
                broker.emit("lab/incubator2/telemetry", {
                    "temp": 37, "co2": 5, "humidity": 60, "doorOpen": False, "setpoint": 37,
                })
                async with GatewayClient(http=http, session=session_id) as client:
                    await until(lambda: client.get_state([KEY]),
                                lambda state: state["entities"][KEY]["values"].get("temperatureC") == 37)
                    with pytest.raises(GatewayError) as readonly:
                        await client.invoke_action("/World/Lab/RobotA", "home", {}, wait=False)
                    assert readonly.value.status_code == 409
                    assert readonly.value.detail["code"] == "evaluation_busy"
                    live = await client.invoke_action(KEY, "setSetpoint", {"temperatureC": 38},
                                                       wait=False, actor="agent:integration")
                    assert live["status"] == "pending_approval" and broker.publications == []
                    approval = app.state.manager.approvals.for_invocation(live["invocationId"])
                    decision = await http.post(f"/api/v1/approvals/{approval.approvalId}/decision",
                                               json={"decision": "approve"},
                                               headers={"X-Actor": "user:integration"})
                    assert decision.status_code == 200, decision.text
                    assert decision.json()["status"] == "approved"
                    await until(lambda: client.get_invocation(live["invocationId"]),
                                lambda value: value["status"] == "running")
                    [(topic, command)] = broker.publications
                    assert topic == "lab/incubator2/cmd" and command["requestId"] == live["invocationId"]
                    broker.emit("lab/incubator2/ack", {"requestId": command["requestId"],
                                                       "status": "succeeded"})
                    await until(lambda: client.get_invocation(live["invocationId"]),
                                lambda value: value["status"] == "succeeded")
                # MCP startup uses this public client discovery with no interactive simulation selected.
                async with GatewayClient(http=http) as discovery:
                    catalog = await discovery.catalog_if_active()
                    assert catalog["sceneRevision"] == app.state.manager.live.catalog.sceneRevision
                assert app.state.manager.sessions[session_id].source is source
                assert health["live"]["state"] == "RUNNING" and not broker.closed
            finally:
                await http.post(f"/api/v1/evaluations/{evaluation}/cancel")

                async def read_evaluation():
                    return (await http.get("/api/v1/evaluations/" + evaluation)).json()

                await until(read_evaluation, lambda value: value["status"] == "cancelled")
    assert app.state.manager.live._task is None and transports[0].closed

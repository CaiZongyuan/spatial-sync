"""The real evaluation lease owns spatial sessions while Live retains its own target."""

import asyncio
import time

import httpx
import pytest
import yaml
from lw.client import GatewayClient, GatewayError
from test_live_devices import EXAMPLE, KEY, FakeBrokerTransport

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from tests.fixtures.evaluation_alerts import prepare_suite, scene_archive


async def until(read, predicate):
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        value = await read()
        if predicate(value):
            return value
        await asyncio.sleep(.02)
    raise AssertionError("Evaluation/Live public observation did not converge")


async def test_evaluation_lease_keeps_live_client_approval_and_mqtt_independent(tmp_path, monkeypatch):
    transports = []
    episode_entered = asyncio.Event()
    monkeypatch.setenv("LW_LIBRARY", str(tmp_path / "library"))

    def factory(broker, topics):
        transport = FakeBrokerTransport(broker, topics, time.monotonic)
        transports.append(transport)
        return transport

    async def hold_episode(session, suite, case):
        # Real admission, Suite validation, Source startup and recording precede this seam.
        episode_entered.set()
        await asyncio.Event().wait()

    configuration = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    configuration["layout"] = "lab-demo@3"
    configuration["entities"][0]["placeAt"] = "/World/Lab/Incubator"
    configuration["entities"][0]["alerts"] = {"leaseHot": {
        "when": {"all": [{"property": "temperatureC", "gt": 40}]}, "forSeconds": .01,
        "severity": "critical", "message": "Combined consumer fixture",
    }}
    bindings = tmp_path / "live.yaml"
    bindings.write_text(yaml.safe_dump(configuration), encoding="utf-8")
    app = create_app(Settings(live_bindings=bindings, state_root=tmp_path / "state",
                               recordings_root=tmp_path / "recordings", recordings_keep=1),
                     live_transport_factory=factory)
    app.state.manager.evaluations._episode = hold_episode
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),
                                      base_url="http://evaluation-live") as http:
            imported = await http.post("/api/v1/library/imports", content=scene_archive(),
                                       headers={"Content-Type": "application/x-tar"})
            assert imported.status_code == 200, imported.text
            assert imported.json()["validation"]["launchable"]
            scene = app.state.manager.library.resolve(imported.json()["sceneVersion"], launch=True)
            suite = await asyncio.to_thread(prepare_suite, app.state.manager.library.roots["user"], scene)
            app.state.manager.evaluations.suites.refresh()
            submitted = await http.post("/api/v1/evaluations", json={
                "subject": "robot_arm.virtual", "suite": suite,
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

                async def simulation_alerts():
                    return (await http.get("/api/v1/alerts", params={
                        "sessionId": session_id, "active": "true",
                    })).json()["alerts"]

                [simulation_alert] = await until(simulation_alerts, lambda values: len(values) == 1)
                assert simulation_alert["readOnly"] is True
                assert simulation_alert["acknowledgement"] is None
                refused = await http.post(f"/api/v1/alerts/{simulation_alert['alertId']}/ack", json={})
                assert refused.status_code == 409 and refused.json()["detail"]["code"] == "evaluation_busy"
                assert app.state.manager.live.manifest is None
                selected = app.state.manager.library.resolve("lab-demo@3")
                references = await asyncio.to_thread(app.state.manager.library_deletions.inspection.scan,
                                                     selected, "scene")
                assert {"suite", "live"} <= {item["kind"] for item in references["references"]}
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
                    plan_response = await http.post("/api/v1/plans", json={
                        "title": "Live plan during evaluation", "target": "live", "steps": [{
                            "id": 1, "do": {"entity": KEY, "action": "setSetpoint",
                                               "params": {"temperatureC": 39}},
                        }],
                    })
                    assert plan_response.status_code == 201, plan_response.text
                    plan = plan_response.json()
                    assert plan["approvalMode"] == "human" and len(broker.publications) == 1
                    early = await client.invoke_action(KEY, "setSetpoint", {"temperatureC": 39}, wait=False,
                                                        planId=plan["id"], stepId=1,
                                                        planRevision=plan["revision"])
                    assert early["error"]["code"] == "plan_not_approved" and len(broker.publications) == 1
                    approved = await http.post(f"/api/v1/approvals/{plan['approvalId']}/decision",
                                               json={"decision": "approve"}, headers={"X-Actor": "user:plan"})
                    assert approved.status_code == 200, approved.text
                    exact = await client.invoke_action(KEY, "setSetpoint", {"temperatureC": 39}, wait=False,
                                                        planId=plan["id"], stepId=1,
                                                        planRevision=plan["revision"])
                    await until(lambda: client.get_invocation(exact["invocationId"]),
                                lambda value: value["status"] == "running")
                    assert len(broker.publications) == 2
                    assert broker.publications[-1][1]["params"] == {"temperatureC": 39}
                    broker.emit("lab/incubator2/ack", {"requestId": exact["invocationId"],
                                                       "status": "succeeded"})
                    await until(lambda: client.get_invocation(exact["invocationId"]),
                                lambda value: value["status"] == "succeeded")
                    broker.emit("lab/incubator2/telemetry", {
                        "temp": 50, "co2": 5, "humidity": 60, "doorOpen": False, "setpoint": 37,
                    })

                    async def live_alerts():
                        return (await http.get("/api/v1/alerts", params={
                            "sessionId": app.state.manager.live.session_id, "active": "true",
                        })).json()["alerts"]

                    [alert] = await until(live_alerts, lambda values: len(values) == 1)
                    assert alert["sessionId"] == app.state.manager.live.session_id
                    assert alert["readOnly"] is False
                    inbox = await client.inbox_poll()
                    assert any(item["alertId"] == alert["alertId"] for item in inbox["alerts"])
                    acknowledged = await http.post(f"/api/v1/alerts/{alert['alertId']}/ack",
                                                   json={"note": "Live remains independent"})
                    assert acknowledged.status_code == 200, acknowledged.text
                    assert acknowledged.json()["readOnly"] is False
                    history = await client.history_query(KEY, "temperatureC", "1h")
                    assert any(point[1] == 50 for point in history["points"])
                    live_log = app.state.manager.live.recording
                    assert history["recordingId"] == live_log.metadata["sessionId"]
                    metadata = await http.get(f"/recordings/{history['recordingId']}/recording.json")
                    assert metadata.status_code == 200 and metadata.json()["kind"] == "live"
                    assert metadata.json()["sceneVersion"] is None
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

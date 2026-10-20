"""Read-only planned motion through actual Synthetic programs and the HTTP boundary."""

import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from test_alerts import control

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.programs.robot_arm import virtual
from packages.programs.robot_arm.virtual import HOME, kinematics

ROBOT = "/World/Lab/RobotA"
CENTRIFUGE = "/World/Lab/Centrifuge"


def create(client):
    response = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2", "controlHz": 20})
    assert response.status_code == 201, response.text
    sid = response.json()["sessionId"]
    route = "/api/v1/sessions/" + sid
    control(client, route, "pause")
    control(client, route, "reset")
    return sid, route


def move(step=1, target=None):
    return {"id": step, "do": {"entity": ROBOT, "action": "moveJoints", "params": {
        "positions": target or [1, .5, -.5, .1, .2, .3, .4]}}}


def submit(client, *steps):
    response = client.post("/api/v1/plans", json={"title": "Inspect planned motion", "target": "session",
                                                 "steps": list(steps)})
    assert response.status_code == 201, response.text
    value = response.json()
    return value, "/api/v1/plans/" + value["id"]


def preview(client, route, step=1, **params):
    response = client.get(f"{route}/steps/{step}/preview", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_submitted_preview_returns_actual_world_body_poses_without_actions_or_grants():
    with TestClient(create_app()) as client:
        sid, session_route = create(client)
        high = {"id": 1, "do": {"entity": "/World/Lab/BiosafetyCabinet", "action": "setUv",
                                 "params": {"on": True}}}
        plan, route = submit(client, high, move(2))
        assert plan["status"] == "submitted"
        initial = client.get(session_route + "/state", params={"keys": ROBOT}).json()
        approvals = client.get("/api/v1/approvals").json()
        inbox = client.get("/api/v1/inbox").json()
        value = preview(client, route, 2)
        assert value["available"] is True and value["policyDriven"] is False
        assert value["sessionId"] == sid and value["epoch"] == plan["epoch"]
        assert value["revision"] == 1 and value["stepId"] == 2
        assert value["startJointPositions"] == HOME
        target = plan["steps"][1]["do"]["params"]["positions"]
        assert value["targetJointPositions"] == target
        assert 1 <= len(value["points"]) <= 64
        assert value["points"][0] == pytest.approx(initial["entities"][ROBOT]["values"]["tcpPose"][:3])
        assert value["points"][-1] == pytest.approx(kinematics(target)[2]["p"])
        scene = client.get(session_route + "/scene")
        assert scene.status_code == 200, scene.text
        manifest = scene.json()
        bodies = {node["entityKey"] for node in manifest["nodes"] if node["kind"] == "body"}
        assert set(value["targetBodyPoses"]) == {
            "/World/Lab/Robot/Link1", "/World/Lab/Robot/Link2", ROBOT + "/Hand"}
        assert set(value["targetBodyPoses"]) <= bodies
        for key, wanted in zip(("/World/Lab/Robot/Link1", "/World/Lab/Robot/Link2", ROBOT + "/Hand"),
                               kinematics(target), strict=True):
            assert value["targetBodyPoses"][key]["p"] == pytest.approx(wanted["p"])
            assert value["targetBodyPoses"][key]["q"] == pytest.approx(wanted["q"])
        assert client.get(session_route + "/state", params={"keys": ROBOT}).json() == initial
        assert client.get(route).json() == plan
        assert client.get("/api/v1/approvals").json() == approvals
        assert client.get("/api/v1/inbox", params={"after": inbox["cursor"]}).json()["messages"] == []
        assert client.get(session_route + "/invocations").json()["invocations"] == []


def test_preview_cache_uses_current_joints_revision_epoch_scene_and_source(monkeypatch):
    built = []
    original = virtual.path_preview

    def observe(*args, **kwargs):
        built.append(deepcopy(args[0]))
        return original(*args, **kwargs)

    monkeypatch.setattr(virtual, "path_preview", observe)
    with TestClient(create_app()) as client:
        sid, session_route = create(client)
        _, route = submit(client, move())
        first = preview(client, route)
        assert preview(client, route)["points"] == first["points"]
        assert len(built) == 1
        changed = [0, .4, -.5, 0, 0, 0, 0]
        revised = client.post(route + "/revisions", json={"title": "Changed goal", "target": "session",
                                                         "steps": [move(target=changed)]})
        assert revised.status_code == 201, revised.text
        assert preview(client, route)["targetJointPositions"] == changed
        assert len(built) == 2
        assert client.get(route + "/steps/1/preview", params={"revision": 1}).status_code == 409
        accepted = client.post(session_route + "/actions", json={
            "requestId": "change-current", "entityKey": ROBOT,
            "action": "moveJoints", "params": {"positions": changed}})
        assert accepted.status_code == 202
        control(client, session_route, "step", count=480)
        terminal = client.get(session_route + "/invocations/" + accepted.json()["invocationId"],
                              params={"wait": True, "timeoutSeconds": 2})
        assert terminal.json()["status"] == "succeeded", terminal.text
        current = preview(client, route)
        assert current["startJointPositions"] == changed and len(current["points"]) == 1
        assert len(built) == 3
        control(client, session_route, "reset")
        assert client.get(route + "/steps/1/preview").status_code == 409
        _, reset_route = submit(client, move())
        reset = preview(client, reset_route)
        assert reset["epoch"] > first["epoch"] and reset["startJointPositions"] == HOME
        old_source = client.app.state.manager.get(sid).source
        reloaded = client.post(session_route + "/reload")
        assert reloaded.status_code == 200, reloaded.text
        assert client.app.state.manager.get(sid).source is not old_source
        assert client.get(reset_route + "/steps/1/preview").status_code == 409
        _, new_route = submit(client, move())
        newest = preview(client, new_route)
        assert newest["sceneRevision"] != reset["sceneRevision"] and newest["startJointPositions"] == HOME
        assert len(built) == 5


def test_non_robot_and_non_action_steps_are_explicitly_unavailable():
    with TestClient(create_app()) as client:
        create(client)
        _, route = submit(client, {"id": 1, "do": {"entity": CENTRIFUGE, "action": "closeLid"}},
                          {"id": 2, "check": {"entity": CENTRIFUGE, "property": "lidOpen", "equals": False}})
        assert preview(client, route)["reason"] == "program_preview_unavailable"
        assert preview(client, route, 2)["reason"] == "step_has_no_action"
        assert client.get(route + "/steps/999/preview").status_code == 404
        assert client.get("/api/v1/plans/missing/steps/1/preview").status_code == 404


def test_preview_failure_has_no_write_permission_and_does_not_disable_program(monkeypatch):
    def forbidden(self, action, params, ctx):
        ctx.set(jointPositions=[99] * 7)

    monkeypatch.setattr(virtual.RobotArmProgram, "preview", forbidden)
    with TestClient(create_app()) as client:
        _, session_route = create(client)
        _, route = submit(client, move())
        before = client.get(session_route + "/state", params={"keys": ROBOT}).json()
        for _ in range(3):
            response = client.get(route + "/steps/1/preview")
            assert response.status_code == 503 and response.json()["detail"]["code"] == "preview_unavailable"
        assert client.get(session_route + "/state", params={"keys": ROBOT}).json() == before
        accepted = client.post(session_route + "/actions", json={
            "requestId": "still-operational", "entityKey": ROBOT, "action": "home"})
        assert accepted.status_code == 202, accepted.text


def test_invalid_taught_location_preview_does_not_reserve_an_invocation():
    with TestClient(create_app()) as client:
        _, session_route = create(client)
        _, route = submit(client, {"id": 1, "do": {"entity": ROBOT, "action": "moveTo",
                                                   "params": {"location": "not-a-location"}}})
        response = client.get(route + "/steps/1/preview")
        assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_preview"
        assert client.get(session_route + "/invocations").json()["invocations"] == []


async def test_cancelled_preview_releases_lifecycle_lock_and_rejects_wrong_source_receipt():
    entered, release = asyncio.Event(), asyncio.Event()

    class Held(SyntheticSource):
        async def preview(self, request):
            value = await super().preview(request)
            entered.set()
            await release.wait()
            return value.model_copy(update={"epoch": value.epoch + 10})

    app = create_app(Settings(), source_factory=lambda kind, sid: Held(sid))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            created = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()
            session_route = "/api/v1/sessions/" + created["sessionId"]
            plan = (await client.post("/api/v1/plans", json={"title": "Wait for preview", "target": "session",
                                                           "steps": [move()]})).json()
            route = "/api/v1/plans/" + plan["id"] + "/steps/1/preview"
            pending = asyncio.create_task(client.get(route))
            await asyncio.wait_for(entered.wait(), 2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            release.set()
            result = await asyncio.wait_for(client.get(route), 2)
            assert result.status_code == 409
            assert result.json()["detail"]["code"] == "invalid_plan_reference"
            assert (await client.delete(session_route)).status_code == 204

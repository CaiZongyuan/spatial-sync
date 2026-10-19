"""Completed condition history and diagnostic metadata survive public adapters."""

import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient
from test_info import CENTRIFUGE, create
from test_plans import plan

from apps.api.app.main import create_app


def condition_call(client, route, kind, plan_id, *, value=True):
    body = {
        "key": CENTRIFUGE,
        "property": "lidOpen",
        "op": "equals",
        "value": value,
        "planId": plan_id,
        "stepId": 1,
    }
    if kind == "check":
        return client.post(route + "/check", json=body)
    return client.get(
        route + "/wait", params={**body, "value": "true" if value else "false", "timeoutSeconds": 0.02}
    )


@pytest.mark.parametrize("kind", ["check", "wait"])
def test_completed_condition_cannot_be_replayed_to_remove_revision_protection(kind):
    with TestClient(create_app()) as client:
        _, route = create(client, realtimeFactor=10)
        content = plan(
            {"id": 1, kind: {"entity": CENTRIFUGE, "property": "lidOpen", "equals": True}},
            {"id": 2, "do": {"entity": CENTRIFUGE, "action": "closeLid"}},
        )
        submitted = client.post("/api/v1/plans", json=content).json()
        first = condition_call(client, route, kind, submitted["id"])
        assert first.status_code == 200 and first.json()["satisfied"], first.text
        before = client.get("/api/v1/plans/" + submitted["id"]).json()
        assert (
            client.post(
                route + "/actions",
                json={
                    "requestId": "close-after-completion",
                    "entityKey": CENTRIFUGE,
                    "action": "closeLid",
                    "wait": True,
                },
            ).json()["status"]
            == "succeeded"
        )
        replay = condition_call(client, route, kind, submitted["id"])
        assert replay.status_code == 409, replay.text
        after = client.get("/api/v1/plans/" + submitted["id"]).json()
        assert after["steps"][0]["status"] == "done"
        assert after["stepResults"]["1"] == before["stepResults"]["1"]
        modified = deepcopy(content)
        modified["steps"][0][kind]["equals"] = False
        for changed in (modified, plan(content["steps"][1])):
            response = client.post("/api/v1/plans/" + submitted["id"] + "/revisions", json=changed)
            assert response.status_code == 422 and response.json()["detail"]["code"] == "invalid_plan"


@pytest.mark.parametrize("kind", ["check", "wait"])
@pytest.mark.parametrize("satisfied", [True, False])
def test_condition_diagnostic_is_retained_without_requiring_client_metadata(kind, satisfied):
    with TestClient(create_app()) as client:
        _, route = create(client)
        message = "请核对离心机盖子"
        created = client.post(
            "/api/v1/plans",
            json=plan(
                {
                    "id": 1,
                    kind: {
                        "entity": CENTRIFUGE,
                        "property": "lidOpen",
                        "equals": satisfied,
                        "message": message,
                    },
                }
            ),
        )
        assert created.status_code == 201, created.text
        submitted = created.json()
        result = condition_call(client, route, kind, submitted["id"], value=satisfied)
        assert result.status_code == 200, result.text
        assert result.json()["satisfied"] is satisfied
        current = client.get("/api/v1/plans/" + submitted["id"]).json()
        assert current["steps"][0][kind]["message"] == message
        assert current["steps"][0]["status"] == ("done" if satisfied else "failed")
        if not satisfied:
            assert result.json()["failed"][0]["message"] == message
            assert current["stepResults"]["1"]["failed"][0]["message"] == message


@pytest.mark.parametrize("kind", ["check", "wait"])
def test_deviation_threshold_is_executable_semantics_and_wrong_threshold_cannot_bind(kind):
    with TestClient(create_app()) as client:
        _, route = create(client)
        created = client.post(
            "/api/v1/plans",
            json=plan(
                {
                    "id": 1,
                    kind: {
                        "entity": CENTRIFUGE,
                        "property": "rpm",
                        "deviatesFrom": "rpm",
                        "by": 0,
                        "message": "转速应有差异",
                    },
                }
            ),
        )
        assert created.status_code == 201, created.text
        plan_id = created.json()["id"]
        body = {
            "key": CENTRIFUGE,
            "property": "rpm",
            "op": "deviatesFrom",
            "value": "rpm",
            "planId": plan_id,
            "stepId": 1,
            "by": 0.5,
        }
        if kind == "check":
            wrong = client.post(route + "/check", json=body)
            exact = client.post(route + "/check", json={**body, "by": 0})
        else:
            wrong = client.get(route + "/wait", params={**body, "value": '"rpm"', "timeoutSeconds": 0.02})
            exact = client.get(
                route + "/wait", params={**body, "value": '"rpm"', "by": 0, "timeoutSeconds": 0.02}
            )
        assert wrong.status_code == 409 and wrong.json()["detail"]["code"] == "invalid_plan_reference"
        assert exact.status_code == 200 and exact.json()["satisfied"] is False, exact.text
        assert exact.json()["failed"][0]["message"] == "转速应有差异"
        assert exact.json()["failed"][0]["condition"]["by"] == 0


@pytest.mark.parametrize("by", [True, -1, "nan", float("inf")])
def test_check_rejects_invalid_threshold_types_and_values(by):
    with TestClient(create_app()) as client:
        _, route = create(client)
        body = {"key": CENTRIFUGE, "property": "rpm", "op": "deviatesFrom", "value": "rpm", "by": by}
        if by == float("inf"):
            response = client.post(
                route + "/check",
                content=(
                    '{"key":"' + CENTRIFUGE + '","property":"rpm","op":"deviatesFrom",'
                    '"value":"rpm","by":1e999}'
                ),
                headers={"Content-Type": "application/json"},
            )
        else:
            response = client.post(route + "/check", json=body)
        assert response.status_code == 422, response.text


def test_threshold_is_rejected_for_an_unrelated_operator():
    with TestClient(create_app()) as client:
        _, route = create(client)
        wrong = client.post(
            route + "/check",
            json={
                "key": CENTRIFUGE,
                "property": "rpm",
                "op": "equals",
                "value": 0,
                "by": 0,
            },
        )
        assert wrong.status_code == 422 and wrong.json()["detail"]["code"] == "invalid_condition"
        wrong_wait = client.get(
            route + "/wait",
            params={
                "key": CENTRIFUGE,
                "property": "rpm",
                "op": "equals",
                "value": "0",
                "by": 0,
            },
        )
        assert wrong_wait.status_code == 422


async def test_deviation_wait_wakes_on_only_the_actual_reference_property_change():
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            session = (await client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@2"})).json()
            route = "/api/v1/sessions/" + session["sessionId"]
            device = "/World/Lab/Incubator"

            async def command(request_id, name, **args):
                result = await client.post(
                    route + "/commands",
                    json={
                        "type": "command.request",
                        "requestId": request_id,
                        "command": name,
                        "args": args,
                    },
                )
                assert result.status_code == 200, result.text

            await command("pause", "pause")
            await command("warm", "step", count=4)
            initial = (await client.get(route + "/state", params={"keys": device})).json()["entities"][device]
            assert abs(initial["values"]["temperatureC"] - initial["values"]["setpointC"]) < 20
            waiting = asyncio.create_task(
                client.get(
                    route + "/wait",
                    params={
                        "key": device,
                        "property": "temperatureC",
                        "op": "deviatesFrom",
                        "value": '"setpointC"',
                        "by": 20,
                        "timeoutSeconds": 1,
                    },
                )
            )
            try:
                info = app.state.manager.get(session["sessionId"]).info
                for _ in range(20):
                    if (device, "temperatureC") in info._waiters:
                        break
                    await asyncio.sleep(0)
                assert (device, "temperatureC") in info._waiters and not waiting.done()
                accepted = await client.post(
                    route + "/actions",
                    json={
                        "requestId": "reference-only",
                        "entityKey": device,
                        "action": "setSetpoint",
                        "params": {"temperatureC": 55},
                    },
                )
                assert accepted.status_code == 202, accepted.text
                await command("advance-reference", "step", count=4)
                result = await waiting
                assert result.status_code == 200 and result.json()["satisfied"], result.text
                observed = (await client.get(route + "/state", params={"keys": device})).json()["entities"][
                    device
                ]
                assert observed["values"]["temperatureC"] == initial["values"]["temperatureC"]
                assert observed["meta"]["temperatureC"] == initial["meta"]["temperatureC"]
                assert observed["values"]["setpointC"] == 55
                assert result.json()["value"] == observed["values"]["temperatureC"]
                assert not info._waiters
            finally:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
                await client.delete(route)

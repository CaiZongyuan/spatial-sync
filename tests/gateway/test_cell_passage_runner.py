"""Real HTTP/Plan pipeline with explicit synthetic mechanical feedback, not Newton proof."""

import asyncio
import json
from copy import deepcopy
from time import perf_counter

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.cli.lw.client import GatewayClient
from tests.fixtures.cell_passage_gateway import create_fixture_app
from tools.cell_passage_recipe import revise_remaining
from tools.cell_passage_runner import PassageExecutor, drive_executor
from tools.cell_passage_session import execute_round, prepare_round


class PublicHTTP(httpx.AsyncBaseTransport):
    def __init__(self, client):
        self.client = client
        self.lose_admission_response = False
        self.lose_next_get = False

    async def handle_async_request(self, request):
        if self.lose_next_get and request.method == "GET":
            self.lose_next_get = False
            raise httpx.ReadTimeout("Fixture dropped one public observation request")
        response = self.client.request(
            request.method, str(request.url), content=request.content, headers=request.headers
        )
        if (self.lose_admission_response and request.method == "POST"
                and request.url.path.endswith("/actions")):
            self.lose_admission_response = False
            raise httpx.ReadTimeout("Fixture dropped an admitted action response")
        return httpx.Response(response.status_code, headers=response.headers, content=response.content)


@pytest.fixture(scope="module")
def passage_gateway(tmp_path_factory):
    root = tmp_path_factory.mktemp("passage-http")
    started = perf_counter()
    with pytest.MonkeyPatch.context() as env:
        env.setenv("LW_LIBRARY", str(root / "library"))
        app = create_fixture_app(root)
        constructed = perf_counter()
        with TestClient(app) as client:
            ready = perf_counter()
            yield client
            finished = perf_counter()
        print({"gatewayTiming": {"construct": constructed - started, "startup": ready - constructed,
                                  "shutdown": perf_counter() - finished}})


@pytest.fixture
def passage_http(passage_gateway, request):
    started = perf_counter()
    response = passage_gateway.post("/api/v1/sessions", json={
        "sceneId": "lab-demo", "seed": getattr(request, "param", 42),
    })
    response.raise_for_status()
    session = response.json()
    admitted = perf_counter()
    try:
        yield passage_gateway, session["sessionId"]
    finally:
        finished = perf_counter()
        passage_gateway.delete("/api/v1/sessions/" + session["sessionId"]).raise_for_status()
        print({"sessionTiming": {"session": admitted - started, "business": finished - admitted,
                                  "cleanup": perf_counter() - finished}})


async def new_executor(client, *, seconds=0.2, budget_seconds=120):
    plan = await client.plan_create({
        "title": "Fixture teaching wait", "target": "session", "steps": [{
            "id": 1, "do": {"entity": "/workcell", "action": "wait", "params": {
                "owner": "auto", "epoch": 0, "operationId": "teaching-wait", "seconds": seconds,
            }},
        }],
    })
    return await PassageExecutor.open(
        client, plan["id"], "/workcell", owner="auto", epoch=0, budget_seconds=budget_seconds
    )


async def test_paused_session_does_not_admit_plan_action_or_spend_experiment_budget(passage_http):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client)
        await client.session_control("pause")
        for _ in range(3):
            assert (await executor.advance())["status"] == "paused"
        plan = await client.plan_show(executor.plan_id)
        assert plan["steps"][0]["status"] == "pending"
        assert plan["stepResults"] == {}


async def test_lost_admission_response_observes_original_invocation_without_replaying(passage_http):
    server, session = passage_http
    transport = PublicHTTP(server)
    async with httpx.AsyncClient(base_url="http://testserver", transport=transport) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client)
        transport.lose_admission_response = True
        assert (await executor.advance())["reason"] == "admission_unconfirmed"
        transport.lose_next_get = True
        assert (await executor.advance())["reason"] == "network_unavailable"
        for _ in range(100):
            result = await executor.advance()
            if result["status"] == "completed":
                break
            assert result["status"] == "observing", result
            await asyncio.sleep(0.02)
        assert result["status"] == "completed", result
        plan = await client.plan_show(executor.plan_id)
        assert len(plan["stepResults"]["1"]["invocationIds"]) == 1
        with pytest.raises(ValueError, match="new round"):
            await PassageExecutor.open(client, plan["id"], "/workcell", owner="auto", epoch=0)


async def test_control_handoff_latches_dispatch_stop_even_if_owner_later_returns(passage_http):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client)
        await client.session_control("pause")
        await client.invoke_action("/workcell", "requestControl", {
            "owner": "auto", "epoch": 0, "operationId": "handoff", "nextOwner": "human", "mode": "manual",
        }, wait=False)
        result = await executor.advance()
        assert (result["status"], result["reason"]) == ("reconcile_required", "control_changed")
        await client.invoke_action("/workcell", "confirmControl", {
            "owner": "auto", "epoch": 1, "operationId": "manual-confirm",
        }, wait=False)
        await client.invoke_action("/workcell", "requestControl", {
            "owner": "human", "epoch": 1, "operationId": "auto-return",
            "nextOwner": "auto-returned", "mode": "auto",
        }, wait=False)
        await client.invoke_action("/workcell", "confirmControl", {
            "owner": "human", "epoch": 2, "operationId": "auto-confirm",
        }, wait=False)
        assert (await executor.advance())["status"] == "reconcile_required"
        plan = await client.plan_show(executor.plan_id)
        assert plan["steps"][0]["status"] == "pending"
        checkpoint = await executor.inspect()
        revised = await client.plan_revise(executor.plan_id, revise_remaining(
            plan, owner="auto-returned", epoch=2, material_revision=checkpoint["materialRevision"],
            expected_material_revision=checkpoint["materialRevision"],
        ))
        assert revised["steps"][0]["do"]["params"]["operationId"] == "teaching-wait"
        await executor.reconcile(await executor.inspect(), checks=[{
            "entity": "/workcell", "property": "controlSafe", "equals": True,
        }])
        assert (await executor.advance())["status"] == "paused"
        await client.session_control("resume")
        for _ in range(100):
            result = await executor.advance()
            if result["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert result["status"] == "completed", result
        assert len(result["plan"]["stepResults"]["1"]["invocationIds"]) == 1


async def test_material_change_without_active_action_requires_reconciliation(passage_http):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client)
        await client.invoke_action("/workcell", "reserve", {
            "owner": "auto", "epoch": 0, "operationId": "external-reservation",
            "resource": "/peer", "device": "/workcell",
        }, wait=True, timeout=2)
        result = await executor.advance()
        assert (result["status"], result["reason"]) == ("reconcile_required", "material_changed")
        assert (await client.plan_show(executor.plan_id))["steps"][0]["status"] == "pending"
        checkpoint = await executor.inspect()
        assert (await executor.reconcile(checkpoint, checks=[{
            "entity": "/workcell", "property": "controlSafe", "equals": True,
        }]))["status"] == "ready"
        assert (await executor.advance())["status"] == "observing"


async def test_experiment_budget_uses_simulation_time_and_retains_running_invocation(passage_http):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client, seconds=2, budget_seconds=0.02)
        admitted = await executor.advance()
        invocation_id = admitted["invocation"]["invocationId"]
        await client.session_control("pause")
        await asyncio.sleep(0.08)
        assert (await executor.advance())["status"] == "paused"
        await client.session_control("resume")
        await asyncio.sleep(0.08)
        result = await executor.advance()
        assert result["status"] == "budget_exceeded"
        assert result["invocation"]["invocationId"] == invocation_id
        plan = await client.plan_show(executor.plan_id)
        assert plan["steps"][0]["status"] == "running"
        assert len(plan["stepResults"]["1"]["invocationIds"]) == 1


async def test_failed_step_revision_reconciles_without_replaying_done_step(passage_http):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        document = {"title": "Fixture revision", "target": "session", "steps": [
            {"id": number, "do": {"entity": "/workcell", "action": "wait", "params": {
                "owner": "auto", "epoch": 0, "operationId": f"step-{number}", "seconds": 0.001,
            }}, "expect": {"entity": "/workcell", "property": "controlMode",
                           "equals": "auto" if number == 1 else "manual"}}
            for number in (1, 2)
        ]}
        plan = await client.plan_create(document)
        executor = await PassageExecutor.open(client, plan["id"], "/workcell", owner="auto", epoch=0)
        for _ in range(100):
            result = await executor.advance()
            if result["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        assert result["status"] == "failed", result
        failed = await client.plan_show(plan["id"])
        assert [row["status"] for row in failed["steps"]] == ["done", "failed"]
        replacement = deepcopy(document["steps"][1])
        replacement["expect"]["equals"] = "auto"
        checkpoint = await executor.inspect()
        repaired = revise_remaining(
            failed, owner="auto", epoch=0, material_revision=checkpoint["materialRevision"],
            expected_material_revision=checkpoint["materialRevision"], replacements={2: replacement},
        )
        revised = await client.plan_revise(plan["id"], repaired)
        assert revised["revision"] == 2
        assert revised["stepResults"]["1"] == failed["stepResults"]["1"]
        await executor.reconcile(await executor.inspect(), checks=[{
            "entity": "/workcell", "property": "controlSafe", "equals": True,
        }])
        for _ in range(100):
            result = await executor.advance()
            if result["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert result["status"] == "completed", result
        assert result["plan"]["stepResults"]["1"] == failed["stepResults"]["1"]


async def test_client_exit_keeps_interrupted_receipt_and_fences_own_generation(passage_http, tmp_path):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client, seconds=5)
        receipt = tmp_path / "round.jsonl"
        task = asyncio.create_task(drive_executor(executor, receipt, interval=0.005))
        try:
            for _ in range(100):
                plan = await client.plan_show(executor.plan_id)
                if plan["steps"][0]["status"] == "running":
                    break
                await asyncio.sleep(0.005)
            assert plan["steps"][0]["status"] == "running"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        rows = [json.loads(line) for line in receipt.read_text(encoding="utf8").splitlines()]
        assert rows[-1]["status"] == "interrupted"
        assert rows[-1]["active"]["invocationId"]
        values = (await client.get_state(["/workcell"]))["entities"]["/workcell"]["values"]
        assert values["controlMode"] == "holding" and values["controlEpoch"] == 1


async def test_preparation_publishes_all_seven_stage_plans_without_starting_actions(passage_http, tmp_path):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        recipe = {"schema": "lw.cell-passage-recipe/1", "roundId": "public-fixture",
                  "mechanicallyQualified": False, "environmentChecksAvailable": False, "plans": [
                      {"stage": stage, "part": 1, "parts": 1, "document": {
                       "title": f"Cell Passage public-fixture · {stage}/7 · Fixture · part 1/1",
                       "target": "session", "steps": [{"id": stage * 100000 + 1, "do": {
                           "entity": "/workcell", "action": "wait", "params": {
                               "owner": "auto", "epoch": 0, "operationId": f"stage-{stage}", "seconds": 0.001,
                           },
                       }}]}} for stage in range(1, 8)
                  ]}
        result = await prepare_round(client, recipe, tmp_path / "prepared", workcell="/workcell")
        assert result["status"] == "prepared"
        assert [row["id"] for row in result["plans"]] == [
            f"cp-public-fixture-stage{stage}-part1-of1" for stage in range(1, 8)
        ]
        for row in result["plans"]:
            plan = await client.plan_show(row["id"])
            assert plan["steps"][0]["status"] == "pending" and plan["stepResults"] == {}
        completed = await execute_round(client, result, tmp_path / "prepared", interval=0.005)
        assert completed["status"] == "plans_completed"
        assert completed["completedParts"] == [row["id"] for row in result["plans"]]
        assert set(completed["stageResults"]) == {str(stage) for stage in range(1, 8)}
        assert completed["experimentQualified"] is False


@pytest.mark.parametrize("passage_http", [99], indirect=True)
async def test_known_generation_waits_for_source_reported_safe_settling_before_dispatch(passage_http):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client)
        result = await executor.advance()
        assert (result["status"], result["reason"]) == ("observing", "devices_settling")
        assert (await client.plan_show(executor.plan_id))["steps"][0]["status"] == "pending"
        for _ in range(150):
            result = await executor.advance()
            if result.get("invocation"):
                break
            await asyncio.sleep(0.005)
        assert result["invocation"]["invocationId"]
        values = (await client.get_state(["/workcell"]))["entities"]["/workcell"]["values"]
        assert values["controlSafe"] is True

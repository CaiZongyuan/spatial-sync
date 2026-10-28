"""Real HTTP/Plan pipeline with explicit synthetic mechanical feedback, not Newton proof."""

import asyncio
from copy import deepcopy
from time import monotonic, perf_counter

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.cli.lw.client import GatewayClient
from packages.protocol.schema import SceneManifest
from tests.fixtures.cell_passage import build_workcell_host
from tools.cell_passage_recipe import revise_remaining
from tools.cell_passage_runner import PassageExecutor


class PublicHTTP(httpx.AsyncBaseTransport):
    def __init__(self, client):
        self.client = client
        self.lose_admission_response = False

    async def handle_async_request(self, request):
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
    class WorkcellSource(SyntheticSource):
        async def start(self, scene_id, options):
            self.options = options.model_copy()
            host_root = root / self.session_id
            host_root.mkdir()
            self.host, self.actuator = build_workcell_host(host_root)
            self.catalog = self.host.catalog
            self.manifest = SceneManifest(
                sessionId=self.session_id, sceneRevision="fixture-" + self.session_id,
                revisionNumericId=1, sceneName="Cell Passage HTTP fixture",
                source={"kind": "synthetic", "physicsDt": options.physicsDt},
                nodes=[{"kind": "group", "entityKey": "/workcell", "name": "Fixture workcell"}],
            )
            self.catalog.sceneRevision = self.manifest.sceneRevision
            self._state = "RUNNING"
            self._publish("stream.snapshot")
            self._last_clock = monotonic()
            self._task = asyncio.create_task(self._run(), name=f"passage-fixture-{self.session_id}")
            return self.manifest, self.catalog

        def _poses(self):
            return []

        def _advance(self, count):
            self._step_index += count
            due = int(self.sim_time * self.options.controlHz + 1e-9)
            while self._control_index < due:
                self._control_index += 1
                for event in self.host.tick(
                    self._control_index / self.options.controlHz, 1 / self.options.controlHz
                ):
                    self._events.put_nowait(event)

    with pytest.MonkeyPatch.context() as env:
        env.setenv("LW_LIBRARY", str(root / "library"))
        settings = Settings(recordings_root=root / "recordings", state_root=root / "state", node_trainer=())
        app = create_app(settings, source_factory=lambda kind, sid: WorkcellSource(sid))
        constructed = perf_counter()
        with TestClient(app) as client:
            ready = perf_counter()
            yield client
            finished = perf_counter()
        print({"gatewayTiming": {"construct": constructed - started, "startup": ready - constructed,
                                  "shutdown": perf_counter() - finished}})


@pytest.fixture
def passage_http(passage_gateway):
    started = perf_counter()
    response = passage_gateway.post("/api/v1/sessions", json={"sceneId": "lab-demo"})
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
        assert (await client.plan_show(executor.plan_id))["steps"][0]["status"] == "pending"


async def test_material_change_without_active_action_requires_reconciliation(passage_http):
    server, session = passage_http
    async with httpx.AsyncClient(base_url="http://testserver", transport=PublicHTTP(server)) as http:
        client = GatewayClient(session=session, http=http)
        executor = await new_executor(client)
        await client.invoke_action("/workcell", "reserve", {
            "owner": "auto", "epoch": 0, "operationId": "external-reservation",
            "resource": "/source", "device": "/workcell",
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

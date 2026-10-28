"""Real HTTP/Plan pipeline with explicit synthetic mechanical feedback, not Newton proof."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from apps.cli.lw.client import GatewayClient
from tests.fixtures.cell_passage import build_workcell_host
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


@pytest.fixture
def passage_http(tmp_path):
    class WorkcellSource(SyntheticSource):
        async def start(self, scene_id, options):
            manifest, _ = await super().start(scene_id, options)
            self.fixed_poses = super()._poses()
            self.host, self.actuator = build_workcell_host(tmp_path)
            self.catalog = self.host.catalog
            self.catalog.sceneRevision = manifest.sceneRevision
            return manifest, self.catalog

        def _poses(self):
            return self.fixed_poses if hasattr(self, "fixed_poses") else super()._poses()

    settings = Settings(recordings_root=tmp_path / "recordings", state_root=tmp_path / "state")
    with TestClient(create_app(settings, source_factory=lambda kind, sid: WorkcellSource(sid))) as client:
        session = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()
        try:
            yield client, session["sessionId"]
        finally:
            client.delete("/api/v1/sessions/" + session["sessionId"])


async def new_executor(client):
    plan = await client.plan_create({
        "title": "Fixture teaching wait", "target": "session", "steps": [{
            "id": 1, "do": {"entity": "/workcell", "action": "wait", "params": {
                "owner": "auto", "epoch": 0, "operationId": "teaching-wait", "seconds": 0.2,
            }},
        }],
    })
    return await PassageExecutor.open(client, plan["id"], "/workcell", owner="auto", epoch=0)


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

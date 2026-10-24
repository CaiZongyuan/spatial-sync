"""A complete 10x3 train evaluation uses actual Synthetic Sessions and platform evidence."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
from lw.mcp_server import create_server
from mcp import Client

from apps.api.app.main import create_app
from packages.agents.provider import TOKEN_KEYS
from packages.models import ModelConfig, ModelResources, PricesInput
from packages.vault import Vault
from tests.unit.test_agent_plan_schema import agent_plan


class ExternalAdapter:
    def __init__(self):
        self.calls, self.cases = [], []

    def describe(self, model, thinking):
        return {
            "adapter": "local-fake-external",
            "contextTokens": 100,
            "runtimeHash": "a" * 64,
            "sessionOptions": {"realtimeFactor": 20},
            "enforcement": {name: "hard" for name in ("requests", "tokens", "llmUsd", "wallSeconds")},
        }

    @asynccontextmanager
    async def open(self, record, deadline, directory):
        async def external(request):
            self.calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "type": "message",
                    "id": f"fake-response-{len(self.calls)}",
                    "content": [
                        {"type": "text", "text": "Claimed score 999999 and tokens 0; this is not evidence."}
                    ],
                    "usage": dict(zip(TOKEN_KEYS, (20, 10, 2, 3), strict=True)),
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(external), base_url="http://fake-only"
        ) as client:
            yield SimpleNamespace(client=client, grant="FAKE_EXTERNAL_ONLY", trusted_relay=False)

    async def case(self, task, gateway, gate):
        self.cases.append(task["caseId"])
        response = await gate.generate(
            {
                "model": task["model"],
                "max_tokens": 20,
                "messages": [{"role": "user", "content": task["statement"]}],
            }
        )
        assert response.status_code == 200
        async with Client(await create_server(gateway), mode="legacy") as mcp:
            if task["caseId"] == "pause-reset":
                for command in ("pause", "reset"):
                    value = await mcp.call_tool("session_control", {"action": command})
                    assert not value.is_error, value.content
            else:
                value = await mcp.call_tool(
                    "invoke_action", {"key": "/World/Lab/Centrifuge", "action": "closeLid"}
                )
                assert not value.is_error, value.content
        return {"score": 999999, "tokens": 0, "finalState": {"claimed": True}}


async def configured(http, app):
    manager = app.state.manager
    resources = ModelResources(manager.settings.state_root)
    resources.add(ModelConfig(provider="anthropic", modelId="fixture-model", credentialName="llm/fixture"))
    Vault(manager.settings.state_root).set("llm/fixture", "FAKE_ONLY_AGENT_RUN_SECRET_44")
    resources.put_prices(
        PricesInput(
            models=[
                {
                    "provider": "anthropic",
                    "modelId": "fixture-model",
                    "inputUsdPerMillion": 1.0,
                    "outputUsdPerMillion": 1.0,
                    "cacheReadUsdPerMillion": 1.0,
                    "cacheWriteUsdPerMillion": 1.0,
                }
            ]
        ),
        {"kind": "user", "name": "fixture"},
    )
    submitted = await http.post("/api/v1/plans", json=agent_plan())
    assert submitted.status_code == 201, submitted.text
    plan = submitted.json()
    approved = await http.post(
        f"/api/v1/approvals/{plan['approvalId']}/decision", json={"decision": "approve"}
    )
    assert approved.status_code == 200, approved.text
    return plan


async def test_complete_train_scope_has_durable_run_real_case_sessions_trace_scores_and_trusted_cost():
    adapter = ExternalAdapter()
    app = create_app(agent_adapter=adapter)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1") as http:
            plan = await configured(http, app)
            request = {
                "suite": plan["suite"],
                "planId": plan["id"],
                "planRevision": 1,
                "requestId": "complete-train-fixture",
            }
            admitted = await http.post("/api/v1/agents/lab-operator/runs", json=request)
            assert admitted.status_code == 202, admitted.text
            job = admitted.json()
            saved = json.loads((app.state.settings.state_root / "agent-runs.json").read_bytes())
            assert job["jobId"] in saved and saved[job["jobId"]]["allocatedCaseRuns"] == 30
            async with asyncio.timeout(60):
                while True:
                    job = (await http.get(f"/api/v1/jobs/{job['jobId']}")).json()
                    if job["status"] in ("succeeded", "failed", "cancelled"):
                        break
                    await asyncio.sleep(0.02)
            assert job["status"] == "succeeded", job
            assert job["attemptedCaseRuns"] == len(job["caseRuns"]) == len(adapter.cases) == 30
            assert len(set(adapter.cases)) == 10 and all(
                adapter.cases.count(name) == 3 for name in set(adapter.cases)
            )
            assert len(adapter.calls) == job["usage"]["requests"] == 30
            assert job["usage"]["tokens"] == 1050 and job["usage"]["llmUsd"] == 0.00105
            assert all(0 <= case["score"] <= 100 for case in job["caseRuns"])
            assert any(case["score"] < 100 for case in job["caseRuns"])
            assert job["result"]["decision"] == "reported" and job["result"]["ci95"] is None
            assert len(app.state.manager.sessions) == 30
            assert not any(session.holds_resources for session in app.state.manager.sessions.values())
            for case in job["caseRuns"]:
                directory = app.state.agent_runs.root / job["jobId"] / "cases" / case["caseRunId"]
                assert (directory / "trace.jsonl").is_file() and (directory / "final.json").is_file()
                assert "FAKE_ONLY_AGENT_RUN_SECRET_44" not in (directory / "trace.jsonl").read_text(
                    encoding="utf-8"
                )
            duplicate = await http.post("/api/v1/agents/lab-operator/runs", json=request)
            assert duplicate.status_code == 202 and duplicate.json()["jobId"] == job["jobId"]
            assert len(adapter.calls) == 30 and len(app.state.agent_runs.records) == 1

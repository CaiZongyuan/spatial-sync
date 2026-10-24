"""Controlled client receipts via real Plan/Run HTTP, stores and model budget gate.

This fixture starts no Source, SDK or external provider. Scores and final state
are labelled test data; the full backend execution journey is verified separately.
"""

import asyncio
import hashlib
import json
import os
import time
from copy import deepcopy
from pathlib import Path

import httpx
from fastapi import Request, Response

ROOT = Path(__file__).resolve().parents[2]
for variable, directory in (
    ("LW_LIBRARY", "library"),
    ("SPATIAL_STATE_ROOT", "state"),
    ("SPATIAL_RECORDINGS_ROOT", "recordings"),
):
    os.environ.setdefault(variable, str(ROOT / ".scratch/agent-clients" / directory))

from apps.api.app.main import create_app  # noqa: E402
from apps.api.app.platform_monitor import platform_snapshot, unavailable_system  # noqa: E402
from apps.api.app.settings import Settings  # noqa: E402
from packages.agents.provider import BudgetedProvider, ProviderLimits  # noqa: E402
from packages.models import ModelConfig, ModelResources, PricesInput  # noqa: E402
from packages.vault import Vault  # noqa: E402
from tests.unit.test_agent_plan_schema import agent_plan  # noqa: E402


class Adapter:
    def describe(self, model, thinking):
        return {
            "adapter": "controlled-client-fixture",
            "runtimeHash": "c" * 64,
            "contextTokens": 100,
            "enforcement": {axis: "hard" for axis in ("requests", "tokens", "llmUsd", "wallSeconds")},
        }


class Sampler:
    def sample(self, rows):
        return platform_snapshot(rows, unavailable_system("Controlled Agent client fixture"))


def no_source(*args):
    raise AssertionError("Client receipt fixture cannot execute physics, training or SDK")


origins = tuple(filter(None, os.getenv("SPATIAL_ALLOWED_ORIGINS", "").split(",")))
app = create_app(
    Settings(node_trainer=(), allowed_origins=origins or Settings().allowed_origins),
    source_factory=no_source,
    platform_sampler=Sampler(),
    agent_adapter=Adapter(),
)
published = {}
plan_modes = {}


async def controlled_evaluation(evaluation_id):
    manager = app.state.manager
    service = app.state.agent_runs
    evaluation = deepcopy(manager.evaluations.store.records[evaluation_id])
    identifier = evaluation["jobId"]
    run = service.get(identifier)
    partial = plan_modes.get(run["planId"]) == "partial"
    service.update(identifier, status="running", startedAt=time.time())
    evaluation.update(status="running")
    manager.evaluations.store.save(evaluation)
    await asyncio.sleep(0.05)
    count = [0]

    async def external(request):
        count[0] += 1
        return httpx.Response(
            200,
            json={
                "type": "message",
                "id": f"client-fixture-{count[0]}",
                "content": [{"type": "text", "text": "Controlled transport response"}],
                "usage": {"input_tokens": 20}
                if partial
                else {
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 2,
                    "cache_creation_input_tokens": 3,
                },
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(external), base_url="http://offline-fixture"
    ) as http:

        def save():
            service.update(identifier, provider=gate.state, usage=gate.snapshot())

        gate = BudgetedProvider(
            identifier,
            "fixture-model",
            ProviderLimits(120, 500000, 2, 900, 512),
            lambda: service.inputs.quote(run["model"]),
            100,
            {"requests": []},
            save,
            http,
            "FAKE_ONLY_LOCAL",
            transport_token="FAKE_ONLY_PROVIDER_GRANT",
        )
        for _ in range(run["allocatedCaseRuns"]):
            await gate.generate(
                {
                    "model": "fixture-model",
                    "max_tokens": 20,
                    "messages": [{"role": "user", "content": "Controlled public case"}],
                }
            )
            if partial:
                break
    cases, artifacts = [], []
    suite = manager.evaluations.suites.resolve(run["suite"])
    for index, (case, repetition) in enumerate(
        (case, repetition)
        for case in suite.public()["cases"]
        for repetition in range(1, suite.metadata["runsPerCase"] + 1)
    ):
        if partial and index:
            break
        case_id = f"case-{index + 1:04d}"
        score = 40 if index == 0 else 80
        result = {
            "caseRunId": case_id,
            "caseId": case["id"],
            "repetition": repetition,
            "status": "valid",
            "score": score,
            "dimensions": {"finalState": 0 if index == 0 else 40, "steps": 40},
            "metrics": {"steps": 1},
            "elapsedSeconds": 0.2,
            "traceId": f"trace-{identifier}-{case_id}",
        }
        if partial:
            result.update(status="invalid", error={"code": "provider_stream_incomplete"})
            result.pop("score")
            result.pop("dimensions")
        cases.append(result)
        for kind, data in (
            ("platform-trace", {"controlled": True, "caseRunId": case_id}),
            ("final-state", {"controlled": True, "entities": {}}),
            ("score", result),
        ):
            payload = json.dumps(data).encode()
            artifact_id = f"{case_id}-{kind}"
            published[(identifier, artifact_id)] = payload
            artifacts.append(
                {
                    "id": artifact_id,
                    "path": f"cases/{case_id}/{kind}.json",
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "url": f"/api/v1/agent-runs/{identifier}/artifacts/{artifact_id}",
                    "caseRunId": case_id,
                    "kind": kind,
                    "source": "platform",
                }
            )
    usage = service.get(identifier)["usage"]
    from apps.api.app.evaluations import now

    if partial:
        evaluation.update(
            status="failed",
            caseRuns=cases,
            attemptedCaseRuns=len(cases),
            error={"code": "provider_stream_incomplete"},
            endedAt=now(),
        )
        manager.evaluations.store.save(evaluation)
        service.update(
            identifier,
            status="failed",
            reason="provider_stream_incomplete",
            attemptedCaseRuns=len(cases),
            caseRuns=cases,
            artifacts=artifacts,
            finishedAt=time.time(),
            executionSeconds=0.2,
            cleanupSeconds=0,
        )
        return
    row = {
        "evaluationId": evaluation_id,
        "layer": "agent",
        "suite": run["suite"],
        "subject": run["agent"],
        "version": run["harnessVersion"],
        "split": "train",
        "score": sum(case["score"] for case in cases) / len(cases),
        "ci95": None,
        "decision": "reported",
        "reference": None,
        "runs": suite.metadata["runsPerCase"],
        "caseRuns": len(cases),
        "recordings": [],
        "traces": [case["traceId"] for case in cases],
        "at": now(),
        "durationSeconds": 0.2,
        "planId": run["planId"],
        "planRevision": run["planRevision"],
        "goalId": run["goalId"],
        "key": {
            "suite": suite.id,
            "scene": suite.metadata["scene"],
            "source": "synthetic",
            "model": run["model"],
            "thinking": run["thinking"],
        },
        "cost": {"llmUsd": usage["llmUsd"], "tokens": usage["tokens"], "gpuHours": 0},
    }
    manager.evaluations.store.append(row)
    evaluation.update(
        status="completed", caseRuns=cases, attemptedCaseRuns=len(cases), result=row, endedAt=now()
    )
    manager.evaluations.store.save(evaluation)
    service.update(
        identifier,
        status="succeeded",
        attemptedCaseRuns=len(cases),
        caseRuns=cases,
        artifacts=artifacts,
        result=row,
        finishedAt=time.time(),
        executionSeconds=0.2,
        cleanupSeconds=0,
    )


app.state.manager.evaluations._evaluate = controlled_evaluation


@app.get("/e2e/storage-roots")
async def storage_roots():
    manager = app.state.manager
    return {
        "sceneLibrary": str(manager.library_assets.roots["user"]),
        "assetLibrary": str(manager.library_assets.roots["user"]),
        "state": str(manager.settings.state_root),
        "recordings": str(manager.settings.recordings_root),
    }


@app.post("/e2e/agent-plan")
async def seed(request: Request):
    resources = ModelResources(app.state.settings.state_root)
    if not resources.models()["models"]:
        resources.add(
            ModelConfig(provider="anthropic", modelId="fixture-model", credentialName="llm/fixture")
        )
        Vault(app.state.settings.state_root).set("llm/fixture", "FAKE_ONLY_LOCAL_CLIENT_44")
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
            {"kind": "user", "name": "owned-client-fixture"},
        )
    body = agent_plan()
    body.pop("id")
    body["title"] = "Agent 客户端验收 · 受控数据"
    if request.query_params.get("mode") == "expired":
        body["goalBudget"]["days"] = 0.000001
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=str(request.base_url)) as http:
        response = await http.post("/api/v1/plans", json=body)
    assert response.status_code == 201, response.text
    plan = response.json()
    plan_modes[plan["id"]] = request.query_params.get("mode", "complete")
    return plan


@app.get("/api/v1/agent-runs/{identifier}/artifacts/{artifact_id}")
async def artifact(identifier: str, artifact_id: str):
    payload = published.get((identifier, artifact_id))
    return Response(payload or b"{}", status_code=200 if payload else 404, media_type="application/json")

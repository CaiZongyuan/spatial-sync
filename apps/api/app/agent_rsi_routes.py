"""Agent training intent uses shared approval/evaluation resources and exact versions."""

import asyncio

from fastapi import Request

from apps.api.app.agent_evaluations import AgentEvaluationRequest
from apps.api.app.info_routes import actor_from_headers
from packages.agent_rsi.workflow import AgentRSI
from packages.info.plans import PlanError
from packages.training.schema import Schema, Text


class BaselineRequest(Schema):
    evaluationId: Text
    requestId: Text


class RunRequest(Schema):
    revision: int
    requestId: Text


class RevisionRequest(Schema):
    revision: int


def install_agent_rsi_routes(app, manager, agents, external, role_for):
    service = AgentRSI(manager, agents, external)
    app.state.agent_rsi = manager.agent_rsi = service

    def operator(request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Controller operator permission is required", status=403)
        return actor_from_headers(request.headers)

    @app.post("/api/v1/suites", status_code=201)
    async def publish_suite(request: Request):
        operator(request)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-tar":
            raise PlanError("invalid_agent_suite", "Submit a selected Suite archive", status=422)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 8 * 1024 * 1024:
                raise PlanError("suite_too_large", "Suite archives are limited to8MiB", status=413)
            data.extend(chunk)
        return await asyncio.to_thread(
            service.manager.evaluations.suites.push_agent, bytes(data), manager.library, agents
        )

    @app.post("/api/v1/agents/{name}/baseline")
    async def baseline(name: str, body: BaselineRequest, request: Request):
        operator(request)
        return await asyncio.to_thread(service.baseline, name, body.evaluationId, body.requestId)

    @app.get("/api/v1/agents/{name}/rsi")
    async def workflow(name: str):
        agent = agents.show(name)
        return {
            "agent": agent,
            "layer": "agent",
            "baselines": [
                service.public_baseline(value)
                for value in service.baselines.values()
                if value["agentId"] == agent["id"]
            ],
            "plans": [
                service.document(value)
                for value in service.records.values()
                if value.plan.agentId == agent["id"]
            ],
            "recovery": [value for value in service.recovery if value["agentId"] == agent["id"]],
        }

    @app.post("/api/v1/plans/{identifier}/agent-runs", status_code=202)
    async def start(identifier: str, body: RunRequest, request: Request):
        operator(request)
        payload, context = await asyncio.to_thread(
            service.prepare_run, identifier, body.revision, body.requestId
        )
        plan = service.get(identifier)
        run = await external.submit(
            plan.plan.agentId, AgentEvaluationRequest.model_validate(payload), agent_round=context
        )
        await asyncio.to_thread(service.retain_run, identifier, body.revision, run["id"])
        return run

    @app.post("/api/v1/plans/{identifier}/settle")
    async def settle(identifier: str, body: RevisionRequest, request: Request):
        operator(request)
        return await asyncio.to_thread(service.settle, identifier, body.revision)

    return service

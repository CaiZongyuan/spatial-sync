"""Agent training intent uses shared approval/evaluation resources and exact versions."""

import asyncio

from fastapi import Request
from fastapi.responses import Response
from pydantic import Field

from apps.api.app.agent_evaluations import AgentEvaluationRequest, CompleteCase
from apps.api.app.info_routes import actor_from_headers
from packages.agent_rsi.schema import Change
from packages.agent_rsi.workflow import AgentRSI
from packages.info.plans import PlanError
from packages.runtime_records.store import ImportCapture
from packages.training.schema import GoalRef, Schema, Text


class BaselineRequest(Schema):
    evaluationId: Text
    requestId: Text


class RunRequest(Schema):
    revision: int
    requestId: Text


class RevisionRequest(Schema):
    revision: int


class HarnessFile(Schema):
    path: Text
    content: str = Field(max_length=2 * 1024 * 1024)


class ProposalRequest(Schema):
    referenceVersion: int = Field(ge=1)
    requestId: Text
    files: list[HarnessFile] = Field(min_length=3, max_length=128)
    change: Change


class OptimizerGrantRequest(Schema):
    referenceEvaluationId: Text
    goalId: GoalRef
    nativeSessionId: Text
    requestId: Text
    wallSeconds: int = Field(default=900, ge=1, le=7200)


class PromotionRequest(RunRequest):
    pass


class ControllerGrantRequest(Schema):
    wallSeconds: int = Field(default=900, ge=1, le=7200)


class NativeBinding(Schema):
    caseRunId: Text
    nativeSessionId: str = Field(min_length=1, max_length=4096)


def install_agent_rsi_routes(app, manager, agents, external, role_for):
    service = AgentRSI(manager, agents, external)
    app.state.agent_rsi = manager.agent_rsi = service
    manager.recordings.is_restricted = service.private_recording
    manager.inbox.is_restricted_session = lambda identity: service.private_recording(identity)

    def operator(request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Controller operator permission is required", status=403)
        return actor_from_headers(request.headers)

    def human(request):
        actor = operator(request)
        if actor.kind == "agent":
            raise PlanError(
                "promotion_requires_human", "A person must review private promotion evidence", status=403
            )
        return actor

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

    @app.get("/api/v1/agents/{name}/harness/{version}")
    async def harness(name: str, version: int):
        return await asyncio.to_thread(agents.files, name, version)

    @app.post("/api/v1/agents/{name}/candidates", status_code=201)
    async def propose(name: str, body: ProposalRequest, request: Request):
        operator(request)
        return await asyncio.to_thread(service.propose, name, body)

    @app.post("/api/v1/agents/{name}/optimizer-grant", status_code=201)
    async def optimizer_grant(name: str, body: OptimizerGrantRequest, request: Request):
        operator(request)
        from packages.agent_rsi.grants import issue_optimizer

        return await asyncio.to_thread(issue_optimizer, service, name, body)

    @app.get("/api/v1/agents/{name}/tool-trace")
    async def tool_trace(name: str, goal: str):
        from packages.agent_rsi.grants import tool_trace

        return {
            "calls": await asyncio.to_thread(tool_trace, service, agents.show(name)["id"], goal),
            "healthy": not service.audit_error,
        }

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
            "auditHealthy": not service.audit_error,
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

    promotion_submission = asyncio.Lock()

    @app.post("/api/v1/plans/{identifier}/promotions", status_code=202)
    async def promote(identifier: str, body: PromotionRequest, request: Request):
        from packages.agent_rsi.promotions import request as request_promotion

        actor = operator(request)
        async with promotion_submission:
            return await request_promotion(service, identifier, body, actor)

    @app.get("/api/v1/agent-promotions/{identifier}")
    async def promotion(identifier: str):
        from packages.agent_rsi.promotions import get, public

        return public(get(service, identifier))

    @app.post("/api/v1/agent-promotions/{identifier}/controller-grant", status_code=201)
    async def promotion_grant(identifier: str, body: ControllerGrantRequest, request: Request):
        from packages.agent_rsi.promotions import issue_controller

        human(request)
        return await asyncio.to_thread(issue_controller, service, identifier, body.wallSeconds)

    @app.get("/api/v1/agent-promotions/{identifier}/review")
    async def promotion_review(identifier: str, request: Request):
        from packages.agent_rsi.promotions import review

        human(request)
        return await asyncio.to_thread(review, service, identifier)

    @app.get("/api/v1/agent-promotions/{identifier}/review/runs/{side}/cases/{case_id}/artifacts/{name}")
    async def promotion_artifact(identifier: str, side: str, case_id: str, name: str, request: Request):
        from packages.agent_rsi.promotions import get

        human(request)
        value = get(service, identifier)
        if (
            value["status"] not in ("waiting-human", "applying", "promoted", "denied")
            or side not in value["runs"]
        ):
            raise PlanError("unknown_agent_artifact", "Unknown private evidence", status=404)
        run = external.get(value["runs"][side])
        case = next((case for case in run["cases"] if case["caseRunId"] == case_id), None)
        if case is None:
            raise PlanError("unknown_agent_artifact", "Unknown private evidence", status=404)
        return Response(
            await asyncio.to_thread(external.artifact, run, case, name),
            media_type="application/x-ndjson" if name == "trace" else "application/json",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/v1/agent-promotions/{identifier}/next")
    async def promotion_next(identifier: str, request: Request):
        from packages.agent_rsi.promotions import next_work

        operator(request)
        return await next_work(service, identifier)

    @app.post("/api/v1/agent-promotions/{identifier}/bind-native")
    async def promotion_bind(identifier: str, body: NativeBinding, request: Request):
        from packages.agent_rsi.promotions import bind_native

        operator(request)
        return await bind_native(service, identifier, body)

    @app.post("/api/v1/agent-promotions/{identifier}/runtime-records/import", status_code=202)
    async def promotion_capture(identifier: str, body: ImportCapture, request: Request):
        from packages.agent_rsi.promotions import ingest

        operator(request)
        return await asyncio.to_thread(ingest, service, identifier, body)

    @app.post("/api/v1/agent-promotions/{identifier}/complete")
    async def promotion_complete(identifier: str, body: CompleteCase, request: Request):
        from packages.agent_rsi.promotions import complete

        operator(request)
        return await complete(service, identifier, body)

    @app.post("/api/v1/agent-promotions/{identifier}/cancel")
    async def promotion_cancel(identifier: str, request: Request):
        from copy import deepcopy

        from packages.agent_rsi.promotions import get, public, save

        operator(request)
        value = get(service, identifier)
        for run_id in value["runs"].values():
            await external.cancel(run_id)
        with service._lock:
            value = deepcopy(get(service, identifier))
            if value["status"] in ("preparing", "evaluating", "cleanup_blocked"):
                value["status"] = (
                    "cancelled"
                    if all(external.get(run_id)["cleanup"]["complete"] for run_id in value["runs"].values())
                    else "cleanup_blocked"
                )
                save(service, value)
        return public(value)

    @app.post("/api/v1/agent-promotions/{identifier}/cleanup")
    async def promotion_cleanup(identifier: str, request: Request):
        from packages.agent_rsi.promotions import get, public, refresh

        operator(request)
        for run_id in get(service, identifier)["runs"].values():
            if not external.get(run_id)["cleanup"]["complete"]:
                await manager.evaluations.cleanup(run_id, private=True)
        return public(await asyncio.to_thread(refresh, service, identifier))

    return service

"""Agent Job admission reuses the common human Plan and evaluator queues."""

from fastapi import Request
from fastapi.responses import FileResponse

from apps.api.app.info_routes import actor_from_headers
from packages.agents.adapter import SDKAdapter
from packages.agents.artifacts import resolve_artifact
from packages.agents.runs import AgentRunRequest, AgentRuns
from packages.info.plans import PlanError


def install_agent_run_routes(app, manager, agents, role_for, adapter=None):
    service = AgentRuns(manager, agents, adapter or SDKAdapter(manager.settings.state_root), app)
    manager.agent_runs = app.state.agent_runs = service
    manager.training_plans.agent_inputs = service.inputs
    notebooks = manager.training_plans.additional_in_use
    manager.training_plans.additional_in_use = lambda identity, revision: (
        notebooks(identity, revision) or service.plan_in_use(identity, revision)
    )

    @app.post("/api/v1/agents/{identifier}/runs", status_code=202)
    async def run(identifier: str, body: AgentRunRequest, request: Request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return await service.request(identifier, body, actor_from_headers(request.headers))

    @app.get("/api/v1/agent-runs/{identity}/artifacts/{artifact_id}")
    async def artifact(identity: str, artifact_id: str):
        path, media = resolve_artifact(service.root, service.get(identity), artifact_id)
        return FileResponse(
            path, media_type=media, filename=artifact_id + path.suffix, headers={"Cache-Control": "no-store"}
        )

    return service

"""Operation-plan HTTP resources; every device action still uses ActionPipeline."""

from typing import Any

from fastapi import Request

from apps.api.app.info_routes import actor_from_headers
from packages.info.plans import PlanError


def plan_context(plan_id=None, step_id=None, revision=None):
    if plan_id is not None and not plan_id:
        raise PlanError("invalid_plan_reference", "planId must not be empty", status=422)
    if (plan_id is None) != (step_id is None) or (revision is not None and plan_id is None):
        raise PlanError("invalid_plan_reference", "Provide planId and stepId together", status=422)
    return (
        {"planId": plan_id, "stepId": step_id, **({"planRevision": revision} if revision is not None else {})}
        if plan_id
        else None
    )


def install_plan_routes(app, manager, role_for):
    store = manager.plans

    @app.exception_handler(PlanError)
    async def plan_error(request, error):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": error.detail}, status_code=error.status)

    def target(document):
        payload = dict(document)
        session_id = payload.pop("sessionId", None)
        if payload.get("target") == "live":
            raise PlanError("target_unavailable", "A live device host is not connected", status=503)
        if session_id:
            session = manager.get(session_id)
        else:
            candidates = [item for item in manager.sessions.values() if item.state in ("RUNNING", "PAUSED")]
            if len(candidates) != 1:
                raise PlanError("target_unavailable", "Select an active session", status=409)
            session = candidates[0]
        if session.state not in ("RUNNING", "PAUSED") or not session.source:
            raise PlanError("target_unavailable", "The plan target is unavailable", status=409)
        return payload, session

    def operator(request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return actor_from_headers(request.headers)

    @app.post("/api/v1/plans", status_code=201)
    async def create_plan(body: dict[str, Any], request: Request):
        actor = operator(request)
        payload, session = target(body)
        await session.action_pipeline()
        return store.document(store.create(payload, session, actor))

    @app.post("/api/v1/plans/{plan_id}/revisions", status_code=201)
    async def revise_plan(plan_id: str, body: dict[str, Any], request: Request):
        actor = operator(request)
        payload, session = target(body)
        await session.action_pipeline()
        return store.document(store.create(payload, session, actor, plan_id=plan_id))

    @app.get("/api/v1/plans")
    async def plans(kind: str | None = None, status: str | None = None):
        store.startup()
        return {
            "plans": [
                store.document(record)
                for record in store.records.values()
                if (kind is None or kind == record.plan.kind)
                and (status is None or status == record.plan.status)
            ]
        }

    @app.get("/api/v1/plans/{plan_id}")
    async def plan(plan_id: str, revision: int | None = None):
        return store.document(store.get(plan_id, revision))

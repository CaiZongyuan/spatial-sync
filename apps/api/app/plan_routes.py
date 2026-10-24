"""Operation-plan HTTP resources; every device action still uses ActionPipeline."""

from typing import Any

from fastapi import Path, Query, Request

from apps.api.app.info_routes import actor_from_headers
from apps.api.app.information_targets import information_target
from packages.info.plans import PlanError
from packages.info.preview import PreviewRequest, SourcePreview
from packages.training.dispatch import training_store, training_stores


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
    training = manager.training_plans

    @app.exception_handler(PlanError)
    async def plan_error(request, error):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": error.detail}, status_code=error.status)

    def target(document):
        payload = dict(document)
        session_id = payload.pop("sessionId", None)
        if payload.get("target") == "live":
            session = getattr(manager, "live", None)
            if session is None:
                raise PlanError("target_unavailable", "A live device host is not connected", status=503)
            if session_id is not None and session_id != session.session_id:
                raise PlanError("target_unavailable", "The selected target is not this Live Host")
        elif session_id:
            session = manager.get(session_id)
        else:
            candidates = [item for item in manager.sessions.values() if item.state in ("RUNNING", "PAUSED")]
            if len(candidates) != 1:
                raise PlanError("target_unavailable", "Select an active session", status=409)
            session = candidates[0]
        simulation = manager.sessions.get(session.session_id)
        if simulation is not None:
            simulation.require_interactive()
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
        if body.get("kind") == "training":
            if body.get("id") in store.records:
                raise PlanError("plan_exists", "Plan ID already exists")
            selected = training_store(manager, document=body)
            if selected is training:
                body = {key: value for key, value in body.items() if key != "layer"}
            return selected.submit(body, actor)
        payload, session = target(body)
        await session.action_pipeline()
        if payload.get("id") in training.records:
            raise PlanError("plan_exists", "Plan ID already exists")
        return store.document(await store.create(payload, session, actor))

    @app.post("/api/v1/plans/{plan_id}/revisions", status_code=201)
    async def revise_plan(plan_id: str, body: dict[str, Any], request: Request):
        actor = operator(request)
        if any(plan_id in selected.records for selected in training_stores(manager)):
            selected = training_store(manager, identifier=plan_id)
            if body.get("layer", "embodied") != ("agent" if selected is manager.agent_rsi else "embodied"):
                raise PlanError("invalid_plan_layer", "A Plan revision cannot change layer", status=422)
            if selected is training:
                body = {key: value for key, value in body.items() if key != "layer"}
            return selected.submit(body, actor, plan_id=plan_id)
        payload, session = target(body)
        await session.action_pipeline()
        return store.document(await store.create(payload, session, actor, plan_id=plan_id))

    @app.get("/api/v1/plans")
    async def plans(kind: str | None = None, status: str | None = None, sessionId: str | None = None):
        store.require_started()
        await store.flush()
        documents = [
                store.document(record)
                for record in store.records.values()
            ] + [selected.document(record) for selected in training_stores(manager)
                 for record in selected.records.values()]
        return {"plans": [value for value in documents
                          if (kind is None or kind == value["kind"])
                          and (status is None or status == value["status"])
                          and (sessionId is None or sessionId == value.get("sessionId"))]}

    @app.get("/api/v1/plans/{plan_id}")
    async def plan(plan_id: str, revision: int | None = None):
        await store.flush()
        if any(plan_id in selected.records for selected in training_stores(manager)):
            selected = training_store(manager, identifier=plan_id)
            return selected.document(selected.get(plan_id, revision))
        return store.document(store.get(plan_id, revision))

    @app.get("/api/v1/plans/{plan_id}/steps/{step_id}/preview")
    async def preview(plan_id: str, step_id: int = Path(ge=1), revision: int | None = Query(None, ge=1)):
        await store.flush()
        if any(plan_id in selected.records for selected in training_stores(manager)):
            raise PlanError("unknown_plan_step", "Training plans have no device steps", status=404)
        record = store.get(plan_id, revision)
        session = information_target(manager, record.sessionId)
        if session is None:
            raise PlanError("target_unavailable", "The plan session is unavailable")
        source = session.source

        def current():
            if store.get(plan_id).plan.revision != record.plan.revision:
                raise PlanError("invalid_plan_reference", "Select the current plan revision")
            store.require_target(record, session)
            if (session.source is not source or source is None or session.state not in ("RUNNING", "PAUSED")):
                raise PlanError("target_unavailable", "The plan source is unavailable")

        async with session.command_lock:
            current()
            step = next((value for value in record.plan.steps if value.id == step_id), None)
            if step is None:
                raise PlanError("unknown_plan_step", "Unknown plan step", status=404)
            metadata = {"planId": plan_id, "revision": record.plan.revision, "stepId": step_id,
                        "sessionId": session.session_id, "sceneRevision": record.sceneRevision,
                        "epoch": record.epoch}
            if not hasattr(step, "do"):
                return metadata | {"available": False, "reason": "step_has_no_action"}
            callback = getattr(source, "preview", None)
            if callback is None:
                return metadata | {"available": False, "reason": "source_preview_unavailable"}
            request = PreviewRequest(entityKey=step.do.entity, action=step.do.action, params=step.do.params,
                                     planId=plan_id, revision=record.plan.revision, stepId=step_id)
            try:
                result = SourcePreview.model_validate(await callback(request))
            except (ValueError, KeyError) as error:
                raise PlanError("invalid_preview", str(error), status=422) from error
            except Exception as error:
                raise PlanError("preview_unavailable", str(error), status=503) from error
            current()
            if (result.sceneRevision, result.epoch) != (record.sceneRevision, record.epoch):
                raise PlanError("invalid_plan_reference", "The preview source context changed")
            if result.preview is None:
                return metadata | {"entityKey": step.do.entity, "available": False,
                                    "reason": "program_preview_unavailable"}
            return metadata | {"entityKey": step.do.entity, "available": True,
                                "simTime": result.simTime, "sampleTimeMs": result.sampleTimeMs,
                                **result.preview.model_dump(mode="json")}

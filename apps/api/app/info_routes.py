"""Information-plane HTTP boundary, shared with WebSocket actions."""

import asyncio
from copy import deepcopy
from functools import lru_cache
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool

from packages.info.conditions import OPERATORS, evaluate_conditions
from packages.info.invocations import TERMINAL, HistoryUnavailable
from packages.protocol.json import encode
from packages.protocol.schema import ActionRequest, Actor, Catalog, JsonValue, Model, NonNegative, Positive


def actor_from_headers(headers):
    from packages.agents.scope import current_scope

    managed = current_scope()
    if managed is not None:
        return Actor(kind="agent", name=managed.actor_name)
    kind, separator, name = headers.get("x-actor", "user:local").partition(":")
    if not separator or kind not in ("user", "cli", "agent") or not name.strip():
        raise HTTPException(422, {"code": "invalid_actor", "message": "Use X-Actor: user|cli|agent:name"})
    return Actor(kind=kind, name=name)


class HttpAction(ActionRequest):
    wait: StrictBool = False
    timeoutSeconds: Positive = 120
    planId: str | None = Field(default=None, min_length=1)
    stepId: int | None = Field(default=None, gt=0, strict=True)
    planRevision: int | None = Field(default=None, gt=0, strict=True)


class Approval(Model):
    decision: Literal["approve", "deny"]
    note: str = Field(default="", max_length=4000)


class CheckRequest(Model):
    key: str
    property: str
    op: str
    value: JsonValue = None
    by: NonNegative | None = None
    planId: str | None = None
    stepId: int | None = Field(default=None, gt=0, strict=True)
    planRevision: int | None = Field(default=None, gt=0, strict=True)


def invocation_response(value, *, waiting=False, timed_out=False, query=False):
    code = value.error.code if value.error else None
    status = {
        "invalid_params": 422,
        "precondition_failed": 409,
        "busy": 409,
        "unknown_entity": 404,
        "unknown_action": 404,
        "not_permitted": 403,
        "source_unavailable": 503,
        "device_unavailable": 503,
        "plan_not_approved": 409,
        "invalid_plan_reference": 409,
        "plan_persistence_unavailable": 503,
    }.get(code)
    if query:
        status = 200
    elif status is None and value.status == "rejected":
        status = 409
    if status is None:
        status = 200 if waiting or value.status in TERMINAL else 202
    data = value.model_dump(mode="json")
    if waiting:
        data["timedOut"] = timed_out
    return JSONResponse(data, status_code=status)


@lru_cache(maxsize=4096)
def _state_prefix(key, type_id):
    # Catalog identity is immutable. The state and active IDs below remain live.
    return encode({"key": key, "type": type_id})[:-1]


def install_info_routes(app, manager, role_for, *, prefix="/api/v1/sessions/{session_id}",
                        resolve_target=None):
    async def session_target(session_id: str):
        return manager.get(session_id)

    resolve_target = resolve_target or session_target

    def context_for(session, plan_id, step_id, revision):
        from apps.api.app.plan_routes import plan_context

        context = plan_context(plan_id, step_id, revision)
        if context and not getattr(session, "supports_plans", True):
            raise HTTPException(409, {"code": "live_plan_unavailable"})
        return context

    @app.exception_handler(HistoryUnavailable)
    async def history_unavailable(request: Request, exc: HistoryUnavailable):
        return JSONResponse(
            {
                "detail": {
                    "code": "history_unavailable",
                    "message": str(exc),
                    "invocationId": exc.invocation_id,
                }
            },
            status_code=503,
        )

    def binding(session, key):
        if key not in session.catalog.entities:
            raise HTTPException(404, {"code": "unknown_entity"})
        return session.catalog.entities[key]

    def state_json(session, key):
        definition = binding(session, key)
        fields = session.info.export_json(key) or '{"rev":0,"values":{},"meta":{}}'
        active = [item.invocationId for item in session.pipeline.table.active_for(key)]
        return (
            _state_prefix(key, definition["type"]) + "," + fields[1:-1] + ',"active":' + encode(active) + "}"
        )

    @app.get(prefix + "/catalog")
    async def catalog(request: Request, session=Depends(resolve_target)):
        if session.catalog is None:
            raise HTTPException(409, {"code": "information_unavailable"})
        etag = '"' + session.catalog.sceneRevision + '"'
        if request.headers.get("if-none-match") in (etag, session.catalog.sceneRevision):
            return Response(status_code=304, headers={"ETag": etag})
        document = Catalog.model_validate(session.catalog.model_dump())
        return JSONResponse(document.model_dump(mode="json", exclude_none=True), headers={"ETag": etag})

    @app.get(prefix + "/entities")
    async def entities(type: str | None = None, q: str | None = None, session=Depends(resolve_target)):
        result = []
        for key, binding in session.catalog.entities.items():
            type_id = binding["type"]
            definition = session.catalog.types[type_id]
            title = definition.get("title", type_id)
            if type and type_id != type:
                continue
            if q and q.casefold() not in (key + " " + title).casefold():
                continue
            value = session.info.entities.get(key)
            result.append(
                {"key": key, "type": type_id, "title": title, "summary": value.values if value else {}}
            )
        return {"entities": result}

    @app.get(prefix + "/entity")
    async def entity(key: str, session=Depends(resolve_target)):
        value = state_json(session, key)
        definition = binding(session, key)
        recent = session.pipeline.table.list(entity=key, limit=10)
        document = {
            "key": key,
            "type": definition["type"],
            "binding": definition,
            "definition": session.catalog.types[definition["type"]],
            "active": [item.model_dump(mode="json") for item in session.pipeline.table.active_for(key)],
            "recent": [item.model_dump(mode="json") for item in recent],
        }
        return Response(
            content=encode(document)[:-1] + ',"state":' + value + "}", media_type="application/json"
        )

    @app.get(prefix + "/state")
    async def states(keys: str | None = None, session=Depends(resolve_target)):
        selected = dict.fromkeys(keys.split(",")) if keys else session.catalog.entities.keys()
        entries = (encode(key) + ":" + state_json(session, key) for key in selected)
        return Response(content='{"entities":{' + ",".join(entries) + "}}", media_type="application/json")

    @app.post(prefix + "/actions")
    async def action(body: HttpAction, request: Request, session=Depends(resolve_target)):
        pipeline = await session.action_pipeline()
        context = context_for(session, body.planId, body.stepId, body.planRevision)
        wire_request = ActionRequest.model_validate(body.model_dump(include=set(ActionRequest.model_fields)))
        value = await pipeline.invoke(
            wire_request, role_for(request.headers), actor_from_headers(request.headers),
            plan_context=context,
        )
        timed_out = False
        if body.wait and value.status not in TERMINAL:
            value, timed_out = await pipeline.table.wait(value.invocationId, body.timeoutSeconds)
        return invocation_response(value, waiting=body.wait, timed_out=timed_out)

    @app.get(prefix + "/invocations")
    async def invocations(
        entity: str | None = None,
        status: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        session=Depends(resolve_target),
    ):
        table = session.pipeline.table
        return {
            "invocations": [
                value.model_dump(mode="json")
                for value in table.list(entity=entity, status=status, limit=limit)
            ]
        }

    @app.get(prefix + "/invocations/{invocation_id}")
    async def invocation(
        invocation_id: str,
        wait: bool = False,
        timeoutSeconds: float = Query(120, ge=0, le=3600),
        session=Depends(resolve_target),
    ):
        table = session.pipeline.table
        value = await table.lookup(invocation_id)
        if value is None:
            raise HTTPException(404, {"code": "unknown_invocation"})
        timed_out = False
        if wait and value.status not in TERMINAL:
            value, timed_out = await table.wait(invocation_id, timeoutSeconds)
        return invocation_response(value, waiting=wait, timed_out=timed_out, query=True)

    async def control(session, invocation_id, role, actor=None, decision=None, note=""):
        pipeline = await session.action_pipeline()
        try:
            value = await (
                pipeline.approve(invocation_id, decision, role, actor, note)
                if decision
                else pipeline.cancel(invocation_id, role)
            )
        except KeyError as exc:
            raise HTTPException(404, {"code": "unknown_invocation"}) from exc
        except PermissionError as exc:
            raise HTTPException(403, {"code": "not_permitted", "message": str(exc)}) from exc
        return invocation_response(value)

    @app.post(prefix + "/invocations/{invocation_id}/cancel")
    async def cancel(invocation_id: str, request: Request, session=Depends(resolve_target)):
        return await control(session, invocation_id, role_for(request.headers))

    @app.post(prefix + "/invocations/{invocation_id}/approve")
    async def approve(invocation_id: str, body: Approval, request: Request, session=Depends(resolve_target)):
        return await control(
            session,
            invocation_id,
            role_for(request.headers),
            actor_from_headers(request.headers),
            body.decision,
            body.note,
        )

    @app.get(prefix + "/wait")
    async def wait_condition(
        request: Request,
        key: str,
        property: str,
        op: str,
        value: str = "null",
        timeoutSeconds: float = Query(120, ge=0, le=3600),
        planId: str | None = None,
        stepId: int | None = Query(None, gt=0),
        planRevision: int | None = Query(None, gt=0),
        by: float | None = Query(None, ge=0, allow_inf_nan=False),
        session=Depends(resolve_target),
    ):
        import json

        binding(session, key)
        try:
            session.catalog.property(key, property)
        except KeyError as exc:
            raise HTTPException(404, {"code": "unknown_property"}) from exc
        if op not in OPERATORS:
            raise HTTPException(422, {"code": "invalid_condition"})
        try:
            expected = json.loads(value)
        except ValueError:
            expected = value
        condition = {"property": property, op: True if op in ("isNull", "notNull") else expected}
        if by is not None:
            condition["by"] = by
        try:
            # Validate the shape once, even when the current property is unknown.
            evaluate_conditions([condition], key, {})
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, {"code": "invalid_condition", "message": str(exc)}) from exc
        context = context_for(session, planId, stepId, planRevision)
        if context and role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        planned = await session.plans.begin_condition(session, {"entity": key, **condition}, "wait", context)
        condition = session.plans.declared_condition(planned, "wait", condition)
        try:
            matched_value = None

            def matches(_known, _actual):
                nonlocal matched_value
                if evaluate_conditions([condition], key, session.info.values(), session.info.metadata()):
                    return False
                matched_value = deepcopy(session.info.values().get(key, {}).get(property))
                return True

            watched = dict.fromkeys([
                property, *([condition["deviatesFrom"]] if "deviatesFrom" in condition else []),
            ])
            waits = [
                asyncio.create_task(session.info.wait(key, prop, matches, timeoutSeconds)) for prop in watched
            ]
            try:
                done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
                result = next(iter(done)).result()
                result["value"] = (matched_value if result["satisfied"] else
                                   deepcopy(session.info.values().get(key, {}).get(property)))
            finally:
                for waiter in waits:
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(*waits, return_exceptions=True)
            if planned:
                session.plans.require_target(session.plans.get(planned[0], planned[1]), session)
                if not result["satisfied"]:
                    result["failed"] = evaluate_conditions(
                        [condition], key, session.info.values(), session.info.metadata(),
                    )
            await session.plans.end_condition(session, planned, result["satisfied"], **result)
            return result
        except BaseException:
            await session.plans.end_condition(session, planned, False, reason="wait_interrupted")
            raise

    @app.post(prefix + "/check")
    async def check(body: CheckRequest, request: Request, session=Depends(resolve_target)):
        from packages.info.conditions import validate_conditions

        binding(session, body.key)
        condition = {"entity": body.key, "property": body.property,
                     body.op: True if body.op in ("isNull", "notNull") else body.value}
        if body.by is not None:
            condition["by"] = body.by
        try:
            validate_conditions([condition], body.key,
                                lambda entity, prop: session.catalog.property(entity, prop)["schema"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, {"code": "invalid_condition", "message": str(exc)}) from exc
        context = context_for(session, body.planId, body.stepId, body.planRevision)
        if context and role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        planned = await session.plans.begin_condition(session, condition, "check", context)
        condition = session.plans.declared_condition(planned, "check", condition)
        failed = evaluate_conditions([condition], body.key, session.info.values(), session.info.metadata())
        actual = session.info.values().get(body.key, {}).get(body.property)
        result = {"satisfied": not failed, "value": actual, "failed": failed}
        await session.plans.end_condition(session, planned, not failed, **result)
        return result

    @app.get(prefix + "/events")
    async def events(
        after: int = Query(0, ge=0), timeoutSeconds: float = Query(30, ge=0, le=120),
        session=Depends(resolve_target),
    ):
        return await session.info.events_after(after, timeoutSeconds)

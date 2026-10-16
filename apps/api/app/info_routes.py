"""Information-plane HTTP boundary, shared with WebSocket actions."""

from typing import Literal

from fastapi import HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import StrictBool

from apps.api.app.session_manager import encode
from packages.info.conditions import OPERATORS, evaluate_conditions
from packages.info.invocations import TERMINAL
from packages.protocol.schema import ActionRequest, Actor, Catalog, Model, Positive


def actor_from_headers(headers):
    kind, separator, name = headers.get("x-actor", "user:local").partition(":")
    if not separator or kind not in ("user", "cli", "agent") or not name.strip():
        raise HTTPException(422, {"code": "invalid_actor", "message": "Use X-Actor: user|cli|agent:name"})
    return Actor(kind=kind, name=name)


class HttpAction(ActionRequest):
    wait: StrictBool = False
    timeoutSeconds: Positive = 120


class Approval(Model):
    decision: Literal["approve", "deny"]


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


def install_info_routes(app, manager, role_for):
    prefix = "/api/v1/sessions/{session_id}"

    def state(session, key):
        if key not in session.catalog.entities:
            raise HTTPException(404, {"code": "unknown_entity"})
        value = session.info.entities.get(key)
        binding = session.catalog.entities[key]
        return {
            "key": key,
            "type": binding["type"],
            **(value.model_dump(mode="json") if value else {"rev": 0, "values": {}, "meta": {}}),
            "active": [item.invocationId for item in session.pipeline.table.active_for(key)],
        }

    @app.get(prefix + "/catalog")
    async def catalog(session_id: str, request: Request):
        session = manager.get(session_id)
        etag = '"' + session.manifest.sceneRevision + '"'
        if request.headers.get("if-none-match") in (etag, session.manifest.sceneRevision):
            return Response(status_code=304, headers={"ETag": etag})
        document = Catalog.model_validate(session.catalog.model_dump())
        return JSONResponse(document.model_dump(mode="json", exclude_none=True), headers={"ETag": etag})

    @app.get(prefix + "/entities")
    async def entities(session_id: str, type: str | None = None, q: str | None = None):
        session = manager.get(session_id)
        result = []
        for key, binding in session.catalog.entities.items():
            type_id = binding["type"]
            definition = session.catalog.types[type_id]
            title = definition.get("title", type_id)
            if type and type_id != type:
                continue
            if q and q.casefold() not in (key + " " + title).casefold():
                continue
            result.append(
                {"key": key, "type": type_id, "title": title, "summary": state(session, key)["values"]}
            )
        return {"entities": result}

    @app.get(prefix + "/entity")
    async def entity(session_id: str, key: str):
        session = manager.get(session_id)
        value = state(session, key)
        binding = session.catalog.entities[key]
        recent = session.pipeline.table.list(entity=key, limit=10)
        document = {
            "key": key,
            "type": binding["type"],
            "binding": binding,
            "definition": session.catalog.types[binding["type"]],
            "state": value,
            "active": [item.model_dump(mode="json") for item in session.pipeline.table.active_for(key)],
            "recent": [item.model_dump(mode="json") for item in recent],
        }
        return Response(content=encode(document), media_type="application/json")

    @app.get(prefix + "/state")
    async def states(session_id: str, keys: str | None = None):
        session = manager.get(session_id)
        selected = keys.split(",") if keys else session.catalog.entities.keys()
        document = {"entities": {key: state(session, key) for key in selected}}
        return Response(content=encode(document), media_type="application/json")

    @app.post(prefix + "/actions")
    async def action(session_id: str, body: HttpAction, request: Request):
        session = manager.get(session_id)
        value = await session.pipeline.invoke(
            body, role_for(request.headers), actor_from_headers(request.headers)
        )
        timed_out = False
        if body.wait and value.status not in TERMINAL:
            value, timed_out = await session.pipeline.table.wait(value.invocationId, body.timeoutSeconds)
        return invocation_response(value, waiting=body.wait, timed_out=timed_out)

    @app.get(prefix + "/invocations")
    async def invocations(
        session_id: str,
        entity: str | None = None,
        status: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
    ):
        table = manager.get(session_id).pipeline.table
        return {
            "invocations": [
                value.model_dump(mode="json")
                for value in table.list(entity=entity, status=status, limit=limit)
            ]
        }

    @app.get(prefix + "/invocations/{invocation_id}")
    async def invocation(
        session_id: str,
        invocation_id: str,
        wait: bool = False,
        timeoutSeconds: float = Query(120, ge=0, le=3600),
    ):
        table = manager.get(session_id).pipeline.table
        value = await table.lookup(invocation_id)
        if value is None:
            raise HTTPException(404, {"code": "unknown_invocation"})
        timed_out = False
        if wait and value.status not in TERMINAL:
            value, timed_out = await table.wait(invocation_id, timeoutSeconds)
        return invocation_response(value, waiting=wait, timed_out=timed_out, query=True)

    async def control(session_id, invocation_id, role, actor=None, decision=None):
        pipeline = manager.get(session_id).pipeline
        try:
            value = await (
                pipeline.approve(invocation_id, decision, role, actor)
                if decision
                else pipeline.cancel(invocation_id, role)
            )
        except KeyError as exc:
            raise HTTPException(404, {"code": "unknown_invocation"}) from exc
        except PermissionError as exc:
            raise HTTPException(403, {"code": "not_permitted", "message": str(exc)}) from exc
        return invocation_response(value)

    @app.post(prefix + "/invocations/{invocation_id}/cancel")
    async def cancel(session_id: str, invocation_id: str, request: Request):
        return await control(session_id, invocation_id, role_for(request.headers))

    @app.post(prefix + "/invocations/{invocation_id}/approve")
    async def approve(session_id: str, invocation_id: str, body: Approval, request: Request):
        return await control(
            session_id,
            invocation_id,
            role_for(request.headers),
            actor_from_headers(request.headers),
            body.decision,
        )

    @app.get(prefix + "/wait")
    async def wait_condition(
        session_id: str,
        key: str,
        property: str,
        op: str,
        value: str = "null",
        timeoutSeconds: float = Query(120, ge=0, le=3600),
    ):
        import json

        session = manager.get(session_id)
        state(session, key)
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
        try:
            # Validate the shape once, even when the current property is unknown.
            evaluate_conditions([condition], key, {})
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, {"code": "invalid_condition", "message": str(exc)}) from exc
        return await session.info.wait(
            key,
            property,
            lambda known, actual: (
                not evaluate_conditions([condition], key, {key: {property: actual}} if known else {})
            ),
            timeoutSeconds,
        )

    @app.get(prefix + "/events")
    async def events(
        session_id: str, after: int = Query(0, ge=0), timeoutSeconds: float = Query(30, ge=0, le=120)
    ):
        return await manager.get(session_id).info.events_after(after, timeoutSeconds)

"""Imported runtime information only. There is no execution or server-path input."""

import asyncio

from fastapi import Request
from fastapi.responses import Response

from packages.info.plans import PlanError
from packages.runtime_records.store import ImportCapture, RuntimeRecords


def install_runtime_routes(app, manager, agents, role_for):
    store = RuntimeRecords(manager, agents)
    app.state.runtime_records = manager.runtime_records = store

    @app.post("/api/v1/runtime-sessions/import", status_code=202)
    async def ingest(body: ImportCapture, request: Request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return await asyncio.to_thread(store.ingest, body)

    @app.get("/api/v1/runtime-sessions")
    async def sessions(
        runtime: str | None = None,
        agent: str | None = None,
        goal: str | None = None,
        plan: str | None = None,
        q: str | None = None,
    ):
        return {
            "sessions": [
                store.document(record)
                for record in tuple(store.records.values())
                if not store.is_private(record)
                and (runtime is None or record["runtime"] == runtime)
                and (agent is None or record["associations"].get("agentId") == agent)
                and (goal is None or record["associations"].get("goalId") == goal)
                and (plan is None or record["associations"].get("planId") == plan)
                and (
                    q is None
                    or q.casefold()
                    in (
                        record["nativeSessionId"]
                        + " "
                        + (record["sourceLabel"] or "")
                        + " "
                        + " ".join(record["models"])
                    ).casefold()
                )
            ]
        }

    @app.get("/api/v1/runtime-sessions/{identity}")
    async def session(identity: str):
        return store.document(store.get(identity), detail=True)

    @app.get("/api/v1/runtime-sessions/{identity}/artifacts/{artifact_id}")
    async def artifact(identity: str, artifact_id: str):
        content, media = store.artifact(store.get(identity), artifact_id)
        return Response(content, media_type=media, headers={"Cache-Control": "no-store"})

    return store

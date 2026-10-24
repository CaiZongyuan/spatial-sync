"""Shared Notebook/loop projections; only people may append override audit."""

import asyncio
import re

from fastapi import Request

from apps.api.app.info_routes import actor_from_headers
from packages.info.plans import PlanError
from packages.library.common import LibraryError
from packages.training.notebook_contract import Verdict
from packages.training.schema import Schema, Text


class OverrideRequest(Schema):
    hypothesisId: Text
    verdict: Verdict
    note: Text
    requestId: Text


def install_notebook_routes(app, service, role_for):
    def goal(value):
        if not re.fullmatch(r"goal/[a-z0-9]+(?:-[a-z0-9]+)*", value):
            raise PlanError("invalid_goal", "Select a goal/name resource ID", status=422)
        return value

    @app.get("/api/v1/loops")
    async def goals():
        return {"goals": service.goals()}

    @app.get("/api/v1/notebooks/{identity:path}")
    async def notebook(identity: str, refuted: bool = False):
        return await asyncio.to_thread(service.notebook, goal(identity), refuted=refuted)

    @app.get("/api/v1/loops/{identity:path}")
    async def loop(identity: str):
        return await asyncio.to_thread(service.loop, goal(identity))

    @app.patch("/api/v1/notebooks/{identity:path}/{entry_id}")
    async def override(identity: str, entry_id: str, body: OverrideRequest, request: Request):
        actor = actor_from_headers(request.headers)
        if role_for(request.headers) != "operator" or actor.kind == "agent":
            raise PlanError(
                "override_requires_human", "A person must override a Hypothesis judgement", status=403
            )
        await asyncio.to_thread(service.reconcile)
        try:
            entry = await asyncio.to_thread(
                service.store.override,
                goal(identity),
                entry_id,
                body.hypothesisId,
                body.verdict,
                actor,
                body.note,
                body.requestId,
            )
            return service.public_entry(entry)
        except (OSError, ValueError, LibraryError) as error:
            raise PlanError(
                "notebook_unavailable", "Lab Notebook publication is unavailable", status=503
            ) from error

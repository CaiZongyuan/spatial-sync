"""Cost reads share the exact GPU counter committed by the Job pipeline."""

from fastapi import Depends, Query, Request

from apps.api.app.info_routes import actor_from_headers
from packages.info.plans import PlanError


def install_cost_routes(app, manager, role_for):
    ledger = manager.costs

    def query(
        range: str = "7d",
        start: str | None = None,
        end: str | None = None,
        agent: str | None = Query(None, max_length=4000),
        model: str | None = Query(None, max_length=4000),
        node: str | None = Query(None, max_length=128),
        goal: str | None = Query(None, max_length=4000),
        plan: str | None = Query(None, max_length=128),
    ):
        return ledger.window(range, start=start, end=end), {
            "agent": agent,
            "model": model,
            "node": node,
            "goal": goal,
            "plan": plan,
        }

    @app.get("/api/v1/cost/summary")
    async def summary(params=Depends(query)):
        return ledger.summary(*params)

    @app.get("/api/v1/cost/series")
    async def series(params=Depends(query)):
        return ledger.series(*params)

    @app.get("/api/v1/cost/breakdown")
    async def breakdown(by: str = "plan", params=Depends(query)):
        return ledger.breakdown(*params, by)

    @app.get("/api/v1/cost/anomalies")
    async def anomalies(request: Request):
        return ledger.anomalies() | {"canAcknowledge": role_for(request.headers) == "operator"}

    @app.post("/api/v1/cost/anomalies/{identity}/read")
    async def acknowledge(identity: str, request: Request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "An operator is required", status=403)
        return ledger.acknowledge(identity, actor_from_headers(request.headers).model_dump())

"""Policy intent references existing Jobs; no host-path or artifact upload endpoint."""

import anyio
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from packages.info.plans import PlanError
from packages.policies.library import PolicyLibrary


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    checkpoint: str = Field(min_length=1, max_length=1000)


def install_policy_routes(app, manager, role_for):
    policies = PolicyLibrary(manager.library_assets.roots)
    app.state.policies = policies
    manager.policies = policies

    @app.get("/api/v1/policies")
    async def listing():
        return {"policies": await anyio.to_thread.run_sync(policies.list)}

    @app.get("/api/v1/policies/{name}/{revision}")
    async def show(name: str, revision: str):
        return await anyio.to_thread.run_sync(policies.show, f"policy/{name}@{revision}")

    @app.post("/api/v1/policies", status_code=201)
    async def create(body: PolicyCreate, request: Request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return await anyio.to_thread.run_sync(policies.create, manager, body.checkpoint)

"""Policy intent references existing Jobs; no host-path or artifact upload endpoint."""

import anyio
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.evaluation_views import public_evaluation
from packages.info.plans import PlanError
from packages.policies.evidence import ObservationEvidence
from packages.policies.library import PolicyLibrary


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    checkpoint: str = Field(min_length=1, max_length=1000)


class ObservationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    artifact: str = Field(min_length=1, max_length=1000)


def install_policy_routes(app, manager, role_for, *, profiles=None):
    policies = PolicyLibrary(manager.library_assets.roots)
    app.state.policies = policies
    manager.policies = policies
    evidence = ObservationEvidence(manager, profiles=profiles)
    manager.policy_evidence = app.state.policy_evidence = evidence

    def operator(request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)

    @app.get("/api/v1/policies")
    async def listing():
        return {"policies": await anyio.to_thread.run_sync(policies.list)}

    @app.get("/api/v1/policies/{name}/{revision}")
    async def show(name: str, revision: str):
        identity = f"policy/{name}@{revision}"
        policy = await anyio.to_thread.run_sync(policies.show, identity)
        evaluations = []
        if manager.evaluations is not None:
            rows = tuple(manager.evaluations.store.rows)
            for record in tuple(manager.evaluations.store.records.values()):
                candidate = record.get("candidateIdentity") or {}
                if (record.get("split") == "train" and candidate.get("kind") == "policy"
                        and candidate.get("id") == identity):
                    evaluations.append(public_evaluation(record, rows))
        return policy | {
            "evidence": {"observationChecks": await anyio.to_thread.run_sync(evidence.list, identity),
                         "evaluations": evaluations}
        }

    @app.post("/api/v1/policies", status_code=201)
    async def create(body: PolicyCreate, request: Request):
        operator(request)
        return await anyio.to_thread.run_sync(policies.create, manager, body.checkpoint)

    @app.post("/api/v1/policies/{name}/{revision}/observation-checks", status_code=201)
    async def check(name: str, revision: str, body: ObservationArtifact, request: Request):
        operator(request)
        return await anyio.to_thread.run_sync(evidence.check, f"policy/{name}@{revision}", body.artifact)

    @app.get("/api/v1/policies/{name}/{revision}/observation-checks")
    async def checks(name: str, revision: str):
        identity = f"policy/{name}@{revision}"
        await anyio.to_thread.run_sync(policies.show, identity)
        return {"checks": await anyio.to_thread.run_sync(evidence.list, identity)}

    @app.get("/api/v1/policies/{name}/{revision}/observation-checks/{identifier}")
    async def check_show(name: str, revision: str, identifier: str):
        return await anyio.to_thread.run_sync(evidence.show, f"policy/{name}@{revision}", identifier)

    @app.post("/api/v1/policies/{name}/{revision}/observation-checks/{identifier}/verify-source")
    async def verify_source(
        name: str, revision: str, identifier: str, body: ObservationArtifact, request: Request
    ):
        operator(request)
        return await anyio.to_thread.run_sync(
            evidence.verify_source, f"policy/{name}@{revision}", identifier, body.artifact
        )

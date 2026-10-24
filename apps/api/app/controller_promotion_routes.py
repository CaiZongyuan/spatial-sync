"""Public promotion intent and separately human-only private deployment review."""

import uuid

import anyio
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.info_routes import actor_from_headers
from apps.api.app.library_validation import program_schemas
from packages.info.plans import PlanError
from packages.library.imports import LibraryImports
from packages.library.validation import StructuralValidator
from packages.training.promotions import ControllerPromotions


class PromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: str = Field(min_length=1, max_length=1000)
    scene: str = Field(min_length=1, max_length=256)
    evaluationId: str | None = None
    observationCheck: str | None = None
    planId: str | None = None
    planRevision: int | None = Field(default=None, ge=1)
    requestId: str | None = Field(default=None, min_length=1, max_length=200)


def install_controller_promotion_routes(
    app, manager, resources, role_for, *, qualification=None, publication_validator=None
):
    validator = publication_validator or StructuralValidator(
        manager.library_assets.jobs_root.parent, program_schemas
    )
    importer = LibraryImports(
        manager.library_assets, manager.library, validator,
    )
    service = ControllerPromotions(manager, resources, importer, qualification=qualification)
    manager.controller_promotions = app.state.controller_promotions = service

    def operator(request, *, human=False):
        actor = actor_from_headers(request.headers)
        if role_for(request.headers) != "operator" or human and actor.kind != "user":
            message = "Human operator permission is required" if human else "Operator permission is required"
            raise PlanError("human_review_required" if human else "not_permitted",
                            message, status=403)
        return actor

    @app.post("/api/v1/controller-promotions", status_code=202)
    async def promote(body: PromotionRequest, request: Request):
        actor = operator(request)
        body = body.model_copy(update={"requestId": body.requestId or uuid.uuid4().hex})
        return await service.request(body, actor)

    @app.get("/api/v1/controller-promotions/{identifier}")
    async def show(identifier: str):
        value = await anyio.to_thread.run_sync(service.refresh, identifier)
        return service.public(value)

    @app.get("/api/v1/controller-promotions/{identifier}/review")
    async def review(identifier: str, request: Request):
        operator(request, human=True)
        return await anyio.to_thread.run_sync(service.review, identifier)

    return service

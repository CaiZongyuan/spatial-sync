"""Metadata-only resource routes. Vault writes and values have no HTTP route."""

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse

from apps.api.app.info_routes import actor_from_headers
from packages.models import Document, ModelConfig, ModelResources, PricesInput, ResourceError
from packages.vault import Vault, VaultError, valid_name


class DefaultModel(Document):
    ref: str


def install_resource_routes(app, settings, role_for):
    resources = ModelResources(settings.state_root)
    vault = Vault(settings.state_root)

    def operator(request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted", "message": "An operator is required"})

    @app.exception_handler(ResourceError)
    async def invalid_resource(request, error):
        return JSONResponse({"detail": {"code": error.code, "message": str(error)}}, error.status)

    @app.exception_handler(VaultError)
    async def private_vault(request, error):
        return JSONResponse(
            {"detail": {"code": "vault_unavailable", "message": "Local Vault is unavailable"}}, 503
        )

    @app.get("/api/v1/vault")
    async def vault_names():
        return JSONResponse({"names": vault.names()}, headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/models")
    async def models(request: Request, q: str | None = Query(None, max_length=256)):
        return resources.models(q) | {"canManage": role_for(request.headers) == "operator"}

    @app.post("/api/v1/models", status_code=201)
    async def add_model(body: ModelConfig, request: Request):
        operator(request)
        if body.credentialName is not None:
            try:
                valid_name(body.credentialName)
            except VaultError:
                raise HTTPException(422, {"code": "invalid_credential_name"}) from None
            if body.credentialName not in vault.names():
                raise HTTPException(
                    404,
                    {
                        "code": "unknown_credential",
                        "message": "Create the Vault name first",
                    },
                )
        return resources.add(body)

    @app.put("/api/v1/models/default")
    async def default_model(body: DefaultModel, request: Request):
        operator(request)
        return resources.set_default(body.ref)

    @app.get("/api/v1/prices")
    async def prices(request: Request):
        return resources.prices() | {"canEdit": role_for(request.headers) == "operator"}

    @app.put("/api/v1/prices")
    async def prices_update(body: PricesInput, request: Request):
        operator(request)
        actor = actor_from_headers(request.headers).model_dump()
        return resources.put_prices(body, actor) | {"canEdit": True}

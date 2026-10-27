"""Optional standalone HTTP host for the same embedded catalog maintenance module."""

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .http import install_catalog_metadata_routes
from .maintenance import MetadataMaintenance
from .models import MetadataError
from .store import MetadataStore


def create_app(*, root=None, operator_token=None, agent_profile=None, launch_workers=True):
    store = MetadataStore(
        root or Path(os.environ.get("LW_CATALOG_STATE_ROOT", "~/.lw-catalog/state")).expanduser()
    )
    maintenance = MetadataMaintenance(store, agent_profile=agent_profile, launch_workers=launch_workers)
    token = operator_token or os.environ.get("LW_CATALOG_OPERATOR_TOKEN")

    def role_for(headers):
        credential = headers.get("authorization", "").removeprefix("Bearer ")
        allowed = token is None or secrets.compare_digest(credential.encode(), token.encode())
        return "operator" if allowed else "viewer"

    @asynccontextmanager
    async def lifespan(app):
        try:
            await maintenance.startup()
            yield
        finally:
            await maintenance.close()
            store.close()

    app = FastAPI(title="Local Asset Catalog", lifespan=lifespan)
    app.state.metadata_store = store
    app.state.metadata_maintenance = maintenance
    install_catalog_metadata_routes(app, maintenance, role_for)

    @app.middleware("http")
    async def authenticate(request, call_next):
        if token is None and request.client and request.client.host not in ("127.0.0.1", "::1", "testclient"):
            return JSONResponse(
                {"detail": {"code": "not_permitted", "message": "Remote catalog credential required"}}, 403
            )
        if (
            token is not None
            and role_for(request.headers) != "operator"
            and not app.state.metadata_worker_auth(request)
        ):
            return JSONResponse(
                {"detail": {"code": "not_permitted", "message": "Catalog credential required"}}, 403
            )
        return await call_next(request)

    @app.exception_handler(MetadataError)
    async def metadata_error(request: Request, error):
        return JSONResponse(
            {"detail": {"code": error.code, "message": error.message, **error.details}}, error.status
        )

    @app.get("/api/v1/health")
    async def health():
        return {"status": "ok", "service": "catalog"}

    return app

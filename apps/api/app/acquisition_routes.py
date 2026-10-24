"""Public acquisition records. Only a catalog entry ID is ever accepted.

No route accepts a filesystem path or a URL: acquisition starts from the
committed catalog and the worker staging root stays operator-owned.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


class StartAcquisition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str


def install_acquisition_routes(app, acquisitions):
    @app.post("/api/v1/library/acquisitions", status_code=202)
    async def start_acquisition(body: StartAcquisition, request: Request):
        return JSONResponse(await acquisitions.start(body.ref), status_code=202)

    @app.get("/api/v1/library/acquisitions")
    async def list_acquisitions(status: str | None = None, limit: int = 50):
        return acquisitions.list(status, limit)

    @app.get("/api/v1/library/acquisitions/{acquisition_id}")
    async def show_acquisition(acquisition_id: str):
        return acquisitions.show(acquisition_id)

    @app.post("/api/v1/library/acquisitions/{acquisition_id}/cancel")
    async def cancel_acquisition(acquisition_id: str):
        return await acquisitions.cancel(acquisition_id)

    @app.post("/api/v1/library/acquisitions/{acquisition_id}/retry")
    async def retry_acquisition(acquisition_id: str):
        return await acquisitions.retry(acquisition_id)


def reject_invalid_acquisition(request, error):
    """One stable 422 envelope for bodies that are not exactly ``{"ref": ...}``."""
    if request.url.path.rstrip("/") == "/api/v1/library/acquisitions":
        return JSONResponse(
            {"detail": {"code": "invalid_acquisition", "message": "Provide a catalog entry ref"}},
            status_code=422,
        )
    return None


__all__ = ["StartAcquisition", "install_acquisition_routes", "reject_invalid_acquisition"]

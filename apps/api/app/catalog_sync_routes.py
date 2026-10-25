"""Operator-triggered discovery; readers keep using the published offline index."""

import anyio
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

from packages.library.common import LibraryError


def install_catalog_sync_routes(app, sync, role_for):
    def operator(request):
        if role_for(request.headers) != "operator":
            raise LibraryError("not_permitted", "Operator permission is required", 403)

    @app.get("/api/v1/library/catalog/sync")
    async def catalog_sync_status():
        return await anyio.to_thread.run_sync(sync.status)

    @app.post("/api/v1/library/catalog/sync", status_code=202)
    async def start_catalog_sync(request: Request):
        operator(request)
        return JSONResponse(await sync.start(), status_code=202)

    @app.get("/api/v1/library/catalog/sync/{identifier}")
    async def show_catalog_sync(identifier: str):
        return await anyio.to_thread.run_sync(sync.show, identifier)

    @app.post("/api/v1/library/catalog/sync/{identifier}/cancel")
    async def cancel_catalog_sync(identifier: str, request: Request):
        operator(request)
        return await sync.cancel(identifier)

    @app.post("/api/v1/library/catalog/sync/{identifier}/retry", status_code=202)
    async def retry_catalog_sync(identifier: str, request: Request):
        operator(request)
        return JSONResponse(await sync.start(retry_of=identifier), status_code=202)

    @app.get("/api/v1/library/catalog/previews/{filename}")
    async def catalog_preview(filename: str):
        path = await anyio.to_thread.run_sync(sync.preview_path, filename)
        media = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}[path.suffix[1:]]
        return FileResponse(path, media_type=media, headers={
            "Cache-Control": "public, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff",
        })

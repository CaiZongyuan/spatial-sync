"""Uploaded library packages and their indexed files."""

import tempfile

import anyio
from fastapi import Request
from fastapi.responses import FileResponse

from packages.library.assets import MAX_UPLOAD_BYTES, AssetLibrary, invalid


def install_library_routes(app, assets: AssetLibrary):
    @app.get("/api/v1/library/assets")
    async def list_assets(kind: str | None = None, q: str | None = None, gaps: bool = False):
        return {"assets": assets.list(kind, q, gaps)}

    @app.get("/api/v1/library/assets/{kind}/{name}")
    @app.get("/api/v1/library/assets/{kind}/{name}/{rev}")
    async def describe_asset(kind: str, name: str, rev: str | None = None):
        return assets.describe(f"{kind}/{name}@{rev}" if rev is not None else f"{kind}/{name}")

    @app.get("/api/v1/library/files/{root}/{relative:path}")
    async def library_file(root: str, relative: str):
        path = await anyio.to_thread.run_sync(assets.indexed_file, root, relative)
        media_type = {
            ".glb": "model/gltf-binary",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(path.suffix.lower(), "application/octet-stream")
        return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/api/v1/library/imports")
    async def list_imports():
        return {"imports": assets.jobs()}

    @app.get("/api/v1/library/imports/{import_id}")
    async def describe_import(import_id: str):
        return assets.job(import_id)

    @app.post("/api/v1/library/imports")
    async def import_package(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0] not in (
            "application/x-tar",
            "application/octet-stream",
        ):
            raise invalid(
                "Upload tar bytes with Content-Type: application/x-tar; host paths are not accepted"
            )
        size = 0
        with tempfile.TemporaryFile() as stream:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise invalid("Upload exceeds 128 MiB")
                stream.write(chunk)
            stream.seek(0)
            return await anyio.to_thread.run_sync(assets.import_tar, stream)

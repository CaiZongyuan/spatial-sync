"""Uploaded library packages and their indexed files."""

import tempfile

import anyio
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from apps.api.app.info_routes import actor_from_headers
from apps.api.app.library_validation import program_schemas
from packages.library.assets import MAX_UPLOAD_BYTES, AssetLibrary, invalid
from packages.library.description_views import description_view
from packages.library.imports import LibraryImports
from packages.library.validation import StructuralValidator


def install_library_routes(app, assets: AssetLibrary, scenes, manager, role_for):
    validator = StructuralValidator(assets.jobs_root.parent, program_schemas)

    async def request_deletion(kind, identifier, request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        actor = actor_from_headers(request.headers)
        record = await anyio.to_thread.run_sync(manager.library_deletions.request, kind, identifier, actor)
        return JSONResponse({"code": "pending_approval", "approvalId": record.approvalId,
                             "subjectId": record.subjectId}, status_code=202)

    @app.delete("/api/v1/library/assets/{kind}/{name}/{rev}")
    async def delete_asset(kind: str, name: str, rev: str, request: Request):
        return await request_deletion("asset", f"{kind}/{name}@{rev}", request)

    @app.delete("/api/v1/library/scenes/{name}/{rev}")
    async def delete_scene(name: str, rev: str, request: Request):
        return await request_deletion("scene", f"{name}@{rev}", request)

    @app.get("/api/v1/library/assets")
    async def list_assets(kind: str | None = None, q: str | None = None, gaps: bool = False):
        return {"assets": assets.list(kind, q, gaps)}

    @app.get("/api/v1/library/assets/{kind}/{name}")
    @app.get("/api/v1/library/assets/{kind}/{name}/{rev}")
    async def describe_asset(
        kind: str, name: str, rev: str | None = None,
        view: str = "full", section: str | None = None, offset: int = 0,
        documentDigest: str | None = None,
    ):
        selector = f"{kind}/{name}@{rev}" if rev is not None else f"{kind}/{name}"
        detail = await anyio.to_thread.run_sync(assets.describe, selector, scenes())
        return description_view(detail, "asset", selector, view=view, section=section, offset=offset,
                                document_digest=documentDigest)

    @app.post("/api/v1/library/assets/{kind}/{name}/{rev}/validate")
    async def validate_asset(kind: str, name: str, rev: str):
        version = assets.resolve(f"{kind}/{name}@{rev}")
        return await anyio.to_thread.run_sync(validator.revalidate, version, "asset", assets)

    @app.post("/api/v1/library/scenes/{name}/{rev}/validate")
    async def validate_scene(name: str, rev: str):
        version = scenes().resolve(f"{name}@{rev}")
        return await anyio.to_thread.run_sync(validator.revalidate, version, "scene", assets)

    @app.get("/api/v1/library/files/{root}/{relative:path}")
    async def library_file(root: str, relative: str):
        collection = assets if (root, relative) in assets.files else scenes()
        path = await anyio.to_thread.run_sync(collection.indexed_file, root, relative)
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
            importer = LibraryImports(assets, scenes(), validator)
            return await anyio.to_thread.run_sync(importer.import_tar, stream)

"""Uploaded library packages, indexed files and the external asset catalog."""

import functools
import json
import tempfile
from typing import Literal

import anyio
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

from apps.api.app.info_routes import actor_from_headers
from apps.api.app.library_validation import program_schemas
from packages.catalog import AssetCatalog
from packages.library.assets import MAX_UPLOAD_BYTES, AssetLibrary, invalid
from packages.library.authoring import LIST_DEFAULT, invalid_authoring
from packages.library.description_views import description_view
from packages.library.imports import LibraryImports
from packages.library.validation import StructuralValidator


def install_library_routes(
    app, assets: AssetLibrary, scenes, manager, role_for, catalog=None, authoring=None
):
    validator = StructuralValidator(assets.jobs_root.parent, program_schemas)
    catalog = catalog if catalog is not None else AssetCatalog(None)

    @app.post("/api/v1/library/authoring-reports")
    async def submit_authoring_report(request: Request):
        length = request.headers.get("content-length")
        if length is not None and (not length.isdecimal() or int(length) > 1024 * 1024):
            raise invalid_authoring("Authoring report body exceeds 1 MiB")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 1024 * 1024:
                raise invalid_authoring("Authoring report body exceeds 1 MiB")
        try:
            document = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise invalid_authoring("Provide one JSON authoring report object") from None
        if not isinstance(document, dict):
            raise invalid_authoring("Provide one JSON authoring report object")
        try:
            report, created = await anyio.to_thread.run_sync(authoring.submit, document)
        except ValidationError:
            raise invalid_authoring("Authoring report does not match its required fields") from None
        return JSONResponse(report, status_code=201 if created else 200)

    @app.get("/api/v1/library/authoring-reports")
    async def list_authoring_reports(
        scene: str | None = None, asset: str | None = None,
        draft: str | None = None, limit: str = str(LIST_DEFAULT),
    ):
        values = await anyio.to_thread.run_sync(
            functools.partial(authoring.list, scene=scene, asset=asset, draft=draft, limit=limit)
        )
        return {"reports": values}

    @app.get("/api/v1/library/authoring-reports/{submission_id}")
    async def show_authoring_report(submission_id: str):
        return await anyio.to_thread.run_sync(authoring.show, submission_id)

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

    @app.get("/api/v1/library/search")
    async def search_library(
        q: str | None = None,
        scope: Literal["all", "local", "online"] = "all",
        kind: str | None = None,
        provider: str | None = None,
        usage: Literal["dressing", "staticCollision"] | None = None,
        licenseAdmitted: bool | None = None,
        installed: bool | None = None,
        minSourceBytes: int | None = None,
        maxSourceBytes: int | None = None,
        minDimensionM: float | None = None,
        maxDimensionM: float | None = None,
        approximate: bool = False,
        sort: Literal["relevance", "name", "sourceBytes"] = "relevance",
        limit: int = 10,
        cursor: str | None = None,
    ):
        search = functools.partial(
            catalog.search,
            assets,
            q=q,
            scope=scope,
            kind=kind,
            provider=provider,
            usage=usage,
            license_admitted=licenseAdmitted,
            installed=installed,
            min_source_bytes=minSourceBytes,
            max_source_bytes=maxSourceBytes,
            min_dimension_m=minDimensionM,
            max_dimension_m=maxDimensionM,
            approximate=approximate,
            sort=sort,
            limit=limit,
            cursor=cursor,
        )
        return await anyio.to_thread.run_sync(search)

    @app.get("/api/v1/library/catalog/entries/{provider}/{package}/{revision}")
    async def describe_catalog_entry(
        provider: str,
        package: str,
        revision: str,
        view: Literal["summary", "full"] = "summary",
        documentDigest: str | None = None,
    ):
        describe = functools.partial(
            catalog.describe,
            provider,
            package,
            revision,
            library=assets,
            view=view,
            document_digest=documentDigest,
        )
        return await anyio.to_thread.run_sync(describe, limiter=getattr(catalog.resources, "limiter", None))

    @app.get("/api/v1/library/catalog/entries/{provider}/{package}/{revision}/estimate")
    async def estimate_catalog_entry(provider: str, package: str, revision: str):
        estimate = functools.partial(catalog.estimate, provider, package, revision, library=assets)
        return await anyio.to_thread.run_sync(estimate, limiter=getattr(catalog.resources, "limiter", None))

    @app.get("/api/v1/library/assets/{kind}/{name}")
    @app.get("/api/v1/library/assets/{kind}/{name}/{rev}")
    async def describe_asset(
        kind: str, name: str, rev: str | None = None,
        view: str = "full", section: str | None = None, offset: str = "0",
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

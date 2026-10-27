"""Uploaded library packages, indexed files and the external asset catalog."""

import base64
import functools
import json
import math
import tempfile
from pathlib import Path
from typing import Literal

import anyio
from fastapi import HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import ValidationError

from apps.api.app.info_routes import actor_from_headers
from apps.api.app.library_validation import program_schemas
from packages.catalog import AssetCatalog
from packages.library.assets import MAX_UPLOAD_BYTES, AssetLibrary, invalid
from packages.library.authoring import LIST_DEFAULT, invalid_authoring
from packages.library.common import REPO_ROOT
from packages.library.description_views import description_view
from packages.library.imports import LibraryImports
from packages.library.previews import dependency_path
from packages.library.read_cache import stamps, tree_paths
from packages.library.removals import RemovalRequest
from packages.library.thumbnails import MAX_PNG_BYTES
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

    @app.post("/api/v1/library/deletions", status_code=202)
    async def remove_version(body: RemovalRequest, request: Request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        try:
            record = await anyio.to_thread.run_sync(
                manager.library_removals.submit, body, actor_from_headers(request.headers)
            )
        except OSError:
            raise HTTPException(503, {"code": "deletion_persistence_unavailable",
                                      "message": "Removal request could not be saved"}) from None
        manager.library_removals.start(record["id"])
        return record

    @app.get("/api/v1/library/deletions")
    async def removed_versions(requestId: str | None = None, limit: int = Query(20, ge=1, le=100),
                               cursor: str | None = None):
        records = [record for record in manager.library_removals.list()
                   if requestId is None or record["requestId"] == requestId]
        total = len(records)
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError()
                after = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if (not isinstance(after, list) or len(after) != 2 or not isinstance(after[0], (float, int))
                        or not math.isfinite(after[0]) or not isinstance(after[1], str)):
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                raise HTTPException(422, {"code": "invalid_deletion_cursor"}) from None
            records = [record for record in records if (record["createdAtMs"], record["id"]) < tuple(after)]
        selected = records[:limit]
        next_cursor = base64.urlsafe_b64encode(json.dumps([
            selected[-1]["createdAtMs"], selected[-1]["id"],
        ]).encode()).decode() if len(records) > limit else None
        return {"deletions": selected, "pagination": {"total": total, "nextCursor": next_cursor}}

    @app.get("/api/v1/library/deletions/{identifier}")
    async def removal_record(identifier: str):
        return manager.library_removals.show(identifier)

    @app.post("/api/v1/library/deletions/{identifier}/restore", status_code=202)
    async def restore_version(identifier: str, request: Request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        try:
            record = await anyio.to_thread.run_sync(
                manager.library_removals.request_restore, identifier, actor_from_headers(request.headers)
            )
        except OSError:
            raise HTTPException(503, {"code": "deletion_persistence_unavailable",
                                      "message": "Restore request could not be saved"}) from None
        manager.library_removals.start(identifier, restore=True)
        return record

    @app.delete("/api/v1/library/scenes/{name}/{rev}")
    async def delete_scene(name: str, rev: str, request: Request):
        return await request_deletion("scene", f"{name}@{rev}", request)

    @app.get("/api/v1/library/assets")
    async def list_assets(kind: str | None = None, q: str | None = None, gaps: bool = False):
        return {"assets": assets.list(kind, q, gaps)}

    def thumbnail_version(resource, identifier, digest):
        collection = assets if resource == "asset" else scenes()
        version = collection.versions.get(identifier)
        if version is None or version.content_hash != digest:
            raise HTTPException(404, {"code": "unknown_version"})
        return version

    @app.get("/api/v1/library/thumbnails/{identifier}.png")
    async def retained_thumbnail(identifier: str):
        payload = await anyio.to_thread.run_sync(manager.library_thumbnails.read, identifier)
        return Response(payload, media_type="image/png", headers={
            "Cache-Control": "private, max-age=86400, immutable", "X-Content-Type-Options": "nosniff",
        })

    @app.get("/api/v1/library/thumbnails")
    async def thumbnail_image(
        resource: Literal["asset", "scene"], id: str, contentHash: str, request: Request,
        theme: Literal["light", "dark"] = "light", source: Literal["synthetic", "newton"] | None = None,
    ):
        def read():
            version = thumbnail_version(resource, id, contentHash)
            variant = theme
            if resource == "scene":
                variant += ":" + (source or version.metadata["sources"][0])
            url = manager.library_thumbnails.url(resource, version, variant)
            if url is None:
                raise HTTPException(404, {"code": "thumbnail_missing"})
            identifier = url.rsplit("/", 1)[1].removesuffix(".png")
            if request.headers.get("if-none-match") == '"' + identifier + '"':
                return Response(status_code=304)
            payload = manager.library_thumbnails.read(identifier)
            return Response(payload, media_type="image/png", headers={
                "Cache-Control": "private, max-age=0, must-revalidate", "ETag": '"' + identifier + '"',
                "X-Content-Type-Options": "nosniff",
            })
        return await anyio.to_thread.run_sync(read)

    @app.post("/api/v1/library/thumbnails")
    async def store_thumbnail(
        request: Request, resource: Literal["asset", "scene"], id: str, contentHash: str,
        theme: Literal["light", "dark"] = "light", source: Literal["synthetic", "newton"] | None = None,
        capturedAt: str | None = None,
    ):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > MAX_PNG_BYTES:
                raise HTTPException(413, {"code": "thumbnail_too_large"})

        def save():
            version = thumbnail_version(resource, id, contentHash)
            variant = theme
            collection = assets if resource == "asset" else scenes()
            paths = tree_paths(version.path, collection.roots[version.root])
            renderer = REPO_ROOT / "apps/web/src"
            paths.update(tree_paths(renderer, renderer))
            paths.add(REPO_ROOT / "pnpm-lock.yaml")
            if resource == "scene":
                version = scenes().resolve(id)
                selected = source or version.metadata["sources"][0]
                try:
                    directory, capture = manager.library_previews._capture(version, selected)
                except (OSError, ValueError):
                    raise HTTPException(409, {"code": "preview_unavailable", "message":
                                             "Rebuild a matching preview before capturing"}) from None
                if capturedAt != capture["capturedAt"]:
                    raise HTTPException(409, {"code": "preview_changed"})
                variant += ":" + selected
                capture_root = next(root for root in manager.library_previews.roots
                                    if directory.is_relative_to(root))
                paths.update(tree_paths(directory, capture_root))
                paths.update(dependency_path(item) for item in capture["dependencies"])
                for snapshot in (*version.template_assets.values(), *version.runtime_assets.values()):
                    path = Path(snapshot["path"])
                    paths.update(tree_paths(path, path.parents[3]))
            watched = tuple(sorted(paths))
            recorded = stamps(watched)
            if resource == "scene":
                manager.library_previews.read(version, selected, capture["documents"]["manifest"])
            else:
                assets.indexed_file(version.root, f"assets/{version.bare_id}/{version.rev}/asset.yaml")
            return manager.library_thumbnails.put(
                resource, version, variant, bytes(payload), watched, recorded
            )
        return await anyio.to_thread.run_sync(save)

    @app.get("/api/v1/library/search")
    async def search_library(
        q: str | None = None,
        scope: Literal["all", "local", "online"] = "all",
        kind: str | None = None,
        provider: str | None = None,
        category: str | None = None,
        tag: str | None = None,
        release: str | None = None,
        page: int | None = None,
        gaps: bool | None = None,
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
            category=category,
            tag=tag,
            release=release,
            page=page,
            gaps=gaps,
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
        release: str | None = None,
    ):
        describe = functools.partial(
            catalog.describe,
            provider,
            package,
            revision,
            library=assets,
            view=view,
            document_digest=documentDigest,
            release=release,
        )
        return await anyio.to_thread.run_sync(describe, limiter=getattr(catalog.resources, "limiter", None))

    @app.get("/api/v1/library/catalog/releases/{identifier}")
    async def catalog_release(identifier: str, offset: int = 0, limit: int = 4096):
        read = functools.partial(catalog.release_document, identifier, offset=offset, limit=limit)
        return await anyio.to_thread.run_sync(read)

    @app.get("/api/v1/library/catalog/entries/{provider}/{package}/{revision}/estimate")
    async def estimate_catalog_entry(provider: str, package: str, revision: str, release: str | None = None):
        estimate = functools.partial(catalog.estimate, provider, package, revision,
                                     library=assets, release=release)
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

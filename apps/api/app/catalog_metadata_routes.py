"""The shared HTTP maintenance contract for people and background workers."""

import functools
import json

import anyio
from fastapi import Request
from fastapi.responses import JSONResponse, Response

from packages.catalog_metadata import MetadataError

PREFIX = "/api/v1/library/catalog/maintenance"


def install_catalog_metadata_routes(app, maintenance, role_for):
    store = maintenance.store

    def worker(request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        return maintenance.authorize_worker(request.url.path, token)

    def operator(request):
        credential = request.headers.get("authorization", "")
        if not worker(request) and (
            credential.startswith("Bearer lw-meta-") or role_for(request.headers) != "operator"
        ):
            raise MetadataError("not_permitted", "Operator permission is required", 403)

    async def body(request, maximum=1048576):
        raw = await request.body()
        if len(raw) > maximum:
            raise MetadataError(
                "metadata_request_too_large", "Maintenance request exceeds its byte limit", 413
            )
        try:
            value = json.loads(raw or b"{}")
        except ValueError as exc:
            raise MetadataError("invalid_metadata_request", "A JSON object is required") from exc
        if not isinstance(value, dict):
            raise MetadataError("invalid_metadata_request", "A JSON object is required")
        return value

    async def call(function, *args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(function, *args, **kwargs))

    def base_version(value):
        version = value.get("baseVersion")
        if type(version) is not int or version < 0:
            raise MetadataError("invalid_base_version", "Supply the current nonnegative integer baseVersion")
        return version

    @app.get(PREFIX)
    async def maintenance_status():
        return await call(maintenance.status)

    @app.get(PREFIX + "/collections")
    async def collections():
        return {"collections": await call(store.collections)}

    @app.post(PREFIX + "/collections")
    async def register_collection(request: Request):
        operator(request)
        return await call(store.register_collection, await body(request, 65536))

    @app.patch(PREFIX + "/collections/{identifier}")
    async def configure_collection(identifier: str, request: Request):
        operator(request)
        changes = await body(request, 65536)
        if set(changes) - {"enabled", "scheduleSeconds", "revision", "associated", "access"}:
            raise MetadataError(
                "invalid_collection_update", "Update enablement, schedule, revision or associated evidence"
            )
        if "scheduleSeconds" in changes and (
            isinstance(changes["scheduleSeconds"], bool)
            or not isinstance(changes["scheduleSeconds"], int)
            or (changes["scheduleSeconds"] != 0 and not 60 <= changes["scheduleSeconds"] <= 604800)
        ):
            raise MetadataError(
                "invalid_schedule", "Schedule is disabled with 0 or an interval from 60 to 604800 seconds"
            )
        profile = await call(store.collection, identifier)
        return await call(store.register_collection, profile | changes)

    @app.get(PREFIX + "/discover")
    async def discover(request: Request, limit: int = 20):
        operator(request)
        from packages.catalog_metadata.hub import HFHubReader

        def read():
            reader = HFHubReader(cache_root=store.blob_root, budgets={"requests": 4, "networkBytes": 2097152})
            try:
                return {"candidates": reader.discover(limit=limit), "enabledAutomatically": False}
            finally:
                reader.close()

        return await call(read)

    @app.post(PREFIX + "/runs", status_code=202)
    async def start_run(request: Request):
        operator(request)
        value = await body(request, 65536)
        if not isinstance(value.get("collectionId"), str):
            raise MetadataError("invalid_maintenance_request", "Select a registered collectionId")
        result = await maintenance.start(
            value["collectionId"],
            revision=value.get("revision"),
            budgets=value.get("budgets"),
            request_id=value.get("requestId"),
            callback_url=maintenance.callback_url(request),
        )
        return JSONResponse(result, status_code=202)

    @app.get(PREFIX + "/runs/{identifier}")
    async def show_run(identifier: str):
        return await call(maintenance.show, identifier)

    @app.post(PREFIX + "/runs/{identifier}/cancel")
    async def cancel_run(identifier: str, request: Request):
        operator(request)
        return await maintenance.cancel(identifier)

    @app.post(PREFIX + "/runs/{identifier}/resume", status_code=202)
    async def resume_run(identifier: str, request: Request):
        operator(request)
        return await maintenance.resume(identifier, callback_url=maintenance.callback_url(request))

    @app.post(PREFIX + "/runs/{identifier}/retry", status_code=202)
    async def retry_run(identifier: str, request: Request):
        operator(request)
        previous = await call(maintenance.show, identifier)
        if previous["status"] in ("queued", "running"):
            raise MetadataError("maintenance_in_progress", "Cancel the active run before retrying", 409)
        return await maintenance.start(
            previous["collectionId"],
            revision=previous["requestedRevision"],
            budgets=previous["budgets"],
            callback_url=maintenance.callback_url(request),
        )

    @app.post(PREFIX + "/runs/{identifier}/phase")
    async def report_phase(identifier: str, request: Request):
        operator(request)
        value = await body(request, 4096)
        return await call(maintenance.phase, identifier, value.get("phase"))

    @app.post(PREFIX + "/runs/{identifier}/inventory")
    async def inventory_batch(identifier: str, request: Request):
        operator(request)
        value = await body(request, 16777216)
        return await call(
            maintenance.ingest,
            identifier,
            value.get("entries"),
            value.get("offset"),
            minimal=value.get("minimal") is True,
        )

    @app.post(PREFIX + "/runs/{identifier}/checkpoint")
    async def source_checkpoint(identifier: str, request: Request):
        operator(request)
        value = await body(request, 4096)
        return await call(
            maintenance.checkpoint,
            identifier,
            value.get("usage", {}),
            source_revision=value.get("sourceRevision"),
        )

    @app.post(PREFIX + "/runs/{identifier}/inventory/complete")
    async def inventory_complete(identifier: str, request: Request):
        operator(request)
        return await call(maintenance.complete_inventory, identifier, await body(request, 1048576))

    @app.post(PREFIX + "/runs/{identifier}/extracted")
    async def extraction_batch(identifier: str, request: Request):
        operator(request)
        value = await body(request, 16777216)
        return await call(maintenance.ingest_extracted, identifier, value.get("entries"))

    @app.post(PREFIX + "/runs/{identifier}/publish")
    async def publish_inventory(identifier: str, request: Request):
        operator(request)
        return await call(maintenance.publish_inventory, identifier)

    @app.post(PREFIX + "/runs/{identifier}/claim")
    async def claim_batch(identifier: str, request: Request):
        operator(request)
        value = await body(request, 4096)
        return await call(maintenance.claim, identifier, limit=value.get("limit", 24))

    @app.post(PREFIX + "/runs/{identifier}/batches/{batch_id}/submit")
    async def submit_batch(identifier: str, batch_id: str, request: Request):
        operator(request)
        return await call(maintenance.submit, identifier, batch_id, await body(request, 4194304))

    @app.post(PREFIX + "/runs/{identifier}/batches/{batch_id}/execution")
    async def execution_start(identifier: str, batch_id: str, request: Request):
        operator(request)
        return await call(maintenance.execution_start, identifier, batch_id)

    @app.post(PREFIX + "/runs/{identifier}/batches/{batch_id}/error")
    async def batch_error(identifier: str, batch_id: str, request: Request):
        operator(request)
        value = await body(request, 4096)
        return await call(
            maintenance.batch_error,
            identifier,
            batch_id,
            str(value.get("code", "batch_error"))[:80],
            str(value.get("message", "Batch failed"))[:500],
            execution_started=value.get("executionStarted"),
        )

    @app.post(PREFIX + "/runs/{identifier}/finish")
    async def finish_run(identifier: str, request: Request):
        operator(request)
        return await call(maintenance.finish, identifier)

    @app.post(PREFIX + "/runs/{identifier}/error")
    async def run_error(identifier: str, request: Request):
        operator(request)
        value = await body(request, 4096)
        if isinstance(value.get("usage"), dict):
            await call(maintenance.checkpoint, identifier, value["usage"])
        return await call(
            maintenance.fail,
            identifier,
            str(value.get("code", "worker_error"))[:80],
            str(value.get("message", "Worker failed"))[:500],
            budget=value.get("code") == "budget_exhausted",
        )

    @app.get(PREFIX + "/evidence/{digest}")
    async def evidence(digest: str, view: str = "summary", offset: int = 0, limit: int = 32768):
        if view not in ("summary", "raw") or offset < 0 or not 1 <= limit <= 65536:
            raise MetadataError(
                "invalid_evidence_view", "Select summary/raw, a nonnegative offset and limit 1..65536"
            )
        raw = await call(store.blob, digest)
        if view == "summary":
            chunk = raw[offset : offset + limit]
            try:
                text, encoding = chunk.decode("utf-8"), "utf-8"
            except UnicodeDecodeError:
                text, encoding = None, "binary"
            return {
                "digest": digest,
                "bytes": len(raw),
                "offset": offset,
                "text": text,
                "encoding": encoding,
                "truncated": offset + len(chunk) < len(raw),
                "nextOffset": offset + len(chunk) if offset + len(chunk) < len(raw) else None,
            }
        return Response(
            raw,
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

    @app.get(PREFIX + "/runs/{identifier}/evidence/{digest}")
    async def run_evidence(identifier: str, digest: str, request: Request):
        operator(request)
        record = await call(maintenance.show, identifier)
        if not record["inventoryId"]:
            raise MetadataError(
                "inventory_not_ready", "Evidence is available after its inventory is staged", 409
            )
        entries = await call(store.inventory_entries, record["inventoryId"])
        if not any(item.get("sha256") == digest for entry in entries for item in entry.get("evidence", [])):
            raise MetadataError(
                "evidence_not_in_run", "This evidence does not belong to the claimed run", 403
            )
        return Response(await call(store.blob, digest), media_type="application/octet-stream")

    @app.post(PREFIX + "/corrections")
    async def correct(request: Request):
        operator(request)
        value = await body(request, 65536)
        return await call(
            store.correct,
            value.get("collectionId"),
            value.get("upstreamId"),
            value.get("fields"),
            base_version=base_version(value),
            reason=value.get("reason", "Manual correction"),
        )

    @app.post(PREFIX + "/corrections/{identifier}/retract")
    async def retract(identifier: str, request: Request):
        operator(request)
        value = await body(request, 4096)
        return await call(store.retract, identifier, base_version=base_version(value))

    @app.post(PREFIX + "/organization/select")
    async def select_organization(request: Request):
        operator(request)
        value = await body(request, 4096)
        return await call(
            store.select_organization,
            value.get("collectionId"),
            value.get("upstreamId"),
            value.get("inputDigest"),
            value.get("version"),
            base_version=base_version(value),
        )

    @app.get(PREFIX + "/organization/history")
    async def organization_history(collectionId: str, upstreamId: str, inputDigest: str):
        return {"versions": await call(store.organization_history, collectionId, upstreamId, inputDigest)}

    @app.get(PREFIX + "/vocabulary")
    async def vocabulary(version: str | None = None):
        return await call(store.vocabulary, version)

    @app.put(PREFIX + "/vocabulary")
    async def update_vocabulary(request: Request):
        operator(request)
        value = await body(request, 1048576)
        return await call(
            store.set_vocabulary, value.get("vocabulary"), base_version=value.get("baseVersion")
        )

    @app.post(PREFIX + "/publish")
    async def publish(request: Request):
        operator(request)
        value = await body(request, 65536)
        return await call(maintenance.publish, value.get("inventoryIds"))

    @app.get(PREFIX + "/export")
    async def export_metadata(request: Request):
        operator(request)
        return await call(store.export)

    @app.post(PREFIX + "/restore")
    async def restore_metadata(request: Request):
        operator(request)
        return await call(maintenance.restore, await body(request, 402653184))

    @app.get(PREFIX + "/preview")
    async def preview(entry: str, release: str | None = None, retry: int = 0):
        from packages.catalog_metadata.hub import HFHubReader

        def read():
            selected = store.entry(entry, release_id=release)
            reader = HFHubReader(cache_root=store.blob_root, budgets={"previewBytes": 2097152})
            try:
                image = reader.read_preview(
                    store.collection(selected["collectionId"]), selected, retry=bool(retry)
                )
                return image
            finally:
                reader.close()

        data, media_type = await call(read)
        return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=86400"})

    app.state.metadata_worker_auth = worker

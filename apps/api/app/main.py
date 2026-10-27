"""HTTP/WS control plane. Physics adapters are loaded only when explicitly selected."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import math
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import anyio
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import StrictBool, TypeAdapter, ValidationError, model_validator

from apps.api.app.acquisition_routes import (
    install_acquisition_routes,
    reject_invalid_acquisition,
)
from apps.api.app.agent_evaluations import install_external_evaluations
from apps.api.app.agent_routes import install_agent_routes
from apps.api.app.agent_rsi_routes import install_agent_rsi_routes
from apps.api.app.alert_routes import install_alert_routes
from apps.api.app.approval_routes import install_approval_routes
from apps.api.app.cost_routes import install_cost_routes
from apps.api.app.demonstrations import DemonstrationService, install_demonstration_routes
from apps.api.app.device_host import DeviceHost
from apps.api.app.evaluation_recordings import EvaluationRecordingRepository
from apps.api.app.evaluations import EvaluationService, install_evaluation_routes
from apps.api.app.history_routes import install_history_routes
from apps.api.app.inbox_routes import install_inbox_routes
from apps.api.app.info_routes import actor_from_headers, install_info_routes
from apps.api.app.job_routes import install_job_routes
from apps.api.app.library_routes import install_library_routes
from apps.api.app.library_validation import program_schemas
from apps.api.app.live_routes import install_live_routes
from apps.api.app.notebook_routes import install_notebook_routes
from apps.api.app.notebook_service import NotebookService
from apps.api.app.plan_routes import install_plan_routes
from apps.api.app.platform_monitor import PlatformMonitor
from apps.api.app.policy_routes import install_policy_routes
from apps.api.app.program_routes import install_program_routes
from apps.api.app.recordings import install_recording_routes
from apps.api.app.resource_routes import install_resource_routes
from apps.api.app.robot_resource_routes import install_robot_resource_routes
from apps.api.app.runtime_routes import install_runtime_routes
from apps.api.app.session_manager import SessionManager, default_source_factory, encode
from apps.api.app.settings import Settings
from apps.api.app.stream_broadcaster import ClientConnection
from apps.api.app.structured_logging import configure_gateway_logging
from apps.api.app.task_routes import install_task_routes
from packages.acquisition import AcquisitionService
from packages.agent_evaluation.scope import is_internal_observation
from packages.assets.registry import AssetRegistry
from packages.catalog import AssetCatalog
from packages.catalog.sync import CatalogSync
from packages.info.invocations import HistoryUnavailable
from packages.info.plans import PlanError
from packages.library.authoring import AuthoringReports
from packages.library.common import REPO_ROOT
from packages.library.deletions import LibraryDeletions
from packages.library.description_views import description_view
from packages.library.previews import PreviewRepository
from packages.library.removals import LibraryRemovals
from packages.library.scenes import LibraryError
from packages.library.validation import StructuralValidator
from packages.live.bindings import load_bindings
from packages.protocol.internal import SessionOptions
from packages.protocol.schema import (
    PROTOCOL_MINOR_VERSION,
    PROTOCOL_VERSION,
    ActionApprove,
    ActionCancel,
    ActionRequest,
    ApprovalPolicy,
    ClientMessage,
    Command,
    Model,
    NonEmpty,
    Positive,
    Ready,
    ServerError,
    SessionSeed,
    Subscribe,
)
from packages.training.tensorboard import TensorBoardService
from packages.training.views import TrainingViews

CLIENT_MESSAGE = TypeAdapter(ClientMessage)


def _validation_diagnostics(value):
    if isinstance(value, float) and not math.isfinite(value):
        return f"<non-finite: {value}>"
    if isinstance(value, list):
        return [_validation_diagnostics(item) for item in value]
    if isinstance(value, dict):
        return {key: _validation_diagnostics(item) for key, item in value.items()}
    return value


class CreateSession(Model):
    requestId: NonEmpty | None = None
    seed: SessionSeed = 42
    sceneId: NonEmpty | None = None
    sceneVersion: NonEmpty | None = None
    source: Literal["synthetic", "newton"] | None = None
    realtimeFactor: Positive | None = 1.0
    continueWithoutObservers: StrictBool = True
    approval: ApprovalPolicy = ApprovalPolicy()
    controlHz: Positive = 60
    factHz: Positive = 10

    @model_validator(mode="after")
    def scene_selector(self):
        if (self.sceneId is None) == (self.sceneVersion is None):
            raise ValueError("Provide exactly one of sceneId or sceneVersion")
        return self


def _role(headers, settings: Settings):
    from packages.agent_evaluation.scope import current_scope
    if current_scope() is not None:
        return "operator"
    from packages.agent_rsi.scope import current_principal
    if current_principal() is not None:
        return "operator"
    if not settings.remote:
        return "operator"
    authorization = headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    # Headers are untrusted text; compare_digest(str, str) rejects non-ASCII.
    # Consistent UTF-8 bytes keep malformed credentials on the unauthorized path.
    token = token.encode("utf-8")
    if (
        token
        and settings.operator_token
        and hmac.compare_digest(token, settings.operator_token.encode("utf-8"))
    ):
        return "operator"
    if token and settings.viewer_token and hmac.compare_digest(token, settings.viewer_token.encode("utf-8")):
        return "viewer"
    return None


def create_app(
    settings: Settings | None = None,
    source_factory=default_source_factory,
    asset_registry: AssetRegistry | None = None,
    platform_sampler=None,
    live_transport_factory=None,
    catalog_path: Path | None = None,
    catalog_sync_source=None,
    catalog_sources=None,
    metadata_store=None,
    metadata_agent_profile=None,
    metadata_launch_workers=True,
    acquisition_runner=None,
    acquisition_limits=None,
    acquisition_disk_usage=None,
    acquisition_source_root: Path | None = None,
    observation_profiles=None,
    controller_promotion_qualification=None,
    controller_publication_validator=None,
) -> FastAPI:
    configure_gateway_logging()
    settings = settings or Settings()
    asset_registry = asset_registry or AssetRegistry.from_file()
    catalog = AssetCatalog(
        catalog_path
        if catalog_path is not None
        else Path(os.environ.get("LW_CATALOG", str(REPO_ROOT / "catalog/simready.json")))
    )
    recordings = EvaluationRecordingRepository(settings, asset_registry)
    previews = PreviewRepository(settings.scene_previews_root)
    manager = SessionManager(settings, source_factory, recordings)
    manager.plan_publication = asyncio.Lock()
    views = TrainingViews(manager, previews)
    tensorboard = TensorBoardService(manager)
    if settings.live_bindings is not None:
        manager.live = DeviceHost(
            load_bindings(settings.live_bindings), manager.approvals, manager.plans,
            approval_timeout=settings.approval_timeout_seconds,
            recordings=recordings,
            inbox=manager.inbox,
            layout_resolver=manager.library.resolve,
            **({"transport_factory": live_transport_factory} if live_transport_factory else {}),
        )
    evaluations = EvaluationService(manager)
    manager.evaluations = evaluations
    notebooks = NotebookService(manager)
    demonstrations = DemonstrationService(manager)
    manager.notebooks = notebooks
    manager.training_plans.additional_in_use = notebooks.plan_in_use
    library_assets = manager.library_assets
    manager.library_deletions = LibraryDeletions(
        library_assets, lambda: manager.library, manager.approvals, recordings,
        manager.platform_sessions, StructuralValidator(settings.state_root, program_schemas),
        live=lambda: manager.live,
        suites=evaluations.suites,
        tasks=manager.tasks,
        jobs=manager.jobs.reference_snapshot,
    )
    manager.library_removals = LibraryRemovals(manager.library_deletions)
    monitor = PlatformMonitor(manager, sampler=platform_sampler)
    acquisitions = AcquisitionService(
        settings.state_root,
        library_assets.roots["user"],
        library_assets,
        catalog,
        runner=acquisition_runner,
        limits=acquisition_limits,
        disk_usage=acquisition_disk_usage,
        source_root=(
            acquisition_source_root
            or (
                Path(os.environ["SPATIAL_ACQUISITION_SOURCE_ROOT"])
                if os.environ.get("SPATIAL_ACQUISITION_SOURCE_ROOT")
                else None
            )
        ),
    )
    authoring = AuthoringReports(
        settings.state_root, library_assets, lambda: manager.library
    )
    from packages.catalog.resources import CatalogResources
    from packages.catalog.sources import CatalogSources

    sources = CatalogSources(catalog_sources, simready_factory=catalog_sync_source)
    catalog.resources = CatalogResources(settings.state_root, sources)
    catalog_sync = CatalogSync(settings.state_root, catalog, sources=sources)
    from packages.catalog_metadata import MetadataError, MetadataStore
    from packages.catalog_metadata.maintenance import MetadataMaintenance

    metadata_store = metadata_store or MetadataStore(settings.state_root / "catalog-metadata")
    metadata_maintenance = MetadataMaintenance(
        metadata_store, agent_profile=metadata_agent_profile, launch_workers=metadata_launch_workers,
    )

    @asynccontextmanager
    async def lifespan(app):
        try:
            catalog.resources.limiter = anyio.CapacityLimiter(8)
            await manager.startup()
            await asyncio.to_thread(runtime_records.startup)
            # Resolve the operator's roots at startup, before any owned workers or recordings.
            await asyncio.to_thread(manager.library_deletions.recover)
            await asyncio.to_thread(manager.library_removals.recover)
            manager.library.refresh()
            library_assets.startup()
            await asyncio.to_thread(catalog.startup)
            await asyncio.to_thread(catalog_sync.startup)
            await metadata_maintenance.startup()
            catalog.attach_metadata(metadata_store)
            metadata_maintenance.release_preparer = catalog.prepare_metadata_release
            await acquisitions.startup()
            await recordings.startup()
            await evaluations.startup()
            await asyncio.to_thread(agent_rsi.startup)
            await asyncio.to_thread(notebooks.startup)
            await asyncio.to_thread(controller_promotions.recover)
            from packages.agent_rsi.contamination import recover
            from packages.agent_rsi.promotions import recover as recover_promotions

            await recover(agent_rsi)
            await recover_promotions(agent_rsi)
            await monitor.start()
            await views.start()
            if manager.live:
                await manager.live.start()
            yield
        finally:
            await manager.library_removals.close()
            try:
                try:
                    try:
                        await metadata_maintenance.close()
                    finally:
                        metadata_store.close()
                        catalog.resources.close()
                        await catalog_sync.close()
                finally:
                    await acquisitions.close()
            finally:
                try:
                    try:
                        await views.close()
                    finally:
                        try:
                            await tensorboard.close()
                        finally:
                            await monitor.close()
                finally:
                    try:
                        if manager.live:
                            await manager.live.close()
                    finally:
                        try:
                            await evaluations.close()
                        finally:
                            await manager.close()

    app = FastAPI(title="Newton Spatial Gateway", version="2", lifespan=lifespan)
    app.state.tensorboard = tensorboard

    @app.post("/api/v1/tensorboard")
    async def start_tensorboard(request: Request):
        if _role(request.headers, settings) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return await tensorboard.start()

    @app.exception_handler(LibraryError)
    async def library_error(request, error):
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    @app.exception_handler(MetadataError)
    async def metadata_error(request, error):
        return JSONResponse(
            {"detail": {"code": error.code, "message": error.message, **error.details}},
            status_code=error.status,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        acquisition = reject_invalid_acquisition(request, error)
        if acquisition is not None:
            return acquisition
        if request.url.path.startswith("/api/v1/runtime-sessions"):
            return JSONResponse(
                {"detail": {"code": "invalid_runtime_import", "message": "Runtime import is invalid"}},
                status_code=422,
            )
        if request.url.path.startswith(("/api/v1/models", "/api/v1/prices", "/api/v1/vault")):
            # These endpoints never echo rejected input (including accidental credential fields).
            return JSONResponse(
                {"detail": {"code": "invalid_resource", "message": "Resource metadata is invalid"}},
                status_code=422,
            )
        # A valid JSON numeric literal can overflow the parser's float range.
        # Its diagnostic input must itself remain valid JSON in the 422 response.
        return JSONResponse(
            {"detail": _validation_diagnostics(jsonable_encoder(error.errors()))}, status_code=422
        )

    app.state.manager, app.state.settings = manager, settings
    app.state.platform_monitor = monitor
    app.state.library_assets = library_assets
    app.state.catalog = catalog
    app.state.catalog_sync = catalog_sync
    app.state.metadata_store = metadata_store
    app.state.metadata_maintenance = metadata_maintenance
    app.state.acquisitions = acquisitions
    app.state.authoring_reports = authoring
    app.state.training_views = views
    app.state.notebooks = notebooks
    app.state.demonstrations = demonstrations
    install_info_routes(app, manager, lambda headers: _role(headers, settings))
    install_live_routes(app, manager, settings, lambda headers: _role(headers, settings))

    install_evaluation_routes(app, evaluations, lambda headers: _role(headers, settings))
    install_notebook_routes(app, notebooks, lambda headers: _role(headers, settings))
    install_demonstration_routes(app, demonstrations, lambda headers: _role(headers, settings))
    install_agent_routes(app, library_assets.roots, lambda headers: _role(headers, settings))
    runtime_records = install_runtime_routes(
        app, manager, app.state.agents, lambda headers: _role(headers, settings)
    )
    external_evaluations = install_external_evaluations(
        app, evaluations, app.state.agents, runtime_records, lambda headers: _role(headers, settings)
    )
    agent_rsi = install_agent_rsi_routes(
        app, manager, app.state.agents, external_evaluations, lambda headers: _role(headers, settings)
    )
    install_approval_routes(app, manager, lambda headers: _role(headers, settings))
    install_plan_routes(app, manager, lambda headers: _role(headers, settings))
    install_job_routes(app, manager, lambda headers: _role(headers, settings), views)
    install_task_routes(app, manager.tasks, lambda headers: _role(headers, settings))
    install_policy_routes(
        app, manager, lambda headers: _role(headers, settings), profiles=observation_profiles
    )
    install_program_routes(app, manager, lambda headers: _role(headers, settings))
    install_alert_routes(app, manager, lambda headers: _role(headers, settings))
    install_recording_routes(app, recordings)
    install_resource_routes(app, settings, lambda headers: _role(headers, settings))
    install_robot_resource_routes(app, library_assets, manager.library, manager)
    from apps.api.app.controller_promotion_routes import install_controller_promotion_routes

    controller_promotions = install_controller_promotion_routes(
        app, manager, app.state.robot_resources, lambda headers: _role(headers, settings),
        qualification=controller_promotion_qualification,
        publication_validator=controller_publication_validator,
    )
    install_cost_routes(app, manager, lambda headers: _role(headers, settings))
    install_history_routes(app, manager, recordings)
    install_inbox_routes(app, manager)
    install_library_routes(app, library_assets, lambda: manager.library, manager,
                           lambda headers: _role(headers, settings), catalog, authoring)
    from apps.api.app.catalog_sync_routes import install_catalog_sync_routes
    install_catalog_sync_routes(app, catalog_sync, lambda headers: _role(headers, settings))
    from apps.api.app.catalog_metadata_routes import install_catalog_metadata_routes
    install_catalog_metadata_routes(app, metadata_maintenance, lambda headers: _role(headers, settings))
    install_acquisition_routes(app, acquisitions)
    request_times = defaultdict(deque)

    async def access_control(request: Request, call_next):
        if app.state.metadata_worker_auth(request):
            return await call_next(request)
        if (manager.jobs.authenticated_node(request.headers) and re.fullmatch(
            r"/api/v1/(?:nodes/local/heartbeat|jobs/job-[a-f0-9]{32}/(?:inputs(?:/packages/[0-9]+)?|artifacts))",
            request.url.path,
        )):
            return await call_next(request)
        if (
            settings.remote
            and request.url.path.startswith(("/api/v1", "/recordings/"))
            and request.url.path != "/api/v1/health"
        ):
            origin = request.headers.get("origin")
            if origin is not None and origin not in settings.allowed_origins:
                return JSONResponse(
                    {"detail": {"code": "unauthorized", "message": "Origin is not allowed"}}, status_code=403
                )
            role = _role(request.headers, settings)
            if role is None:
                return JSONResponse({"detail": {"code": "unauthorized"}}, status_code=401)
            if (
                request.method in ("POST", "DELETE", "PUT", "PATCH")
                and role != "operator"
                and not request.url.path.endswith("/actions")
            ):
                return JSONResponse({"detail": {"code": "unauthorized"}}, status_code=403)
            if not is_internal_observation(request):
                now = time.monotonic()
                key = request.client.host if request.client else "unknown"
                times = request_times[key]
                while times and now - times[0] > 60:
                    times.popleft()
                if len(times) >= settings.remote_requests_per_minute:
                    return JSONResponse({"detail": {"code": "rate_limited"}}, status_code=429)
                times.append(now)
        return await call_next(request)

    if settings.remote:
        app.middleware("http")(access_control)

    class EvaluatorOwnership:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            # Direct ASGI delegation preserves the request's existing cancellation/cleanup semantics.
            if scope["type"] == "http":
                request = Request(scope)
                if (request.method in ("POST", "DELETE", "PUT", "PATCH")
                        and _role(request.headers, settings) is not None):
                    parts = request.url.path.split("/")
                    if len(parts) >= 5 and parts[1:4] == ["api", "v1", "sessions"]:
                        session = manager.sessions.get(parts[4])
                        if session is not None:
                            try:
                                session.require_interactive()
                                evaluations.require_interactive()
                            except HTTPException as error:
                                response = JSONResponse(
                                    {"detail": error.detail}, status_code=error.status_code,
                                )
                                await response(scope, receive, send)
                                return
            await self.app(scope, receive, send)

    app.add_middleware(EvaluatorOwnership)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-None-Match", "X-Actor"],
    )

    @app.get("/api/v1/health")
    async def health():
        from packages.info.evaluation_privacy import visible_session

        sessions = [session for session in manager.sessions.values() if visible_session(manager, session)]
        return {
            "status": "ok",
            "protocolVersion": PROTOCOL_VERSION,
            "protocolMinorVersion": PROTOCOL_MINOR_VERSION,
            "sessionCount": len(sessions),
            "live": {"enabled": manager.live is not None,
                     "state": manager.live.state if manager.live else "DISABLED"},
            "sessions": [
                {"sessionId": s.session_id, "state": s.state, "source": s.kind,
                 **({"evaluationId": s.evaluation_id} if s.evaluation_id else {})}
                for s in sessions
            ],
            "evaluation": evaluations.status(),
        }

    @app.get("/api/v1/scenes")
    async def scenes():
        return {"scenes": await asyncio.to_thread(manager.library.list)}

    @app.get("/api/v1/library/scenes")
    async def library_scenes(
        q: str | None = None, launchable: bool | None = None,
        source: Literal["synthetic", "newton"] | None = None,
        view: Literal["full", "summary"] = "full", limit: int | None = None,
        cursor: str | None = None,
    ):
        if view == "summary" or limit is not None or cursor is not None:
            return await asyncio.to_thread(manager.library.page, q, launchable, source, limit, cursor)
        def full_list():
            result = []
            for value in manager.library.list(q, launchable):
                if source is not None and source not in value["sources"]:
                    continue
                version = manager.library.resolve(value["id"])
                preview = previews.describe(version, source or value["sources"][0])
                result.append(value | {"preview": preview, "entityCount": preview["entityCount"],
                                       "deviceCount": preview["bindingCount"],
                                       "countsReason": preview["reason"]})
            return {"scenes": result}
        return await asyncio.to_thread(full_list)

    @app.get("/api/v1/library/scenes/{name}")
    @app.get("/api/v1/library/scenes/{name}/{rev}")
    async def library_scene(
        name: str, rev: str | None = None, source: Literal["synthetic", "newton"] | None = None,
        view: str = "full", section: str | None = None, offset: str = "0",
        documentDigest: str | None = None,
    ):
        selector = f"{name}@{rev}" if rev is not None else name
        detail = await asyncio.to_thread(manager.library.describe, selector)
        version = await asyncio.to_thread(manager.library.resolve, detail["id"])
        preview = await asyncio.to_thread(previews.describe, version, source or detail["sources"][0])
        detail.update(
            preview=preview, entityCount=preview["entityCount"], deviceCount=preview["bindingCount"],
            countsReason=preview["reason"],
        )
        related = await asyncio.to_thread(recordings.list)
        detail["recordings"] = [item for item in related if item.get("sceneVersion") == detail["id"]]
        def version_summaries():
            values = []
            for candidate in manager.library.versions.values():
                if candidate.name != name:
                    continue
                value = manager.library.resolved(candidate).summary()
                values.append({
                    key: value[key]
                    for key in ("id", "rev", "contentHash", "launchable", "launchReason",
                                "validationStatus")
                })
            return sorted(values, key=lambda item: item["rev"], reverse=True)

        detail["versions"] = await asyncio.to_thread(version_summaries)
        return description_view(detail, "scene", selector, view=view, section=section, offset=offset,
                                document_digest=documentDigest)

    @app.get("/api/v1/library/scenes/{name}/{rev}/preview/{source}/{relative:path}")
    async def library_preview_file(name: str, rev: str, source: str, relative: str):
        version = await asyncio.to_thread(manager.library.resolve, f"{name}@{rev}")
        try:
            payload = await asyncio.to_thread(previews.read, version, source, relative)
        except (OSError, ValueError) as exc:
            raise HTTPException(404, {"code": "preview_unavailable", "message": str(exc)}) from exc
        media = {".json": "application/json", ".bin": "application/octet-stream", ".glb": "model/gltf-binary",
                 ".gltf": "model/gltf+json", ".png": "image/png", ".svg": "image/svg+xml"}
        return Response(payload, media_type=media.get(Path(relative).suffix, "application/octet-stream"))

    @app.get("/api/v1/platform")
    async def platform():
        return JSONResponse(monitor.snapshot, headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(body: CreateSession):
        result, status = await evaluations.create_session(
            body.sceneId,
            body.source if body.source is not None else ("synthetic" if body.sceneId else None),
            SessionOptions(
                seed=body.seed,
                realtimeFactor=body.realtimeFactor,
                continueWithoutObservers=body.continueWithoutObservers,
                approval=body.approval,
                controlHz=body.controlHz,
                factHz=body.factHz,
            ),
            scene_version=body.sceneVersion,
            request_id=body.requestId,
        )
        return JSONResponse(result, status_code=status, headers=(
            {"Location": f"/api/v1/session-requests/{result['requestId']}"} if status == 202 else {}
        ))

    @app.get("/api/v1/sessions/{session_id}")
    async def session_summary(session_id: str):
        return manager.get(session_id).summary()

    @app.post("/api/v1/sessions/{session_id}/viewer")
    async def open_viewer(session_id: str):
        if settings.remote:
            raise HTTPException(403, {
                "code": "viewer_local_only", "message": "Native Viewer is available only on the gateway host"
            })
        return await manager.get(session_id).open_viewer()

    @app.post("/api/v1/sessions/{session_id}/commands")
    async def session_command(session_id: str, body: Command, request: Request):
        session = manager.get(session_id)
        replies = []
        # The HTTP caller is an acknowledgement sink, not a stream observer.
        caller = SimpleNamespace(
            role=_role(request.headers, settings),
            enqueue_reliable=lambda payload: replies.append(json.loads(payload)),
        )
        await session.execute(caller, body)
        return {"ack": replies[-1], "state": session.summary()}

    @app.get("/api/v1/sessions/{session_id}/scene")
    async def scene(session_id: str, request: Request):
        value = manager.get(session_id).scene()
        etag = '"' + value.sceneRevision + '"'
        if request.headers.get("if-none-match") in (etag, value.sceneRevision):
            return Response(status_code=304, headers={"ETag": etag})
        return Response(
            content=encode(value),
            media_type="application/json",
            headers={"ETag": etag, "Cache-Control": "no-cache"},
        )

    @app.get("/api/v1/sessions/{session_id}/snapshot")
    async def snapshot(session_id: str):
        frame = await manager.get(session_id).snapshot()
        return Response(
            content=encode(frame.model_copy(update={"type": "stream.snapshot"})),
            media_type="application/json",
        )

    @app.get("/api/v1/sessions/{session_id}/pose")
    async def pose(session_id: str, key: str):
        return await manager.get(session_id).pose(key)

    @app.get("/api/v1/sessions/{session_id}/joints/state")
    async def joint_state(session_id: str, key: str):
        session = manager.get(session_id)
        if key not in {joint.entityKey for joint in session.scene().joints}:
            raise HTTPException(404, {"code": "unknown_entity"})
        if session.source is None or session.state == "FAILED":
            raise HTTPException(503, session.error or {"code": "source_unavailable"})
        try:
            value = await asyncio.wait_for(session.source.query_joint_state(key), timeout=0.2)
        except TimeoutError as exc:
            raise HTTPException(504, {"code": "source_timeout"}) from exc
        except KeyError as exc:
            raise HTTPException(404, {"code": "unknown_entity"}) from exc
        return Response(content=encode(value), media_type="application/json")

    @app.get("/api/v1/sessions/{session_id}/stats")
    async def stats(session_id: str):
        return manager.get(session_id).stats()

    @app.get("/api/v1/sessions/{session_id}/meshes/{mesh_id}")
    async def mesh(session_id: str, mesh_id: str, request: Request):
        session = manager.get(session_id)
        registered = {
            visual.meshId
            for node in session.scene().nodes
            if node.kind != "group"
            for visual in [*node.visuals, *(node.fallbackVisuals or []), *node.colliders]
            if visual.kind == "mesh"
        }
        if mesh_id not in registered:
            raise HTTPException(404, {"code": "asset_missing"})
        etag = '"' + mesh_id + '"'
        cache_headers = {"Cache-Control": "public,max-age=31536000,immutable", "ETag": etag}
        if request.headers.get("if-none-match") == etag:
            return Response(status_code=304, headers=cache_headers)
        if session.source is None or session.state == "FAILED":
            raise HTTPException(503, session.error or {"code": "source_unavailable"})
        try:
            data = await session.source.read_mesh(mesh_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "asset_missing"}) from exc
        return Response(
            content=data,
            media_type="model/gltf-binary",
            headers=cache_headers,
        )

    @app.get("/api/v1/assets/{asset_id:path}")
    async def asset(asset_id: str, request: Request):
        try:
            value = asset_registry.read(asset_id)
        except KeyError as exc:
            raise HTTPException(404, {"code": "asset_missing"}) from exc
        headers = {"Cache-Control": "public,max-age=3600", "ETag": value.etag}
        if request.headers.get("if-none-match") == value.etag:
            return Response(status_code=304, headers=headers)
        return Response(content=value.data, media_type=value.media_type, headers=headers)

    @app.post("/api/v1/sessions/{session_id}/reload")
    async def reload_scene(session_id: str):
        session = await manager.reload(session_id)
        return session.summary()

    @app.post("/api/v1/sessions/{session_id}/restart")
    async def restart(session_id: str):
        session = manager.get(session_id)
        if session.state != "FAILED":
            raise HTTPException(409, {"code": "invalid_state"})
        await manager.reload(session_id)
        return session.summary()

    @app.delete("/api/v1/sessions/{session_id}", status_code=204)
    async def close_session(session_id: str):
        await manager.get(session_id).close()
        return Response(status_code=204)

    @app.websocket("/api/v1/sessions/{session_id}/ws")
    async def websocket(websocket: WebSocket, session_id: str):
        role = _role(websocket.headers, settings)
        origin = websocket.headers.get("origin")
        if (
            role is None
            or (origin is not None and origin not in settings.allowed_origins)
            or (settings.remote and origin is None)
        ):
            await websocket.close(code=1008)
            return
        try:
            session = manager.get(session_id)
        except HTTPException:
            await websocket.close(code=1008)
            return
        if session.manifest is None:
            # Ready requires a real revision; do not invent one before first startup.
            await websocket.close(code=1013)
            return
        await websocket.accept()
        connection = ClientConnection(websocket, settings, role)
        session.clients.add(connection)
        connection.start()
        summary = session.summary()
        connection.enqueue_reliable(
            encode(
                Ready(
                    role=role,
                    sessionId=session_id,
                    sceneRevision=summary["sceneRevision"],
                    revisionNumericId=summary["revisionNumericId"],
                    epoch=summary["epoch"],
                    sessionState=session.state,
                    simTime=summary["simTime"],
                    basePublishHz=session.options.basePublishHz,
                    realtimeFactor=session.options.realtimeFactor,
                    heartbeatIntervalMs=settings.heartbeat_interval_seconds * 1000,
                    sampleTimeClock=session.manifest.source.sampleTimeClock,
                )
            )
        )

        async def receive():
            commands = deque()
            while True:
                raw = await websocket.receive_text()
                if len(raw) > 16_384:
                    connection.request_close(1009)
                    return
                try:
                    message = CLIENT_MESSAGE.validate_json(raw)
                except ValidationError:
                    connection.enqueue_reliable(
                        encode(
                            ServerError(code="invalid_message", message="Message does not match protocol v2")
                        )
                    )
                    continue
                if settings.remote and isinstance(
                    message, (Command, ActionRequest, ActionCancel, ActionApprove)
                ):
                    now = time.monotonic()
                    while commands and now - commands[0] > 60:
                        commands.popleft()
                    if len(commands) >= 60:
                        connection.enqueue_reliable(
                            encode(
                                ServerError(
                                    code="invalid_message",
                                    message="Mutation rate limit exceeded",
                                    requestId=message.requestId,
                                )
                            )
                        )
                        continue
                    commands.append(now)
                if isinstance(message, (Command, ActionRequest, ActionCancel, ActionApprove)):
                    try:
                        session.require_interactive()
                        evaluations.require_interactive()
                    except HTTPException:
                        connection.enqueue_reliable(encode(ServerError(
                            code="invalid_message",
                            message="evaluation_busy: This session is evaluator-owned and read-only",
                            requestId=message.requestId,
                        )))
                        continue
                if isinstance(message, Subscribe):
                    await session.observer_subscribe(connection, message)
                elif isinstance(message, (ActionRequest, ActionCancel, ActionApprove)):
                    try:
                        actor = actor_from_headers(websocket.headers)
                        pipeline = await session.action_pipeline()
                        if isinstance(message, ActionRequest):
                            await pipeline.invoke(message, role, actor)
                        elif isinstance(message, ActionCancel):
                            await pipeline.cancel(message.invocationId, role)
                        else:
                            await pipeline.approve(message.invocationId, message.decision, role, actor)
                    except HistoryUnavailable as exc:
                        connection.enqueue_reliable(
                            encode(
                                ServerError(
                                    code="history_unavailable",
                                    message=str(exc),
                                    requestId=message.requestId,
                                    invocationId=exc.invocation_id,
                                )
                            )
                        )
                    except (PermissionError, KeyError, HTTPException, PlanError) as exc:
                        connection.enqueue_reliable(
                            encode(
                                ServerError(
                                    code="unauthorized"
                                    if isinstance(exc, PermissionError)
                                    else "invalid_message",
                                    message=str(exc),
                                    requestId=message.requestId,
                                )
                            )
                        )
                elif isinstance(message, Command):
                    await session.execute(connection, message)

        receiver = asyncio.create_task(receive(), name="websocket-receiver")
        try:
            done, _ = await asyncio.wait({receiver, connection.task}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                    task.result()
        finally:
            # ASGI cancellation must not abandon a pump after removing its owner.
            # finish() retains its existing bounded WebSocket close timeout.
            with anyio.CancelScope(shield=True):
                receiver.cancel()
                try:
                    with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                        await receiver
                finally:
                    try:
                        await session.remove_client(connection)
                    finally:
                        await connection.finish()

    from packages.agent_evaluation.scope import CaseAuthority
    app.add_middleware(CaseAuthority, service=external_evaluations)
    from packages.agent_rsi.scope import WorkflowAuthority
    app.add_middleware(WorkflowAuthority, service=agent_rsi)
    return app


app = create_app(
    Settings(
        remote=os.getenv("SPATIAL_REMOTE") == "1",
        operator_token=os.getenv("SPATIAL_OPERATOR_TOKEN"),
        viewer_token=os.getenv("SPATIAL_VIEWER_TOKEN"),
        **(
            {"allowed_origins": tuple(os.environ["SPATIAL_ALLOWED_ORIGINS"].split(","))}
            if os.getenv("SPATIAL_ALLOWED_ORIGINS")
            else {}
        ),
    )
)

"""HTTP/WS control plane. Physics adapters are loaded only when explicitly selected."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import StrictBool, TypeAdapter, ValidationError

from apps.api.app.session_manager import SCENES, SessionManager, default_source_factory, encode
from apps.api.app.settings import Settings
from apps.api.app.stream_broadcaster import ClientConnection
from packages.assets.registry import AssetRegistry
from packages.protocol.internal import SessionOptions
from packages.protocol.schema import (
    ClientMessage,
    Command,
    Model,
    NonEmpty,
    Positive,
    Ready,
    ServerError,
    Subscribe,
)

CLIENT_MESSAGE = TypeAdapter(ClientMessage)


class CreateSession(Model):
    sceneId: NonEmpty
    source: Literal["synthetic", "newton"] = "synthetic"
    realtimeFactor: Positive | None = 1.0
    continueWithoutObservers: StrictBool = True


def _role(headers, settings: Settings):
    if not settings.remote:
        return "operator"
    authorization = headers.get("authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else ""
    if token and settings.operator_token and hmac.compare_digest(token, settings.operator_token):
        return "operator"
    if token and settings.viewer_token and hmac.compare_digest(token, settings.viewer_token):
        return "viewer"
    return None


def create_app(
    settings: Settings | None = None,
    source_factory=default_source_factory,
    asset_registry: AssetRegistry | None = None,
) -> FastAPI:
    settings = settings or Settings()
    manager = SessionManager(settings, source_factory)
    asset_registry = asset_registry or AssetRegistry.from_file()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await manager.close()

    app = FastAPI(title="Newton Spatial Gateway", version="1", lifespan=lifespan)
    app.state.manager, app.state.settings = manager, settings
    request_times = defaultdict(deque)

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        if (
            settings.remote
            and request.url.path.startswith("/api/v1")
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
            if request.method in ("POST", "DELETE", "PUT", "PATCH") and role != "operator":
                return JSONResponse({"detail": {"code": "unauthorized"}}, status_code=403)
            now = time.monotonic()
            key = request.client.host if request.client else "unknown"
            times = request_times[key]
            while times and now - times[0] > 60:
                times.popleft()
            if len(times) >= settings.remote_requests_per_minute:
                return JSONResponse({"detail": {"code": "rate_limited"}}, status_code=429)
            times.append(now)
        return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-None-Match"],
    )

    @app.get("/api/v1/health")
    async def health():
        return {
            "status": "ok",
            "protocolVersion": 1,
            "sessionCount": len(manager.sessions),
            "sessions": [
                {"sessionId": s.session_id, "state": s.state, "source": s.kind}
                for s in manager.sessions.values()
            ],
        }

    @app.get("/api/v1/scenes")
    async def scenes():
        return {"scenes": SCENES}

    @app.post("/api/v1/sessions", status_code=201)
    async def create_session(body: CreateSession):
        session = await manager.create(
            body.sceneId,
            body.source,
            SessionOptions(
                realtimeFactor=body.realtimeFactor,
                continueWithoutObservers=body.continueWithoutObservers,
            ),
        )
        return session.summary()

    @app.get("/api/v1/sessions/{session_id}")
    async def session_summary(session_id: str):
        return manager.get(session_id).summary()

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
        frame = manager.get(session_id).latest_frame()
        if frame is None:
            raise HTTPException(503, {"code": "session_not_ready"})
        return Response(
            content=encode(frame.model_copy(update={"type": "stream.snapshot"})),
            media_type="application/json",
        )

    @app.get("/api/v1/sessions/{session_id}/pose")
    async def pose(session_id: str, key: str):
        return manager.get(session_id).pose(key)

    @app.get("/api/v1/sessions/{session_id}/joints/state")
    async def joint_state(session_id: str, key: str):
        session = manager.get(session_id)
        if key not in {joint.entityKey for joint in session.scene().joints}:
            raise HTTPException(404, {"code": "unknown_entity"})
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
        session = manager.get(session_id)
        await session.reload()
        return session.summary()

    @app.post("/api/v1/sessions/{session_id}/restart")
    async def restart(session_id: str):
        session = manager.get(session_id)
        if session.state != "FAILED":
            raise HTTPException(409, {"code": "invalid_state"})
        await session.reload()
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
        await websocket.accept()
        connection = ClientConnection(websocket, settings, role)
        session.clients.add(connection)
        connection.start()
        summary = session.summary()
        connection.enqueue_reliable(
            encode(
                Ready(
                    sessionId=session_id,
                    sceneRevision=summary["sceneRevision"],
                    revisionNumericId=summary["revisionNumericId"],
                    epoch=summary["epoch"],
                    sessionState=session.state,
                    simTime=summary["simTime"],
                    basePublishHz=session.options.basePublishHz,
                    realtimeFactor=session.options.realtimeFactor,
                    heartbeatIntervalMs=settings.heartbeat_interval_seconds * 1000,
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
                            ServerError(code="invalid_message", message="Message does not match protocol v1")
                        )
                    )
                    continue
                if isinstance(message, Subscribe):
                    await session.observer_subscribe(connection, message)
                elif isinstance(message, Command):
                    if settings.remote:
                        now = time.monotonic()
                        while commands and now - commands[0] > 60:
                            commands.popleft()
                        if len(commands) >= 60:
                            connection.enqueue_reliable(
                                encode(
                                    ServerError(code="invalid_message", message="Command rate limit exceeded")
                                )
                            )
                            continue
                        commands.append(now)
                    await session.execute(connection, message)

        receiver = asyncio.create_task(receive(), name="websocket-receiver")
        try:
            done, _ = await asyncio.wait({receiver, connection.task}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                    task.result()
        finally:
            receiver.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                await receiver
            await asyncio.shield(session.remove_client(connection))
            await connection.finish()

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

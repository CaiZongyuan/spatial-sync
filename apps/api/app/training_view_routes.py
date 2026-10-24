"""Authenticated producer and read-only browser Job sockets; no Session lifecycle."""

import asyncio
import contextlib
from pathlib import Path

from fastapi import Response, WebSocket, WebSocketDisconnect

from packages.info.plans import PlanError
from packages.training.jobs import TERMINAL
from packages.training.views import Producer, Reader


def install_training_view_routes(app, manager, views, role_for, authenticate_node):
    @app.websocket("/api/v1/jobs/{identity}/view-frames")
    async def producer(websocket: WebSocket, identity: str):
        ticket = Producer()
        try:
            job = authenticate_node(websocket, identity)
            views._live(job)
        except PlanError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode()) > 256 * 1024:
                    raise PlanError("training_view_message_size", "View message exceeds its bounded size")
                job = authenticate_node(websocket, identity)
                await views.push(job, raw, ticket)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except PlanError as error:
            with contextlib.suppress(Exception):
                await websocket.send_json(views.envelope(job, "error", error.detail))
                await websocket.close(code=1008)
        finally:
            views.disconnect(identity, ticket)

    @app.websocket("/api/v1/jobs/{identity}/view")
    async def reader(websocket: WebSocket, identity: str):
        role = role_for(websocket.headers)
        origin = websocket.headers.get("origin")
        if (
            role is None
            or origin is not None
            and origin not in manager.settings.allowed_origins
            or manager.settings.remote
            and origin is None
        ):
            await websocket.close(code=1008)
            return
        try:
            job = manager.jobs.get(identity)
        except PlanError:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        if job["status"] != "running":
            await websocket.send_json(
                views.envelope(job, "ended", {"status": job["status"], "reason": job["reason"]})
            )
            await websocket.close(code=1000)
            return
        subscription = Reader(websocket)
        sender = asyncio.create_task(views.stream(job, subscription), name="job-view-reader")

        async def receive():
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return
                await websocket.close(code=1008)  # Browsers cannot produce/control this stream.
                return

        receiver = asyncio.create_task(receive(), name="job-view-readonly")
        try:
            await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass
        finally:
            for task in (sender, receiver):
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(sender, receiver, return_exceptions=True)

    @app.get("/api/v1/jobs/{identity}/view-assets/{digest}/{relative:path}")
    async def resource(identity: str, digest: str, relative: str):
        job = manager.jobs.get(identity)
        if job["status"] in TERMINAL:
            raise PlanError("training_view_asset_unavailable", "Job view is no longer active", status=404)
        payload = await asyncio.to_thread(views.descriptors.read, identity, digest, relative)
        media = {
            ".glb": "model/gltf-binary",
            ".gltf": "model/gltf+json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
        }
        return Response(
            payload,
            media_type=media.get(Path(relative).suffix, "application/octet-stream"),
            headers={"Cache-Control": "private, max-age=31536000, immutable"},
        )

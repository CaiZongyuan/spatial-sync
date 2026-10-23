"""Task source transport uses confined archives, never paths on the gateway host."""

import tempfile

import anyio
from fastapi import HTTPException, Request

from packages.library.archive import MAX_UPLOAD_BYTES, invalid


def install_task_routes(app, tasks, role_for):
    @app.get("/api/v1/tasks")
    async def task_list():
        return {"tasks": await anyio.to_thread.run_sync(tasks.list)}

    @app.get("/api/v1/tasks/{name}/{rev}")
    async def task_show(name: str, rev: str):
        return await anyio.to_thread.run_sync(tasks.show, f"task/{name}@{rev}")

    @app.post("/api/v1/tasks", status_code=201)
    async def task_push(request: Request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        if request.headers.get("content-type", "").split(";", 1)[0] not in (
            "application/x-tar", "application/octet-stream",
        ):
            raise invalid("Upload Task tar bytes; gateway host paths are not accepted")
        with tempfile.TemporaryFile() as stream:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise invalid("Task upload exceeds 128 MiB")
                stream.write(chunk)
            stream.seek(0)
            return await anyio.to_thread.run_sync(tasks.push, stream)

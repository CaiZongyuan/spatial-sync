"""Program package content remains inert metadata in the gateway."""

import tempfile

import anyio
from fastapi import HTTPException, Request, Response

from apps.api.app.info_routes import actor_from_headers
from packages.library.common import LibraryError
from packages.programs.library import ProgramLibrary, invalid

MAX_UPLOAD_BYTES = 16 * 1024 * 1024


def install_program_routes(app, manager, role_for):
    library = ProgramLibrary(manager.library_assets.roots)
    manager.programs = app.state.programs = library
    manager.training_plans.programs = library

    def document(record):
        associations = []
        history, _ = manager.training_plans.snapshot()
        for stored in history:
            intent = stored.plan.program
            if (
                intent is not None
                and intent.id == record["id"]
                and intent.contentHash == record["contentHash"]
            ):
                plan = manager.training_plans.document(stored)
                associations.append(
                    {
                        "id": plan["id"],
                        "revision": plan["revision"],
                        "approvalId": plan["approvalId"],
                        "status": plan["status"],
                    }
                )
        return record | {"plans": sorted(associations, key=lambda value: (value["id"], value["revision"]))}

    async def receive(request, *, inspect=False):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        if request.headers.get("content-type", "").split(";", 1)[0] not in (
            "application/x-tar",
            "application/octet-stream",
        ):
            raise invalid("Upload program tar bytes; gateway host paths are not accepted")
        with tempfile.TemporaryFile() as stream:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise invalid("Program upload exceeds 16 MiB")
                stream.write(chunk)
            stream.seek(0)
            if inspect:
                return await anyio.to_thread.run_sync(library.inspect, stream)
            record = await anyio.to_thread.run_sync(library.push, stream, actor_from_headers(request.headers))
            return document(record)

    @app.post("/api/v1/programs/inspect")
    async def inspect_program(request: Request):
        return await receive(request, inspect=True)

    @app.post("/api/v1/programs", status_code=201)
    async def push(request: Request):
        return await receive(request)

    @app.get("/api/v1/programs")
    async def listing():
        return {"programs": [document(value) for value in await anyio.to_thread.run_sync(library.list)]}

    @app.get("/api/v1/programs/{name}/{revision}")
    async def show(name: str, revision: str):
        return document(
            await anyio.to_thread.run_sync(library.show, f"program/robot_arm.script.{name}@{revision}")
        )

    @app.get("/api/v1/programs/{name}/{revision}/archive")
    async def archive(name: str, revision: str):
        content = await anyio.to_thread.run_sync(
            library.export, f"program/robot_arm.script.{name}@{revision}"
        )
        return Response(content, media_type="application/x-tar")

    @app.get("/api/v1/programs/{name}/{revision}/files/{filename:path}")
    async def source(name: str, revision: str, filename: str):
        def read():
            record, package = library.resolve(f"program/robot_arm.script.{name}@{revision}")
            if filename not in record["files"]:
                raise LibraryError("unknown_program_file", "Unknown program source file", 404)
            return (package / filename).read_bytes()

        content = await anyio.to_thread.run_sync(read)
        return Response(content, media_type="text/plain", headers={"X-Content-Type-Options": "nosniff"})

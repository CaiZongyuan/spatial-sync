"""Public Agent metadata and confined credential-free Harness transport."""

import anyio
from fastapi import HTTPException, Request
from fastapi.responses import Response

from packages.library.agents import MAX_HARNESS_BYTES, AgentLibrary, invalid_agent


def install_agent_routes(app, roots, role_for):
    agents = AgentLibrary(roots)
    app.state.agents = agents

    def operator(request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})

    def integer(value):
        if not value.isascii() or not value.isdecimal() or len(value) > 9 or int(value) < 1:
            raise invalid_agent("unknown_harness_version", 404)
        return int(value)

    async def upload(request, *, optional=False):
        content_type = request.headers.get("content-type", "").split(";", 1)[0]
        if content_type not in ("application/x-tar", "application/octet-stream", ""):
            raise invalid_agent()
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_HARNESS_BYTES + 128 * 1024:
                raise invalid_agent()
            data.extend(chunk)
        if not data and optional and content_type == "":
            return None
        if not data or not content_type:
            raise invalid_agent()
        return bytes(data)

    @app.get("/api/v1/agents")
    async def list_agents():
        return {"agents": await anyio.to_thread.run_sync(agents.list)}

    @app.get("/api/v1/agents/{name}")
    async def agent(name: str):
        return await anyio.to_thread.run_sync(agents.show, name)

    @app.get("/api/v1/agents/{name}/versions")
    async def versions(name: str):
        return {"versions": await anyio.to_thread.run_sync(agents.versions, name)}

    @app.get("/api/v1/agents/{name}/versions/{version}")
    async def version(name: str, version: str, against: str | None = None):
        version = integer(version)
        record = await anyio.to_thread.run_sync(agents.version, name, version)
        if against is not None:
            against = integer(against)
            record = record | {"diff": await anyio.to_thread.run_sync(agents.diff, name, version, against)}
        return record

    @app.get("/api/v1/agents/{name}/versions/{version}/snapshot")
    async def export_snapshot(name: str, version: str):
        version = integer(version)
        data = await anyio.to_thread.run_sync(agents.export, name, version)
        return Response(data, media_type="application/x-tar", headers={"X-Content-Type-Options": "nosniff"})

    @app.post("/api/v1/agents", status_code=201)
    async def create_agent(request: Request, id: str, title: str | None = None):
        operator(request)
        data = await upload(request)
        return await anyio.to_thread.run_sync(agents.create, id, data, title)

    @app.post("/api/v1/agents/{name}/snapshots", status_code=201)
    async def snapshot(name: str, request: Request):
        operator(request)
        data = await upload(request, optional=True)
        return await anyio.to_thread.run_sync(agents.snapshot, name, data)

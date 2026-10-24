"""Read-only Embodiment, Controller and Robot Model catalog routes."""

import anyio

from apps.api.app.library_validation import program_schemas
from packages.library.robot_resources import RobotResources


def install_robot_resource_routes(app, assets, scenes):
    resources = RobotResources(assets.roots, assets, scenes, program_schemas())
    app.state.robot_resources = resources

    @app.get("/api/v1/robot-models")
    async def robot_models():
        return {"models": await anyio.to_thread.run_sync(resources.models)}

    @app.get("/api/v1/robot-models/{identifier}")
    async def robot_model(identifier: str):
        return await anyio.to_thread.run_sync(resources.model, identifier)

    @app.get("/api/v1/embodiments")
    async def embodiments():
        return {"embodiments": await anyio.to_thread.run_sync(resources.embodiments)}

    @app.get("/api/v1/embodiments/{identifier}")
    async def embodiment(identifier: str):
        return await anyio.to_thread.run_sync(resources.embodiment, identifier)

    @app.get("/api/v1/controllers/{identifier}")
    async def controller(identifier: str):
        return await anyio.to_thread.run_sync(resources.controller, identifier)

    @app.get("/api/v1/controllers/{identifier}/versions")
    async def controller_versions(identifier: str):
        value = await anyio.to_thread.run_sync(resources.controller, identifier)
        return {"versions": value["versions"]}

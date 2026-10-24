"""Read-only Embodiment, Controller and Robot Model catalog routes."""

import anyio

from apps.api.app.library_validation import program_schemas
from packages.library.robot_resources import RobotResources


def install_robot_resource_routes(app, assets, scenes, manager=None):
    resources = RobotResources(assets.roots, assets, scenes, program_schemas())
    app.state.robot_resources = resources

    def active_sessions(robot):
        from packages.info.evaluation_privacy import visible_session

        sessions = []
        for session in tuple(manager.sessions.values()) if manager is not None else ():
            if (not session.holds_resources or session.catalog is None
                    or not visible_session(manager, session)):
                continue
            loaded = session.scene_version
            if loaded is None or not any(binding["entity"] in session.catalog.entities
                                         for binding in robot["bindings"]):
                continue
            permit = session.deployment_permit
            controller = permit["controller"] if permit else None
            sessions.append({"sessionId": session.session_id, "scene": loaded.id,
                             "sceneContentHash": loaded.content_hash, "controller": controller})
        return robot | {"activeSessions": sessions}

    @app.get("/api/v1/robot-models")
    async def robot_models():
        return {"models": await anyio.to_thread.run_sync(resources.models)}

    @app.get("/api/v1/robot-models/{identifier}")
    async def robot_model(identifier: str):
        return await anyio.to_thread.run_sync(resources.model, identifier)

    @app.get("/api/v1/embodiments")
    async def embodiments():
        return {"embodiments": [active_sessions(robot)
                                for robot in await anyio.to_thread.run_sync(resources.embodiments)]}

    @app.get("/api/v1/embodiments/{identifier}")
    async def embodiment(identifier: str):
        return active_sessions(await anyio.to_thread.run_sync(resources.embodiment, identifier))

    @app.get("/api/v1/controllers/{identifier}")
    async def controller(identifier: str):
        return await anyio.to_thread.run_sync(resources.controller, identifier)

    @app.get("/api/v1/controllers/{identifier}/versions")
    async def controller_versions(identifier: str):
        value = await anyio.to_thread.run_sync(resources.controller, identifier)
        return {"versions": value["versions"]}

"""Read-only Embodiment, Controller and Robot Model catalog routes."""

import anyio

from apps.api.app.library_validation import program_schemas
from packages.info.evaluation_identity import comparison_identity, verify_identity
from packages.library.common import LibraryError
from packages.library.robot_resources import RobotResources


def install_robot_resource_routes(app, assets, scenes, manager=None):
    resources = RobotResources(assets.roots, assets, scenes, program_schemas())
    app.state.robot_resources = resources

    def evaluation_evidence(version):
        unknown = {"status": "unknown", "currentEvidence": "unknown"}
        if manager is None or manager.evaluations is None:
            return unknown
        store = manager.evaluations.store
        record = store.records.get(version["evaluationId"], {})
        row = record.get("result") or {}
        candidate = row.get("candidateIdentity") or {}
        artifact = version.get("programArtifact")
        expected = version["policy"] or (artifact or {}).get("id") or version["program"]
        actual = candidate.get("id") or candidate.get("programId")
        successes, episodes = row.get("successes"), row.get("episodes")
        if (record.get("status") != "completed" or record.get("split") != "train"
                or row.get("split") != "train" or row.get("evaluationId") != version["evaluationId"]
                or row not in store.rows or actual != expected
                or type(successes) is not int or type(episodes) is not int or not 0 <= successes <= episodes
                or episodes == 0 or row.get("score") != successes / episodes):
            return unknown
        current = "unknown"
        try:
            if (not isinstance(record.get("identitySnapshot"), dict) or not comparison_identity(row)
                    or record["identitySnapshot"]["candidateIdentity"] != candidate):
                raise ValueError("Current comparison evidence is unavailable")
            if record.get("runtimeHash") != manager.evaluations._runtime_hash:
                raise ValueError("Runtime evidence differs")
            verify_identity(record["identitySnapshot"], directory=store.root / "identity-inputs")
            suite = manager.evaluations.suites.resolve(row["suite"])
            scene = manager.library.resolve(row["key"]["scene"], source=row["key"]["source"])
            if (suite.content_hash != record["suiteContentHash"]
                    or scene.content_hash != row["key"]["sceneContentHash"]):
                raise ValueError("Evaluation packages differ")
            if version["policy"]:
                material, _ = manager.policies.resolve(version["policy"])
                if material["contentHash"] != candidate["contentHash"]:
                    raise ValueError("Material evidence differs")
            elif artifact:
                material, _ = manager.programs.resolve(artifact["id"])
                if material["contentHash"] != artifact["contentHash"] or artifact["contentHash"] != (
                    candidate["contentHash"]):
                    raise ValueError("Material evidence differs")
            current = "verified"
        except (OSError, ValueError, KeyError, TypeError, LibraryError):
            pass  # The measured historical result remains readable; reusability stays unknown.
        return {"status": "measured", "evaluationId": row["evaluationId"], "suite": row["suite"],
                "successes": successes, "episodes": episodes, "score": row["score"],
                "decision": row["decision"], "currentEvidence": current,
                "evidenceMode": record.get("evidenceMode", "measured"),
                "url": "/api/v1/evaluations/" + row["evaluationId"]}

    def version_readback(version):
        return version | {"evaluationEvidence": evaluation_evidence(version)} if version is not None else None

    def controller_readback(value):
        return value | {"current": version_readback(value["current"]),
                        "versions": [version_readback(version) for version in value["versions"]]}

    def robots_readback(values):
        return [active_sessions(robot) for robot in values]

    def active_sessions(robot):
        from packages.info.evaluation_privacy import visible_session

        sessions = []
        for session in tuple(manager.sessions.values()) if manager is not None else ():
            if (not session.holds_resources or session.catalog is None
                    or not visible_session(manager, session)):
                continue
            loaded = session.scene_version
            permit = session.deployment_permit
            same_embodiment = bool(permit and permit["controller"]["document"]["embodiment"] == robot["id"])
            original_binding = loaded is not None and any(
                binding["scene"] == loaded.id and binding["source"] == session.kind
                and binding["entity"] in session.catalog.entities for binding in robot["bindings"]
            )
            if loaded is None or not (same_embodiment or original_binding):
                continue
            controller = permit["controller"] if permit else None
            sessions.append({"sessionId": session.session_id, "scene": loaded.id,
                             "sceneContentHash": loaded.content_hash, "controller": controller})
        return robot | {
            "activeSessions": sessions, "currentController": version_readback(robot["currentController"]),
            "controllerHistory": [version_readback(version) for version in robot["controllerHistory"]],
        }

    @app.get("/api/v1/robot-models")
    async def robot_models():
        return {"models": await anyio.to_thread.run_sync(resources.models)}

    @app.get("/api/v1/robot-models/{identifier}")
    async def robot_model(identifier: str):
        return await anyio.to_thread.run_sync(resources.model, identifier)

    @app.get("/api/v1/embodiments")
    async def embodiments():
        values = await anyio.to_thread.run_sync(resources.embodiments)
        return {"embodiments": await anyio.to_thread.run_sync(robots_readback, values)}

    @app.get("/api/v1/embodiments/{identifier}")
    async def embodiment(identifier: str):
        value = await anyio.to_thread.run_sync(resources.embodiment, identifier)
        return await anyio.to_thread.run_sync(active_sessions, value)

    @app.get("/api/v1/controllers/{identifier}")
    async def controller(identifier: str):
        value = await anyio.to_thread.run_sync(resources.controller, identifier)
        return await anyio.to_thread.run_sync(controller_readback, value)

    @app.get("/api/v1/controllers/{identifier}/versions")
    async def controller_versions(identifier: str):
        value = await anyio.to_thread.run_sync(resources.controller, identifier)
        value = await anyio.to_thread.run_sync(controller_readback, value)
        return {"versions": value["versions"]}

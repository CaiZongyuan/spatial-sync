"""Guessing an embodied held-out identity cannot authorize a generic cleanup mutation."""

import httpx
from fastapi.testclient import TestClient

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.protocol.schema import SessionOptions


def test_generic_eval_cleanup_cannot_mutate_an_embodied_private_scope():
    with TestClient(create_app(Settings(node_trainer=()))) as client:
        service = client.app.state.manager.evaluations
        identifier = "eval-private-controller-counter"
        service.store.save({"evaluationId": identifier, "split": "test", "status": "failed", "episodes": []})
        service._blocked.add(identifier)
        assert client.get("/api/v1/evaluations/" + identifier).status_code == 404
        response = client.post("/api/v1/evaluations/" + identifier + "/cleanup",
                               headers={"X-Actor-Kind": "agent"})
        assert response.status_code == 404, response.text
        assert identifier in service._blocked


async def test_embodied_private_session_and_durable_recording_cannot_be_read_by_guessed_id():
    app = create_app(Settings(node_trainer=()))
    async with app.router.lifespan_context(app):
        manager = app.state.manager
        identifier = "eval-controller-heldout-read-counter"
        record = {"evaluationId": identifier, "split": "test", "status": "running", "episodes": []}
        manager.evaluations.store.save(record)
        session = await manager.create(None, "synthetic", SessionOptions(continueWithoutObservers=True),
                                       scene_version="lab-demo@1", evaluation_id=identifier)
        try:
            record["episodes"] = [{"recordingId": session.session_id, "status": "running"}]
            manager.evaluations.store.save(record)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://owned",
                                         headers={"X-Actor-Kind": "agent"}) as client:
                routes = [f"/api/v1/sessions/{session.session_id}",
                          f"/api/v1/sessions/{session.session_id}/scene",
                          f"/recordings/{session.session_id}/recording.json"]
                statuses = {route: (await client.get(route)).status_code for route in routes}
                assert set(statuses.values()) == {404}, statuses
        finally:
            await session.close()
        assert not session.holds_resources

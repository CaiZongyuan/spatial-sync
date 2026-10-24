"""The owned in-process listener closes its own socket/tasks without replacing host signals."""

import httpx

from packages.agents.adapter import close_model_server, model_server
from packages.info.plans import PlanError


async def test_model_listener_rejects_unknown_grant_and_has_retriable_owned_cleanup():
    class Gate:
        def authenticate(self, value):
            raise PlanError("agent_transport_required", "No fixture model grant is active", status=401)

    handles = []
    async with model_server(Gate(), registered=handles.append) as url:
        async with httpx.AsyncClient(base_url=url, trust_env=False) as http:
            denied = await http.post("/v1/messages", json={"model": "not-executed"})
            assert denied.status_code == 401
    [handle] = handles
    server, task, listener = handle
    assert task.done() and listener.fileno() == -1 and not server.server_state.tasks
    await close_model_server(handle)

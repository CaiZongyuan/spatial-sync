"""A completed arm action can precede its next source safe-hold sample."""

import pytest

from tools.cell_passage_runner import PassageExecutor


class ReleaseClient:
    def __init__(self):
        self.sim_time, self.arm_safe, self.posts = 10.0, False, []
        self.plan = {"id": "plan", "revision": 1, "status": "approved",
                     "sessionId": "session", "sceneRevision": "scene", "epoch": 0,
                     "steps": [{"id": 1, "status": "pending", "do": {
                         "entity": "/workcell", "action": "release", "params": {
                             "resource": "/site", "owner": "auto", "epoch": 0, "operationId": "release-site",
                         },
                     }}]}

    async def plan_show(self, _):
        return self.plan

    async def status(self):
        return {"sessionId": "session", "sceneRevision": "scene", "epoch": 0,
                "state": "RUNNING", "simTime": self.sim_time}

    async def get_state(self, keys):
        values = {"controlOwner": "auto", "controlEpoch": 0, "controlMode": "auto",
                  "materialRevision": 1, "controlSafe": True}
        return {"entities": {key: {"values": values if key == "/workcell" else {
            "controlSafe": self.arm_safe}} for key in keys}}

    async def catalog(self):
        return {"entities": {"/workcell": {"config": {"managedDevices": ["/arm"]}}}}

    async def route(self):
        return "/session"

    async def request(self, method, path, **kwargs):
        self.posts.append((method, path, kwargs))
        return {"invocationId": "release-invocation", "status": "accepted"}


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, None])
async def test_release_waits_for_actual_managed_safe_fact_before_first_post(initial):
    client = ReleaseClient()
    client.arm_safe = initial
    executor = PassageExecutor(client, client.plan, "/workcell", "auto", 0, 1)
    executor.budget_seconds = 2
    first = await executor.advance()
    assert first["reason"] == "managed_devices_settling"
    assert first["unsafeDevices"] == ["/arm"]
    assert client.posts == []
    client.sim_time += 1
    assert (await executor.advance())["elapsedSimSeconds"] == 1
    assert client.posts == []
    client.arm_safe = True
    result = await executor.advance()
    assert result["invocation"]["invocationId"] == "release-invocation"
    assert len(client.posts) == 1


@pytest.mark.asyncio
async def test_managed_settling_budget_uses_source_time_and_does_not_reset_each_poll():
    client = ReleaseClient()
    executor = PassageExecutor(client, client.plan, "/workcell", "auto", 0, 1)
    executor.budget_seconds = 2
    await executor.advance()
    for _ in range(3):
        assert (await executor.advance())["elapsedSimSeconds"] == 0
    client.sim_time += 3
    result = await executor.advance()
    assert (result["status"], result["elapsedSimSeconds"]) == ("budget_exceeded", 3)
    assert client.posts == []

"""Plan snapshots refreshed between actions must prevent repeating completed work."""

from copy import deepcopy

import pytest

from apps.cli.lw.client import GatewayClient


@pytest.mark.parametrize("later_status", ["done", "running", "failed"])
async def test_runner_uses_latest_step_status_before_next_action(later_status):
    class ConcurrentClient(GatewayClient):
        def __init__(self):
            self._session_explicit = True
            self.calls = []
            self.plan = {
                "id": "p", "kind": "operation", "target": "session", "status": "approved", "revision": 1,
                "sessionId": "s", "sceneRevision": "r", "epoch": 0,
                "steps": [
                    {"id": 7, "status": "pending", "do": {"entity": "/a", "action": "first"}},
                    {"id": 20, "status": "pending", "do": {"entity": "/b", "action": "second"}},
                ],
            }

        async def plan_show(self, id, revision=None):
            return deepcopy(self.plan)

        async def status(self):
            return {"sessionId": "s", "sceneRevision": "r", "epoch": 0}

        async def invoke_action(self, key, action, params=None, **context):
            self.calls.append(context["stepId"])
            self.plan["steps"][0]["status"] = "done"
            # Another controller advances the later step while this action runs.
            self.plan["steps"][1]["status"] = later_status
            self.plan["status"] = "completed" if later_status == "done" else "running"
            return {"status": "succeeded", "invocationId": "first"}

    client = ConcurrentClient()
    result = await client.plan_run("p", revision=1)
    assert client.calls == [7]
    assert result["status"] == ("completed" if later_status == "done" else "failed")
    if later_status != "done":
        assert result["stoppedStep"] == 20

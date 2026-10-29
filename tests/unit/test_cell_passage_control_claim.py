"""Accepted control RPCs and published state can arrive on different frames."""

import json

import pytest

from tools.cell_passage import _claim

WORKCELL, ARM = "/workcell", "/arm"


class ClaimClient:
    def __init__(self, fault=None):
        self.phase, self.index, self.fault = None, 0, fault
        self.calls, self.receipts = [], []
        self.invocations = {}
        self.last_state = None

    async def status(self):
        return {"sessionId": "session", "sceneRevision": "revision",
                "epoch": 1 if self.phase is not None and self.fault == "session_reset" else 0,
                "simTime": 100 + self.index}

    async def route(self):
        return "/session"

    async def request(self, method, path, *, params):
        assert method == "GET" and path == "/session/invocations"
        return {"invocations": ([{"entityKey": ARM, "status": "running"}]
                                 if self.fault == "active_managed" else [])}

    async def get_invocation(self, identifier):
        result = self.invocations[identifier]
        if self.fault == "invocation_failed":
            return result | {"status": "failed"}
        completed = self.index >= (6 if self.phase == "requestControl" else 3)
        return result | {"status": "succeeded" if completed else "accepted",
                         "startedSimTime": (0.3 if self.phase == "requestControl" else 0.6)
                         if completed else None}

    async def catalog(self):
        return {"entities": {WORKCELL: {"config": {"managedDevices": [ARM]}}}}

    async def invoke_action(self, key, action, params, **kwargs):
        if action == "confirmControl":
            assert self.index >= 6  # A cached safe flag cannot finish a still-queued source invocation.
            state = self.last_state
            assert state[WORKCELL]["values"]["controlEpoch"] == 1
            assert state[WORKCELL]["values"]["controlMode"] == "holding"
            assert state[WORKCELL]["values"]["controlSafe"] is True
            assert state[ARM]["values"]["controlSafe"] is True
            assert state[WORKCELL]["values"]["materialSnapshot"]["controlRequests"]
            assert state[WORKCELL]["values"]["controlSafeAtSimTime"] >= 0.3
        self.calls.append(action)
        self.phase, self.index = action, 0
        self.receipts.append({"operationId": params["operationId"], "epoch": 1, "action": action,
                              "paramsJson": json.dumps(params, sort_keys=True)})
        result = {"status": "accepted", "invocationId": "inv-" + action,
                  "entityKey": key, "action": action, "params": params}
        self.invocations[result["invocationId"]] = result
        return result

    async def get_state(self, keys):
        epoch, owner, mode, safe, receipts, sampled = 0, "auto", "auto", True, [], 0.1
        if self.phase == "requestControl":
            if self.index >= 1:
                epoch, mode = 1, "holding"
            if self.index >= 2:
                receipts = self.receipts.copy()
                safe = self.index >= 3
            if self.fault == "new_epoch":
                epoch, owner, mode = 2, "operator", "manual"
            elif self.fault == "other_owner":
                epoch, owner, mode = 1, "other", "holding"
            elif self.fault == "wrong_receipt":
                epoch, mode = 1, "holding"
                receipts = [self.receipts[0] | {"paramsJson": "{}"}]
            elif self.fault == "never_observe":
                epoch, mode, receipts = 0, "auto", []
            sampled = 0.2 if self.index < 7 else 0.4
        elif self.phase == "confirmControl":
            epoch, mode, receipts = 1, "holding", self.receipts[:1]
            if self.index >= 1:
                owner, mode = "runner", "auto"
            if self.index >= 2:
                receipts = self.receipts.copy()
            if self.fault == "fenced_after_confirm":
                epoch, owner, mode = 2, "operator", "manual"
            sampled = 0.4 if self.index < 4 else 0.7
        if self.fault == "unknown_sample":
            sampled = None
        managed_safe = self.phase != "requestControl" or self.index >= 4
        self.index += 1
        self.last_state = {
            WORKCELL: {"values": {"controlOwner": owner, "controlEpoch": epoch, "controlMode": mode,
                                   "controlSafe": safe, "controlSafeAtSimTime": sampled,
                                   "materialSnapshot": {"controlRequests": receipts}}},
            ARM: {"values": {"controlSafe": managed_safe}},
        }
        return {"entities": self.last_state}


@pytest.fixture(autouse=True)
def no_wall_wait(monkeypatch):
    async def sleep(_):
        pass

    monkeypatch.setattr("tools.cell_passage.asyncio.sleep", sleep)


@pytest.mark.asyncio
async def test_claim_waits_for_own_receipts_and_both_published_handoff_phases():
    client = ClaimClient()
    assert await _claim(client, WORKCELL, "runner") == 1
    assert client.calls == ["requestControl", "confirmControl"]
    assert len(client.last_state[WORKCELL]["values"]["materialSnapshot"]["controlRequests"]) == 2
    assert client.last_state[WORKCELL]["values"]["controlSafeAtSimTime"] >= 0.6


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["new_epoch", "other_owner", "wrong_receipt", "never_observe",
                                   "fenced_after_confirm", "session_reset", "invocation_failed"])
async def test_claim_stops_at_a_foreign_context_without_resubmission(fault):
    client = ClaimClient(fault)
    assert await _claim(client, WORKCELL, "runner") is None
    assert client.calls == (["requestControl", "confirmControl"] if fault == "fenced_after_confirm"
                            else ["requestControl"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["unknown_sample", "active_managed"])
async def test_global_clock_or_old_safe_values_cannot_replace_new_own_quiescence_feedback(fault):
    client = ClaimClient(fault)
    assert await _claim(client, WORKCELL, "runner") is None
    assert client.calls == ["requestControl"]

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
                "epoch": 1 if self.phase is not None and self.fault == "session_reset" else 0}

    async def get_invocation(self, identifier):
        result = self.invocations[identifier]
        if self.fault == "invocation_failed":
            return result | {"status": "failed"}
        return result

    async def catalog(self):
        return {"entities": {WORKCELL: {"config": {"managedDevices": [ARM]}}}}

    async def invoke_action(self, key, action, params, **kwargs):
        if action == "confirmControl":
            state = self.last_state
            assert state[WORKCELL]["values"]["controlEpoch"] == 1
            assert state[WORKCELL]["values"]["controlMode"] == "holding"
            assert state[WORKCELL]["values"]["controlSafe"] is True
            assert state[ARM]["values"]["controlSafe"] is True
            assert state[WORKCELL]["values"]["materialSnapshot"]["controlRequests"]
        self.calls.append(action)
        self.phase, self.index = action, 0
        self.receipts.append({"operationId": params["operationId"], "epoch": 1, "action": action,
                              "paramsJson": json.dumps(params, sort_keys=True)})
        result = {"status": "accepted", "invocationId": "inv-" + action,
                  "entityKey": key, "action": action, "params": params}
        self.invocations[result["invocationId"]] = result
        return result

    async def get_state(self, keys):
        epoch, owner, mode, safe, receipts = 0, "auto", "auto", True, []
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
        elif self.phase == "confirmControl":
            epoch, mode, receipts = 1, "holding", self.receipts[:1]
            if self.index >= 1:
                owner, mode = "runner", "auto"
            if self.index >= 2:
                receipts = self.receipts.copy()
            if self.fault == "fenced_after_confirm":
                epoch, owner, mode = 2, "operator", "manual"
        managed_safe = self.phase != "requestControl" or self.index >= 4
        self.index += 1
        self.last_state = {
            WORKCELL: {"values": {"controlOwner": owner, "controlEpoch": epoch, "controlMode": mode,
                                   "controlSafe": safe, "materialSnapshot": {"controlRequests": receipts}}},
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


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["new_epoch", "other_owner", "wrong_receipt", "never_observe",
                                   "fenced_after_confirm", "session_reset", "invocation_failed"])
async def test_claim_stops_at_a_foreign_context_without_resubmission(fault):
    client = ClaimClient(fault)
    assert await _claim(client, WORKCELL, "runner") is None
    assert client.calls == (["requestControl", "confirmControl"] if fault == "fenced_after_confirm"
                            else ["requestControl"])

"""Exit receipts reflect real HTTP invocation outcomes without retrying an uncertain hold."""

import json
from types import SimpleNamespace

import httpx
import pytest

from apps.cli.lw.client import GatewayClient
from tools.cell_passage_runner import drive_executor


@pytest.mark.parametrize("ending", ["failure", "exception"])
@pytest.mark.parametrize("status", [
    "rejected", "failed", "cancelled", "accepted", "running", "pending_approval", "succeeded", "lost",
])
async def test_drive_exit_keeps_hold_outcome_and_unknown_admission_single_post(tmp_path, ending, status):
    posts = []

    def response(request):
        if request.method == "GET":
            assert request.url.path.endswith("/state")
            return httpx.Response(200, json={"entities": {"/workcell": {"values": {
                "controlOwner": "auto", "controlEpoch": 0,
            }}}})
        assert request.method == "POST" and request.url.path.endswith("/actions")
        body = json.loads(request.content)
        posts.append(body)
        if status == "lost":
            raise httpx.ReadTimeout("Lost response after admission")
        value = {"invocationId": "hold-invocation", "requestId": body["requestId"],
                 "entityKey": body["entityKey"], "action": body["action"], "params": body["params"],
                 "status": status, "error": {"code": "source_unavailable", "message": "Source unavailable"}
                 if status == "rejected" else None}
        return httpx.Response(503 if status == "rejected" else 200, json=value)

    async def advance():
        if ending == "exception":
            raise RuntimeError("Original executor error")
        return {"status": "failed", "reason": "mechanical_fault"}

    async with httpx.AsyncClient(base_url="http://owned", transport=httpx.MockTransport(response)) as http:
        executor = SimpleNamespace(client=GatewayClient(session="owned-session", http=http),
            workcell="/workcell", owner="auto", control_epoch=0, plan_id="owned-plan",
            target={"sessionId": "owned-session", "sceneRevision": "scene", "epoch": 0},
            active={"requestId": "original-request", "invocationId": "original-invocation"}, advance=advance)
        receipt = tmp_path / "exit.jsonl"
        if ending == "exception":
            with pytest.raises(RuntimeError, match="Original executor error"):
                await drive_executor(executor, receipt)
        else:
            assert (await drive_executor(executor, receipt))["status"] == "failed"
    end = json.loads(receipt.read_text().splitlines()[-1])
    assert end["status"] == ("interrupted" if ending == "exception" else "dispatch_stopped")
    assert end["active"]["invocationId"] == "original-invocation"
    assert end["experimentQualified"] is False
    assert len(posts) == 1
    assert posts[0]["action"] == "requestControl"
    assert posts[0]["params"]["owner"] == "auto" and posts[0]["params"]["epoch"] == 0
    hold = end["hold"]
    expected = status if status in ("rejected", "failed", "cancelled") else (
        "unconfirmed" if status == "lost" else "acknowledged"
    )
    assert hold["status"] == expected
    assert hold["requestId"] == posts[0]["requestId"]
    assert hold["operationId"] == posts[0]["params"]["operationId"]
    assert hold["globalSafeConfirmed"] is False
    if status == "lost":
        assert hold.get("invocationId") is None
        assert hold["reason"] == "admission_unconfirmed"
    else:
        assert hold["invocationId"] == "hold-invocation"
        assert hold["invocationStatus"] == status
        if status == "rejected":
            assert hold["reason"] == "source_unavailable"


@pytest.mark.parametrize("fault", ["fenced", "observation_lost", "identity", "http_error"])
async def test_exit_hold_does_not_claim_unknown_ack_or_submit_for_another_owner(tmp_path, fault):
    posts = []

    def response(request):
        if request.method == "GET":
            assert request.url.path.endswith("/state")
            if fault == "observation_lost":
                raise httpx.ReadTimeout("State observation unavailable")
            return httpx.Response(200, json={"entities": {"/workcell": {"values": {
                "controlOwner": "human" if fault == "fenced" else "auto", "controlEpoch": 0,
            }}}})
        posts.append(json.loads(request.content))
        if fault == "http_error":
            return httpx.Response(503, json={"detail": {"code": "source_unavailable"}})
        return httpx.Response(200, json={"invocationId": "other-invocation", "requestId": "other-request",
                                        "status": "running"})

    async def advance():
        return {"status": "failed"}

    async with httpx.AsyncClient(base_url="http://owned", transport=httpx.MockTransport(response)) as http:
        executor = SimpleNamespace(client=GatewayClient(session="session", http=http), workcell="/workcell",
            owner="auto", control_epoch=0, plan_id="plan", target={}, active=None, advance=advance)
        receipt = tmp_path / "exit.jsonl"
        await drive_executor(executor, receipt)
    hold = json.loads(receipt.read_text().splitlines()[-1])["hold"]
    assert hold["globalSafeConfirmed"] is False
    assert hold["status"] == ("already_fenced" if fault == "fenced" else "unconfirmed")
    assert len(posts) == (0 if fault in ("fenced", "observation_lost") else 1)
    if fault == "identity":
        assert hold["reason"] == "invocation_identity_unconfirmed"
    elif fault == "http_error":
        assert hold["error"]["statusCode"] == 503
        assert hold["error"]["detail"]["code"] == "source_unavailable"

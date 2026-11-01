"""Launch retries observe one durable identity; they never resubmit a Source."""

import json

import pytest

from apps.cli.lw.client import GatewayError
from tools import cell_passage as demo
from tools.cell_passage import _start_session


class LaunchClient:
    def __init__(self, *, lost=False, fault=None, immediate=False):
        self.calls, self.polls, self.frames = [], 0, 0
        self.lost, self.fault, self.immediate = lost, fault, immediate

    async def request(self, method, path, **kwargs):
        self.calls.append((method, path))
        if method == "POST":
            if self.lost:
                raise GatewayError("lost acknowledgement")
            return {"sessionId": "session"} if self.immediate else {
                "requestId": "request-test", "status": "starting"}
        self.frames += 1
        return {"sessionId": "session", "sceneVersion": "wrong@1" if self.fault == "scene" else "scene@2",
                "state": "LOADING" if self.frames == 1 else "RUNNING"}

    async def session_request_show(self, identifier):
        assert identifier == "request-test"
        self.polls += 1
        if self.lost and self.polls == 1:
            raise GatewayError("not yet observed", status_code=404)
        return {"requestId": "request-wrong" if self.fault == "identity" else identifier,
                "status": "failed" if self.fault == "failed" else "started", "sessionId": "session"}


@pytest.mark.asyncio
@pytest.mark.parametrize("lost,immediate", [(False, False), (True, False), (False, True)])
async def test_observes_loading_and_lost_ack_without_second_post(tmp_path, lost, immediate):
    client = LaunchClient(lost=lost, immediate=immediate)
    result = await _start_session(client, "scene@2", tmp_path, request_id="request-test")
    assert result["state"] == "RUNNING"
    assert sum(method == "POST" for method, _ in client.calls) == 1
    receipt = json.loads((tmp_path / "launch-request.json").read_text())
    assert receipt["status"] == "ready" and receipt["request"]["requestId"] == "request-test"
    if lost:
        assert receipt["requestNotObserved"] and receipt["postError"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["identity", "scene", "failed"])
async def test_rejects_changed_identity_scene_and_definitive_failure(tmp_path, fault):
    client = LaunchClient(fault=fault)
    with pytest.raises(ValueError):
        await _start_session(client, "scene@2", tmp_path, request_id="request-test")
    receipt = json.loads((tmp_path / "launch-request.json").read_text())
    assert receipt["exceptionType"] == "ValueError"
    assert receipt["request"]["requestId"] == "request-test"


@pytest.mark.asyncio
async def test_wall_timeout_retains_owned_identity_for_cleanup(tmp_path):
    client = LaunchClient()
    with pytest.raises(TimeoutError):
        await _start_session(client, "scene@2", tmp_path, request_id="request-test",
                             startup_timeout_seconds=.01)
    receipt = json.loads((tmp_path / "launch-request.json").read_text())
    assert receipt["exceptionType"] == "TimeoutError"
    assert receipt["request"]["requestId"] == "request-test"
    assert client.calls == [("POST", "/api/v1/sessions")]


@pytest.mark.parametrize("status", ["queued", "starting", "started"])
def test_cleanup_only_cancels_and_closes_owned_request(monkeypatch, status):
    calls = []

    class Response:
        status_code = 200
        headers = {"X-Passage-Owner": "nonce"}

        def raise_for_status(self):
            pass

        def json(self):
            return {"requestId": "request-test", "status": status, "sessionId": "owned"}

    def get(url, **kwargs):
        calls.append(("GET", url))
        return Response()

    def delete(url, **kwargs):
        calls.append(("DELETE", url))
        return Response()

    monkeypatch.setattr(demo.httpx, "get", get)
    monkeypatch.setattr(demo.httpx, "delete", delete)
    result = demo._cancel_owned_launch("http://owned", "nonce", "request-test")
    deletes = [url for method, url in calls if method == "DELETE"]
    expected = ["http://owned/api/v1/session-requests/request-test"] if status != "started" else []
    assert deletes == expected + ["http://owned/api/v1/sessions/owned"]
    assert result["sessionClosed"]


def test_changed_gateway_nonce_prevents_cleanup(monkeypatch):
    class Response:
        headers = {"X-Passage-Owner": "someone-else"}

        def raise_for_status(self):
            pass

    monkeypatch.setattr(demo.httpx, "get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(demo.httpx, "delete", lambda *args, **kwargs: pytest.fail("must not delete"))
    with pytest.raises(ValueError, match="identity changed"):
        demo._cancel_owned_launch("http://owned", "nonce", "request-test")

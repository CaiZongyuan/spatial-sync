"""A completed round cannot suppress cleanup of the next pending launch."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tools import cell_passage as demo


@pytest.mark.parametrize("fault", ["timeout", "lost_ack"])
def test_second_round_launch_failure_cancels_new_identity_only(tmp_path, monkeypatch, fault):
    owned, closed, cancelled = {}, [], []
    prepare = demo._prepare

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, path, **kwargs):
            assert method == "POST" and path == "/api/v1/sessions"
            owned["request"] = kwargs["json"]["requestId"]
            if fault == "lost_ack":
                raise demo.GatewayError("lost acknowledgement")
            await asyncio.sleep(10)

        async def session_request_show(self, identifier):
            assert identifier == owned["request"]
            raise demo.GatewayError("not yet observed", status_code=404)

    class Process:
        def __init__(self, *args, env, **kwargs):
            owned["nonce"] = env["SPATIAL_PASSAGE_OWNER_NONCE"]
            self.stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, **kwargs):
            return 0

    async def rounds(args, inputs, bootstrap, identifier, round_id, output):
        if round_id.endswith("r1"):
            bootstrap.update(sessionRequestId="request-first", session={"sessionId": "first"},
                             sessionClosed=False)
            return {"status": "plans_completed"}
        return await prepare(args, inputs, bootstrap, identifier, round_id, output)

    async def install(*args):
        return "scene@2"

    def cancel(url, nonce, identifier):
        assert nonce == owned["nonce"]
        cancelled.append(identifier)
        return {"status": "cancelled"}

    monkeypatch.setattr(demo, "GatewayClient", Client)
    monkeypatch.setattr(demo, "_available", lambda port: None)
    monkeypatch.setattr(demo, "load_inputs", lambda library: ({}, {}, {}, {}))
    monkeypatch.setattr(demo, "content_hash", lambda scene: "hash")
    monkeypatch.setattr(demo, "compile_recipe", lambda *a, **kw: {"environmentChecksAvailable": True})
    monkeypatch.setattr(demo.subprocess, "Popen", Process)
    monkeypatch.setattr(demo.httpx, "get", lambda *a, **kw: SimpleNamespace(
        headers={"X-Passage-Owner": owned["nonce"]}, raise_for_status=lambda: None))
    monkeypatch.setattr(demo, "_install_once", install)
    monkeypatch.setattr(demo, "_prepare", rounds)
    monkeypatch.setattr(demo, "_close_owned_session", lambda url, nonce, session: closed.append(session))
    monkeypatch.setattr(demo, "_audit_closed_recording", lambda *a: {"status": "recording_valid"})
    monkeypatch.setattr(demo, "_cancel_owned_launch", cancel)
    args = SimpleNamespace(round="case", repeats=2, source=None, generated=tmp_path / "generated",
                           out=tmp_path / "run", api_port=8023, web_port=5223, no_web=True,
                           prepare_only=False, exit_after_run=True, startup_timeout_seconds=.01)
    with pytest.raises(TimeoutError):
        demo.demo(args)
    bootstrap = json.loads((args.out / "bootstrap.json").read_text())
    assert cancelled == [owned["request"]] and owned["request"] != "request-first"
    assert closed == ["first"]
    assert "session" not in bootstrap and bootstrap["sessionClosed"] is False
    assert bootstrap["runs"][0]["session"]["sessionId"] == "first"
    assert bootstrap["launchCleanup"]["status"] == "cancelled"

"""Real library workers publish safely through the actual fsync writer."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from test_asset_imports import package_files, upload

from apps.api.app.main import create_app
from apps.api.app.settings import Settings

TARGET = "/api/v1/library/assets/labware/test-tube/"


def test_library_worker_cold_poll_waits_its_fixed_fsync_prefix_while_later_work_continues(monkeypatch):
    app = create_app()
    loop_threads, producer_threads = set(), set()

    @app.middleware("http")
    async def observe_loop(request, call_next):
        loop_threads.add(threading.get_ident())
        return await call_next(request)

    with TestClient(app) as client:
        assert upload(client).status_code == 200
        assert upload(client, package_files() | {"NOTICE": b"second revision"}).status_code == 200
        first_entered, second_entered, attached = (threading.Event() for _ in range(3))
        release_first, release_second = threading.Event(), threading.Event()
        store = app.state.manager.inbox
        write, wait = store.journal._write_batch, store.journal.wait
        callback = app.state.manager.approvals.on_change
        count = 0

        def observed(record):
            producer_threads.add(threading.get_ident())
            callback(record)

        def held_write(stream, entries):
            nonlocal count
            count += 1
            entered, release = ((first_entered, release_first) if count == 1
                                else (second_entered, release_second))
            entered.set()
            assert release.wait(10), "The test must release its owned disk writer"
            write(stream, entries)  # Actual write, flush and fsync precede acknowledgement.

        async def observed_wait(ticket):
            attached.set()
            await wait(ticket)

        monkeypatch.setattr(app.state.manager.approvals, "on_change", observed)
        monkeypatch.setattr(store.journal, "_write_batch", held_write)
        monkeypatch.setattr(store.journal, "wait", observed_wait)
        with ThreadPoolExecutor(3) as executor:
            try:
                first = executor.submit(client.delete, TARGET + "2").result(timeout=5)
                assert first.status_code == 202, first.text
                assert first_entered.wait(3)
                cold = executor.submit(client.get, "/api/v1/inbox")
                assert attached.wait(3) and not cold.done()
                second = executor.submit(client.delete, TARGET + "1").result(timeout=5)
                assert second.status_code == 202, second.text
                assert producer_threads and producer_threads.isdisjoint(loop_threads)
                release_first.set()
                assert second_entered.wait(3)
                frozen = cold.result(timeout=3)
                assert frozen.status_code == 200, frozen.text
                assert [value["approvalId"] for value in frozen.json()["approvals"]] == [
                    first.json()["approvalId"]]
                assert [value["data"]["approvalId"] for value in frozen.json()["messages"]] == [
                    first.json()["approvalId"]]
                # The next batch remains held; the first cursor did not wait for it.
                assert not release_second.is_set()
                durable = [json.loads(line) for line in store.path.read_bytes().splitlines()[1:]]
                assert [value["data"]["approvalId"] for value in durable] == [first.json()["approvalId"]]
                later = executor.submit(client.get, "/api/v1/inbox",
                                        params={"after": frozen.json()["cursor"]})
                release_second.set()
                continued = later.result(timeout=3)
                assert continued.status_code == 200, continued.text
                assert [value["data"]["approvalId"] for value in continued.json()["messages"]] == [
                    second.json()["approvalId"]]
                snapshot = client.get("/api/v1/inbox").json()
                assert len(snapshot["approvals"]) == 2
                repeated = client.delete(TARGET + "1")
                assert repeated.json()["approvalId"] == second.json()["approvalId"]
                unchanged = client.get("/api/v1/inbox", params={"after": snapshot["cursor"]})
                assert unchanged.json()["messages"] == []
            finally:
                release_first.set()
                release_second.set()


def test_actual_worker_approval_still_returns_when_inbox_disk_fails(monkeypatch):
    app = create_app()
    with TestClient(app) as client:
        assert upload(client).status_code == 200

        def failed_write(stream, entries):
            raise OSError("Injected inbox disk failure")

        monkeypatch.setattr(app.state.manager.inbox.journal, "_write_batch", failed_write)
        requested = client.delete(TARGET + "1")
        assert requested.status_code == 202, requested.text
        unavailable = client.get("/api/v1/inbox")
        assert unavailable.status_code == 503 and unavailable.json()["detail"]["code"] == "inbox_unavailable"
        record = client.get("/api/v1/approvals/" + requested.json()["approvalId"])
        assert record.status_code == 200 and record.json()["status"] == "pending"
        assert client.get(TARGET + "1").status_code == 200
        denied = client.post("/api/v1/approvals/" + requested.json()["approvalId"] + "/decision",
                             json={"decision": "deny"})
        assert denied.status_code == 200 and denied.json()["status"] == "denied"
        assert client.get("/api/v1/inbox").status_code == 503


async def test_cancelled_poll_cannot_cancel_shared_disk_ack_or_hide_later_notifications(monkeypatch):
    app = create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            sid = (await client.post("/api/v1/sessions", json={"sceneId": "lab-demo"})).json()["sessionId"]
            route = "/api/v1/sessions/" + sid + "/actions"
            inbox = app.state.manager.inbox
            entered, release = threading.Event(), threading.Event()
            original = inbox.journal._write_batch

            def held_write(stream, entries):
                entered.set()
                assert release.wait(10)
                original(stream, entries)

            monkeypatch.setattr(inbox.journal, "_write_batch", held_write)
            body = {"requestId": "first-rejection", "entityKey": "/World/Lab/Centrifuge", "action": "missing"}
            try:
                assert (await client.post(route, json=body)).status_code == 404
                assert await asyncio.to_thread(entered.wait, 2)
                cancelled = asyncio.create_task(client.get("/api/v1/inbox"))
                await asyncio.sleep(0)
                cancelled.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await cancelled
                second = await asyncio.wait_for(client.post(route, json=body | {"requestId": "second"}), 2)
                assert second.status_code == 404
                release.set()
                response = await asyncio.wait_for(client.get("/api/v1/inbox"), 3)
                assert response.status_code == 200, response.text
                assert [value["data"]["requestId"] for value in response.json()["messages"]] == [
                    "first-rejection", "second"]
            finally:
                release.set()


def test_acknowledged_cursor_ahead_of_recovered_history_and_corrupt_stream_fail_explicitly(tmp_path):
    settings = Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings")
    with TestClient(create_app(settings)) as client:
        sid = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()["sessionId"]
        body = {"entityKey": "/World/Lab/Centrifuge", "action": "missing"}
        for index in range(2):
            assert client.post(f"/api/v1/sessions/{sid}/actions", json=body | {
                "requestId": str(index)}).status_code == 404
        cursor = client.get("/api/v1/inbox").json()["cursor"]
    path = settings.state_root / "inbox.jsonl"
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(lines[:2]))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/inbox", params={"after": cursor}).status_code == 422
    path.write_bytes(lines[0] + b"{bad-json}\n" + b"".join(lines[2:]))
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/inbox").status_code == 503
        assert client.get("/api/v1/health").status_code == 200
        sid = client.post("/api/v1/sessions", json={"sceneId": "lab-demo"}).json()["sessionId"]
        independent = client.post(f"/api/v1/sessions/{sid}/actions", json=body | {"requestId": "still-works"})
        assert independent.status_code == 404

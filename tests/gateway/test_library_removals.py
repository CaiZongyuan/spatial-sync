"""Ordinary HTTP removal/restore uses the real immutable bytes and legacy reference guards."""

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from scene_support import user_version

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from packages.library.common import LibraryError, content_hash
from packages.library.removals import RemovalRequest
from packages.protocol.schema import Actor

TARGET = "/api/v1/library/scenes/lab-demo/4"
ROOT = "/api/v1/library/deletions"
AGENT = {"X-Actor": "agent:library-test"}


def settled(client, record):
    deadline = time.monotonic() + 10
    while record["status"] in ("queued", "checking", "restoring"):
        assert time.monotonic() < deadline, record
        time.sleep(0.02)
        response = client.get(ROOT + "/" + record["id"])
        assert response.status_code == 200, response.text
        record = response.json()
    return record


def body(client, request_id="ordinary-delete"):
    value = client.get(TARGET).json()
    return {
        "resource": "scene",
        "id": value["id"],
        "contentHash": value["contentHash"],
        "requestId": request_id,
    }


def test_agent_removal_restore_idempotence_and_legacy_approval(tmp_path):
    user_version(tmp_path / "user-library", 4)
    app = create_app()
    with TestClient(app) as client:
        original = client.get(TARGET).json()
        request = body(client)
        legacy = client.delete(TARGET).json()
        response = client.post(ROOT, json=request, headers=AGENT)
        assert response.status_code == 202, response.text
        removed = settled(client, response.json())
        assert removed["status"] == "committed" and removed["retainedBytes"] > 0, removed
        assert client.get(TARGET).status_code == 404
        assert client.get("/api/v1/approvals").json()["pendingCount"] == 0
        approval = client.get("/api/v1/approvals/" + legacy["approvalId"]).json()
        assert approval["status"] == "unavailable" and approval["decisions"] == []
        assert settled(client, client.post(ROOT, json=request, headers=AGENT).json()) == removed
        changed = client.post(ROOT, json=request | {"contentHash": "a" * 64}, headers=AGENT)
        assert changed.status_code == 409 and changed.json()["detail"]["code"] == "request_conflict"
        restored = settled(client, client.post(ROOT + "/" + removed["id"] + "/restore", headers=AGENT).json())
        assert restored["status"] == "restored" and restored["retainedBytes"] == 0
        assert client.get(TARGET).json()["contentHash"] == original["contentHash"]
        assert client.post(ROOT + "/" + removed["id"] + "/restore").json() == restored
        assert client.get("/api/v1/approvals").json()["pendingCount"] == 0
    with TestClient(create_app()) as client:
        assert client.get(TARGET).json()["contentHash"] == original["contentHash"]
        assert client.get(ROOT + "/" + removed["id"]).json()["status"] == "restored"


def test_removal_keeps_active_session_reference_and_readonly_guards(tmp_path):
    user_version(tmp_path / "user-library", 4)
    with TestClient(create_app()) as client:
        request = body(client)
        builtin = client.get("/api/v1/library/scenes/lab-demo/1").json()
        denied = client.post(
            ROOT, json=request | {"id": builtin["id"], "contentHash": builtin["contentHash"]}
        )
        assert denied.status_code == 403
        assert client.post(ROOT, json=request | {"contentHash": "a" * 64}).status_code == 409
        session = client.post("/api/v1/sessions", json={"sceneVersion": "lab-demo@4"})
        assert session.status_code == 201, session.text
        try:
            record = settled(client, client.post(ROOT, json=request).json())
            assert record["status"] == "blocked" and record["error"]["code"] == "version_in_use"
            assert record["error"]["references"] and client.get(TARGET).status_code == 200
            assert client.get("/api/v1/approvals").json()["pendingCount"] == 0
        finally:
            client.delete("/api/v1/sessions/" + session.json()["sessionId"])


def test_retained_bytes_are_verified_before_restore(tmp_path):
    user_version(tmp_path / "user-library", 4)
    app = create_app()
    with TestClient(app) as client:
        removed = settled(client, client.post(ROOT, json=body(client)).json())
        package = app.state.manager.library_removals.directory(removed["id"]) / "package"
        package.joinpath("locations.yaml").write_text("locations: {}", encoding="utf-8")
        restored = settled(client, client.post(ROOT + "/" + removed["id"] + "/restore").json())
        assert restored["status"] == "committed" and restored["error"]["code"] == "version_changed"
        assert client.get(TARGET).status_code == 404 and package.exists()


def test_interrupted_removal_rolls_back_and_interrupted_restore_completes(tmp_path, monkeypatch):
    class Interrupted(BaseException):
        pass

    user_version(tmp_path / "user-library", 4)
    app = create_app()
    with TestClient(app) as client:
        service = app.state.manager.library_removals
        monkeypatch.setattr(service, "start", lambda *args, **kwargs: None)
        record = client.post(ROOT, json=body(client)).json()
        package = service.directory(record["id"]) / "package"
        refresh = service.legacy._refresh

        def fail_after_move():
            if package.exists():
                raise Interrupted()
            return refresh()

        with monkeypatch.context() as patch:
            patch.setattr(service.legacy, "_refresh", fail_after_move)
            with pytest.raises(Interrupted):
                service.execute(record["id"])
        assert package.exists()
    with TestClient(create_app()) as client:
        assert client.get(TARGET).status_code == 200
        assert client.get(ROOT + "/" + record["id"]).json()["status"] == "failed"
        removed = settled(client, client.post(ROOT, json=body(client, "second-request")).json())
        service = client.app.state.manager.library_removals
        service.request_restore(removed["id"], Actor(kind="user", name="test"))
        with monkeypatch.context() as patch:
            patch.setattr(service.legacy, "_refresh", lambda: (_ for _ in ()).throw(Interrupted()))
            with pytest.raises(Interrupted):
                service.restore(removed["id"])
    with TestClient(create_app()) as client:
        assert client.get(TARGET).status_code == 200
        assert client.get(ROOT + "/" + removed["id"]).json()["status"] == "restored"


def test_viewer_cannot_remove_or_restore(tmp_path):
    user_version(tmp_path / "user-library", 4)
    app = create_app(Settings(remote=True, operator_token="test-operator", viewer_token="test-viewer"))
    with TestClient(app, headers={"Authorization": "Bearer test-viewer"}) as client:
        request = body(client)
        assert client.post(ROOT, json=request).status_code == 403
        assert client.post(ROOT + "/deletion-" + "a" * 32 + "/restore").status_code == 403


def test_submit_after_replacement_failure_reuses_the_durable_request(tmp_path, monkeypatch):
    import packages.library.removals as removals

    user_version(tmp_path / "user-library", 4)
    with TestClient(create_app()) as client:
        request = body(client)
        durable = removals.durable_json

        def uncertain(path, value):
            durable(path, value)
            if value.get("status") == "queued":
                raise OSError("injected directory fsync failure after replacement")

        with monkeypatch.context() as patch:
            patch.setattr(removals, "durable_json", uncertain)
            assert client.post(ROOT, json=request).status_code == 503
        existing = client.get(ROOT).json()["deletions"]
        assert len(existing) == 1 and client.get(TARGET).status_code == 200
        removed = settled(client, client.post(ROOT, json=request).json())
        assert removed["id"] == existing[0]["id"] and removed["status"] == "committed"
        assert len(client.get(ROOT).json()["deletions"]) == 1


def test_failed_first_journal_write_does_not_break_restart(tmp_path, monkeypatch):
    import packages.library.removals as removals

    user_version(tmp_path / "user-library", 4)
    with TestClient(create_app()) as client:
        request = body(client)
        with monkeypatch.context() as patch:
            patch.setattr(removals, "durable_json", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
            assert client.post(ROOT, json=request).status_code == 503
        assert client.get(TARGET).status_code == 200
    with TestClient(create_app()) as client:
        assert client.get(TARGET).status_code == 200
        assert settled(client, client.post(ROOT, json=request).json())["status"] == "committed"


def test_old_approval_is_inapplicable_even_if_its_audit_append_failed(tmp_path, monkeypatch):
    user_version(tmp_path / "user-library", 4)
    app = create_app()
    with TestClient(app) as client:
        legacy = client.delete(TARGET).json()
        save = app.state.manager.approvals._save

        def fail_retirement(record):
            if record.status == "unavailable":
                raise OSError("injected audit failure")
            return save(record)

        with monkeypatch.context() as patch:
            patch.setattr(app.state.manager.approvals, "_save", fail_retirement)
            removed = settled(client, client.post(ROOT, json=body(client)).json())
            assert removed["status"] == "committed"
            assert client.get("/api/v1/approvals/" + legacy["approvalId"]).json()["status"] == "unavailable"
        assert (
            client.post(
                "/api/v1/approvals/" + legacy["approvalId"] + "/decision", json={"decision": "approve"}
            ).status_code
            == 409
        )
        assert (
            settled(client, client.post(ROOT + "/" + removed["id"] + "/restore").json())["status"]
            == "restored"
        )
        fresh = client.delete(TARGET).json()
        assert fresh["approvalId"] != legacy["approvalId"]
        assert client.get("/api/v1/approvals/" + fresh["approvalId"]).json()["status"] == "pending"


def test_waiting_for_journal_read_lock_does_not_block_health(tmp_path, monkeypatch):
    user_version(tmp_path / "user-library", 4)
    app = create_app()
    with TestClient(app) as client, ThreadPoolExecutor(3) as pool:
        service = app.state.manager.library_removals
        record = service.submit(RemovalRequest.model_validate(body(client)), Actor(kind="user", name="test"))
        entered = Event()
        original = service.show

        def waiting(identifier):
            entered.set()
            return original(identifier)

        monkeypatch.setattr(service, "show", waiting)
        with service.lock:
            reading = pool.submit(client.get, ROOT + "/" + record["id"])
            assert entered.wait(5)
            health = pool.submit(client.get, "/api/v1/health")
            assert health.result(timeout=2).status_code == 200
            assert not reading.done()
        assert reading.result(timeout=5).status_code == 200


def test_retained_dependencies_remain_protected_from_legacy_and_ordinary_deletion():
    from test_asset_imports import package_files, upload

    with TestClient(create_app()) as client:
        dependency = upload(client).json()
        files = package_files()
        files["asset.yaml"] = files["asset.yaml"].replace(b"test-tube", b"indirect")
        files["asset.usda"] = (
            b'#usda 1.0\n(defaultPrim = "Asset"\nupAxis = "Z"\n'
            b'metersPerUnit = 1)\ndef Xform "Asset" (references = '
            b"@library:assets/labware/test-tube/1/asset.usda@) {}\n"
        )
        owner = upload(client, files).json()
        request = {
            "resource": "asset",
            "id": owner["assetVersion"],
            "contentHash": owner["contentHash"],
            "requestId": "retain-owner",
        }
        removed = settled(client, client.post(ROOT, json=request).json())
        assert removed["status"] == "committed", removed
        target = "/api/v1/library/assets/labware/test-tube/1"
        legacy = client.delete(target)
        assert legacy.status_code == 409 and legacy.json()["detail"]["references"][0]["kind"] == "recovery"
        attempted = settled(
            client,
            client.post(
                ROOT,
                json={
                    "resource": "asset",
                    "id": dependency["assetVersion"],
                    "contentHash": dependency["contentHash"],
                    "requestId": "keep-dependency",
                },
            ).json(),
        )
        assert attempted["status"] == "blocked" and attempted["error"]["code"] == "version_in_use"
        assert (
            settled(client, client.post(ROOT + "/" + removed["id"] + "/restore").json())["status"]
            == "restored"
        )


def test_linked_recovery_package_is_rejected_before_restore_and_restart(tmp_path):
    user_version(tmp_path / "user-library", 4)
    app = create_app()
    with TestClient(app) as client:
        removed = settled(client, client.post(ROOT, json=body(client)).json())
        service = app.state.manager.library_removals
        package = service.directory(removed["id"]) / "package"
        target = tmp_path / "user-library/retained-copy"
        shutil.copytree(package, target)
        original = content_hash(target)
        assert package.resolve().is_relative_to(service.directory(removed["id"]).resolve())
        shutil.rmtree(package)
        try:
            if os.name == "nt":
                import _winapi

                _winapi.CreateJunction(str(target), str(package))
            else:
                package.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Directory link creation unavailable")
        result = settled(client, client.post(ROOT + "/" + removed["id"] + "/restore").json())
        assert result["status"] == "committed" and result["error"] is not None
        assert client.get(TARGET).status_code == 404 and content_hash(target) == original
    with pytest.raises((LibraryError, ValueError)):
        with TestClient(create_app()):
            pass


def test_old_remove_audit_failure_cannot_poison_accepted_restore(tmp_path, monkeypatch):
    user_version(tmp_path / "user-library", 4)
    app = create_app()
    with TestClient(app) as client:
        entered, release = Event(), Event()
        service = app.state.manager.library_removals

        def delayed_failure(record):
            entered.set()
            assert release.wait(10)
            raise OSError("injected secondary audit failure")

        monkeypatch.setattr(service, "_retire_approvals", delayed_failure)
        record = client.post(ROOT, json=body(client)).json()
        try:
            assert entered.wait(5)
            restoring = client.post(ROOT + "/" + record["id"] + "/restore").json()
            assert restoring["status"] == "restoring"
        finally:
            release.set()
        restored = settled(client, restoring)
        assert restored["status"] == "restored" and client.get(TARGET).status_code == 200

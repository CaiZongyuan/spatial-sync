"""Deletion uses the real upload, human decision, index and durable restart boundaries."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event, local

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from test_asset_imports import package_files, upload

from apps.api.app.main import create_app
from apps.api.app.settings import Settings
from apps.api.app.sources.synthetic_source import SyntheticSource
from packages.library.validation import StructuralValidator
from packages.protocol.internal import SessionOptions
from packages.protocol.schema import Actor

TARGET = "/api/v1/library/assets/labware/test-tube/2"


def decision(client, approval_id, **kwargs):
    return client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "approve", "note": "确认仅删除此版本"}, **kwargs,
    )


def test_delete_exact_version_approval_restart_and_never_reuse_revision():
    original = package_files()
    second = original | {"NOTICE": b"Second revision\n"}
    with TestClient(create_app()) as client:
        assert upload(client, original).json()["assetVersion"] == "labware/test-tube@1"
        assert upload(client, second).json()["assetVersion"] == "labware/test-tube@2"
        first = client.get(TARGET.removesuffix("2") + "1").json()
        first_bytes = {key: client.get(url).content for key, url in first["files"].items()}
        target = client.get(TARGET).json()
        requested = client.delete(TARGET, headers={"X-Actor": "agent:builder"})
        assert requested.status_code == 202, requested.text
        pending = requested.json()
        assert pending["code"] == "pending_approval"
        approval_id = pending["approvalId"]
        assert client.get(TARGET).json()["contentHash"] == target["contentHash"]
        assert all(client.get(url).status_code == 200 for url in target["files"].values())
        assert decision(client, approval_id, headers={"X-Actor": "agent:builder"}).status_code == 403
        approved = decision(client, approval_id)
        assert approved.status_code == 200, approved.text
        record = approved.json()
        assert record["status"] == "approved" and not record["canRevoke"]
        assert record["details"]["execution"]["status"] == "committed"
        assert len(record["decisions"]) == 1
        assert client.get(TARGET).status_code == 404
        assert all(client.get(url).status_code == 404 for url in target["files"].values())
    with TestClient(create_app()) as client:
        assert client.get(TARGET).status_code == 404
        repeated = decision(client, approval_id)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["decisions"] == record["decisions"]
        assert upload(client, second).json()["assetVersion"] == "labware/test-tube@3"
        assert {key: client.get(url).content for key, url in first["files"].items()} == first_bytes


def test_new_reference_between_request_and_decision_blocks_execution():
    from template_support import reference_files, scene_files
    from template_support import upload as template_upload

    with TestClient(create_app()) as client:
        assert template_upload(client, reference_files()).status_code == 200
        route = "/api/v1/library/assets/device/slice-reference-centrifuge/1"
        before = client.get(route).json()
        requested = client.delete(route)
        assert requested.status_code == 202, requested.text
        files, _ = scene_files()
        metadata = yaml.safe_load(files["scene.yaml"])
        metadata.update(stage="python:robot-and-tubes", sources=["newton"])
        files["scene.yaml"] = yaml.safe_dump(metadata).encode()
        published = template_upload(client, files)
        assert published.status_code == 200, published.text
        assert published.json()["sceneVersion"] == "template-slice@1"
        blocked = decision(client, requested.json()["approvalId"])
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "version_in_use"
        assert any(ref["id"] == "template-slice@1" for ref in blocked.json()["detail"]["references"])
        record = client.get(f"/api/v1/approvals/{requested.json()['approvalId']}").json()
        assert record["status"] == "pending" and record["decisions"] == []
        assert record["details"]["execution"]["status"] == "blocked"
        assert client.get(route).json()["contentHash"] == before["contentHash"]
        assert all(client.get(url).status_code == 200 for url in before["files"].values())


@pytest.mark.parametrize("source", ["usd", "history", "hashless"])
def test_nested_asset_and_all_history_retain_exact_asset(source):
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        files = package_files()
        files["asset.yaml"] = files["asset.yaml"].replace(b"test-tube", b"indirect")
        if source == "usd":
            files["asset.usda"] = (b'#usda 1.0\n(defaultPrim = "Asset"\nupAxis = "Z"\n'
                                   b'metersPerUnit = 1)\ndef Xform "Asset" (references = '
                                   b'@library:assets/labware/test-tube/1/asset.usda@) {}\n')
        assert upload(client, files).status_code == 200
        if source != "usd":
            assets = client.app.state.library_assets
            owner = assets.resolve("labware/indirect@1")
            pin = {"id": "labware/test-tube@1"}
            if source == "history":
                pin["contentHash"] = assets.resolve("labware/test-tube@1").content_hash
            owner.reports.save(owner.validation | {"referencedAssets": [pin]}, owner.content_hash)
            owner.reports.save(owner.validation, owner.content_hash)  # latest alone forgets the pin.
            owner.reports.directory("asset:" + owner.id, owner.content_hash).joinpath("latest.json").unlink()
        blocked = client.delete(TARGET.removesuffix("2") + "1")
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["detail"]["code"] == "version_in_use"
        assert any(ref["id"] == "labware/indirect@1" for ref in blocked.json()["detail"]["references"])
        assert client.get("/api/v1/approvals").json()["pendingCount"] == 0


@pytest.mark.parametrize("unknown", ["recording", "history", "training"])
def test_unknown_consumers_fail_closed_and_identify_uninspected_object(tmp_path, unknown):
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        if unknown == "recording":
            path = tmp_path / "recordings/broken/recording.json"
            path.parent.mkdir(parents=True)
            path.write_text("{", encoding="utf-8")
        elif unknown == "training":
            path = tmp_path / "user-library/jobs/unreadable"
            path.parent.mkdir(parents=True)
            path.write_text("unknown format", encoding="utf-8")
        else:
            # A surviving package with a misbound history entry cannot silently discard that entry.
            owner = client.app.state.library_assets.resolve("robot/fr3@1")
            saved = owner.reports.save(owner.validation, owner.content_hash)
            path = owner.reports.directory("asset:" + owner.id, owner.content_hash)
            path = path / (saved["reportId"] + ".json")
            path.write_text(json.dumps(saved | {"subject": "asset:robot/other@1"}), encoding="utf-8")
            path.parent.joinpath("latest.json").unlink()
        rejected = client.delete(TARGET.removesuffix("2") + "1")
        assert rejected.status_code == 409, rejected.text
        detail = rejected.json()["detail"]
        assert detail["code"] == "reference_inspection_incomplete" and not detail["complete"]
        assert detail["uninspected"]
        assert client.get(TARGET.removesuffix("2") + "1").status_code == 200


def test_index_refresh_failure_rolls_back_bytes_indexes_and_keeps_human_audit(monkeypatch):
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        target = TARGET.removesuffix("2") + "1"
        before = client.get(target).json()
        expected = {key: client.get(url).content for key, url in before["files"].items()}
        pending = client.delete(target).json()
        scenes = client.app.state.manager.library
        refresh = scenes.refresh
        asset_path = client.app.state.library_assets.resolve("labware/test-tube@1").path
        injected = False

        def fail_between_indexes():
            nonlocal injected
            if not asset_path.exists() and not injected:
                injected = True
                raise OSError("Injected scene-index publication failure")
            return refresh()

        monkeypatch.setattr(scenes, "refresh", fail_between_indexes)
        failed = decision(client, pending["approvalId"])
        assert failed.status_code == 503 and failed.json()["detail"]["code"] == "deletion_failed"
        assert injected
        record = client.get(f"/api/v1/approvals/{pending['approvalId']}").json()
        assert record["status"] == "approved" and record["details"]["execution"]["status"] == "failed"
        assert len(record["decisions"]) == 1 and record["canRevoke"]
        assert client.get(target).json()["contentHash"] == before["contentHash"]
        assert {key: client.get(url).content for key, url in before["files"].items()} == expected
        succeeded = decision(client, pending["approvalId"])
        assert succeeded.status_code == 200, succeeded.text
        assert succeeded.json()["decisions"] == record["decisions"]
        assert client.get(target).status_code == 404


def test_changed_target_cannot_be_authorized_by_an_older_request():
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        target = TARGET.removesuffix("2") + "1"
        pending = client.delete(target).json()
        version = client.app.state.library_assets.resolve("labware/test-tube@1")
        version.path.joinpath("LICENSE").write_bytes(b"Externally changed bytes after request\n")
        rejected = decision(client, pending["approvalId"])
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "asset_changed"
        record = client.get(f"/api/v1/approvals/{pending['approvalId']}").json()
        assert record["status"] == "pending" and record["decisions"] == []
        assert record["details"]["execution"]["status"] == "blocked"
        assert version.path.joinpath("LICENSE").read_bytes() == b"Externally changed bytes after request\n"


def test_purge_failure_stays_committed_and_only_owned_quarantine_is_cleaned_on_restart(monkeypatch):
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        target = TARGET.removesuffix("2") + "1"
        pending = client.delete(target).json()
        deletions = client.app.state.manager.library_deletions
        package = deletions.root / ".deletions" / pending["approvalId"] / "package"

        def fail_purge(*args):
            raise OSError("Injected quarantine cleanup failure")

        with monkeypatch.context() as patch:
            patch.setattr(deletions, "_purge", fail_purge)
            committed = decision(client, pending["approvalId"])
        assert committed.status_code == 200, committed.text
        assert committed.json()["details"]["execution"] == {"status": "committed", "cleanupPending": True}
        assert package.is_dir() and client.get(target).status_code == 404
    with TestClient(create_app()) as client:
        assert client.get(target).status_code == 404 and not package.exists()
        record = client.get(f"/api/v1/approvals/{pending['approvalId']}").json()
        assert len(record["decisions"]) == 1 and record["details"]["execution"]["status"] == "committed"


def test_human_authorization_fsync_failure_never_withdraws(monkeypatch):
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        target = TARGET.removesuffix("2") + "1"
        before = client.get(target).json()
        pending = client.delete(target).json()
        store = client.app.state.manager.approvals
        fsync = os.fsync

        def fail_approval_fsync(descriptor):
            if os.fstat(descriptor).st_ino == store.path.stat().st_ino:
                raise OSError("Injected human audit fsync failure")
            return fsync(descriptor)

        with monkeypatch.context() as patch:
            patch.setattr(os, "fsync", fail_approval_fsync)
            failed = decision(client, pending["approvalId"])
        assert failed.status_code == 503, failed.text
        assert failed.json()["detail"]["code"] == "approval_persistence_unavailable"
        assert client.get(target).json()["contentHash"] == before["contentHash"]
        assert all(client.get(url).status_code == 200 for url in before["files"].values())
        directory = client.app.state.library_assets.roots["user"] / ".deletions" / pending["approvalId"]
        assert not directory.joinpath("package").exists()
        assert json.loads(directory.joinpath("receipt.json").read_text())["phase"] == "prepared"


def test_interrupted_withdrawal_recovers_bytes_before_serving_index(monkeypatch):
    class Interrupted(BaseException):
        pass

    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        target = TARGET.removesuffix("2") + "1"
        before = client.get(target).json()
        pending = client.delete(target).json()
        manager = client.app.state.manager
        path = client.app.state.library_assets.resolve("labware/test-tube@1").path
        refresh = manager.library.refresh

        def interrupt_after_rename():
            if not path.exists():
                raise Interrupted("Simulated process death after rename, before scene-index publication")
            return refresh()

        with monkeypatch.context() as patch:
            patch.setattr(manager.library, "refresh", interrupt_after_rename)
            with pytest.raises(Interrupted):
                manager.library_deletions.decide(
                    pending["approvalId"], "approve",
                    Actor(kind="user", name="recovery-test"), "保留人工决定",
                )
        assert not path.exists()
    with TestClient(create_app()) as client:
        assert client.get(target).json()["contentHash"] == before["contentHash"]
        assert all(client.get(url).status_code == 200 for url in before["files"].values())
        record = client.get(f"/api/v1/approvals/{pending['approvalId']}").json()
        assert record["status"] == "approved" and len(record["decisions"]) == 1
        assert record["details"]["execution"]["status"] == "failed"
        assert decision(client, pending["approvalId"]).status_code == 200


def test_committed_receipt_keeps_result_visible_if_final_audit_append_fails(monkeypatch, tmp_path):
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        target = TARGET.removesuffix("2") + "1"
        pending = client.delete(target).json()
        deletions = client.app.state.manager.library_deletions
        save = deletions._save_execution

        def fail_result(record, status, **details):
            if status == "committed":
                raise OSError("Injected final result append failure")
            return save(record, status, **details)

        with monkeypatch.context() as patch:
            patch.setattr(deletions, "_save_execution", fail_result)
            failed = decision(client, pending["approvalId"])
        assert failed.status_code == 503 and failed.json()["detail"]["code"] == "deletion_result_unavailable"
        record = client.get(f"/api/v1/approvals/{pending['approvalId']}").json()
        assert record["status"] == "approved" and record["details"]["execution"]["status"] == "committed"
        assert not record["canRevoke"] and client.get(target).status_code == 404
    # Another gateway with the same library but another state root validates the embedded human
    # authorization, recovers the shared withdrawal, and does not invent a local approval request.
    with TestClient(create_app(Settings(state_root=tmp_path / "other-state"))) as other:
        assert other.get(target).status_code == 404
        assert other.get("/api/v1/approvals").json()["approvals"] == []
    with TestClient(create_app()) as client:
        assert decision(client, pending["approvalId"]).json()["details"]["execution"]["status"] == "committed"


@pytest.mark.parametrize("replaced", [False, True])
def test_commit_receipt_write_failure_recovers_according_to_the_actual_commit_point(monkeypatch, replaced):
    target = TARGET.removesuffix("2") + "1"
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        pending = client.delete(target).json()
        deletions = client.app.state.manager.library_deletions
        write = deletions._receipt

        def fail_commit(directory, receipt, phase):
            if phase != "committed":
                return write(directory, receipt, phase)
            if replaced:
                write(directory, receipt, phase)
            raise OSError("Injected commit durability failure")

        with monkeypatch.context() as patch:
            patch.setattr(deletions, "_receipt", fail_commit)
            failed = decision(client, pending["approvalId"])
        assert failed.status_code == 503 and failed.json()["detail"]["code"] == "deletion_recovery_required"
        record = client.get(f"/api/v1/approvals/{pending['approvalId']}").json()
        assert record["details"]["execution"]["status"] == ("committed" if replaced else "in_progress")
        assert record["status"] == "approved" and not record["canRevoke"]
        assert client.get(target).status_code == 404
    with TestClient(create_app()) as client:
        assert client.get(target).status_code == (404 if replaced else 200)
        record = client.get(f"/api/v1/approvals/{pending['approvalId']}").json()
        assert record["details"]["execution"]["status"] == ("committed" if replaced else "failed")
        assert len(record["decisions"]) == 1


def test_concurrent_decisions_share_one_human_audit_and_withdrawal():
    with TestClient(create_app()) as client:
        assert upload(client).status_code == 200
        target = TARGET.removesuffix("2") + "1"
        pending = client.delete(target).json()
        with ThreadPoolExecutor(max_workers=2) as workers:
            replies = list(workers.map(lambda _: decision(client, pending["approvalId"]), range(2)))
        assert [reply.status_code for reply in replies] == [200, 200]
        assert all(len(reply.json()["decisions"]) == 1 for reply in replies)
        assert replies[0].json()["decisions"] == replies[1].json()["decisions"]
        assert client.get(target).status_code == 404


def test_starting_and_active_session_without_recording_protect_exact_scene(tmp_path):
    from scene_support import user_version

    user_version(tmp_path / "user-library", 4)
    entered, release = Event(), Event()

    class HeldSource(SyntheticSource):
        async def start(self, *args, **kwargs):
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)
            return await super().start(*args, **kwargs)

    app = create_app(Settings(recordings_enabled=False), source_factory=lambda kind, sid: HeldSource(sid))
    with TestClient(app) as client:
        target = "/api/v1/library/scenes/lab-demo/4"
        requested = client.delete(target)
        assert requested.status_code == 202, requested.text
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(client.post, "/api/v1/sessions", json={
                "sceneVersion": "lab-demo@4", "source": "synthetic",
            })
            try:
                assert entered.wait(5)
                blocked = decision(client, requested.json()["approvalId"])
                assert blocked.status_code == 409, blocked.text
                assert blocked.json()["detail"]["code"] == "version_in_use"
                assert any(ref["kind"] == "session" for ref in blocked.json()["detail"]["references"])
            finally:
                release.set()
            created = future.result(timeout=10)
        assert created.status_code == 201, created.text
        assert decision(client, requested.json()["approvalId"]).status_code == 409
        assert client.get("/api/v1/recordings").json() == []
        client.delete("/api/v1/sessions/" + created.json()["sessionId"])
        assert decision(client, requested.json()["approvalId"]).status_code == 200


def test_cross_thread_publication_keeps_the_no_recording_deletion_reference(tmp_path, monkeypatch):
    from scene_support import user_version

    user_version(tmp_path / "user-library", 4)
    entered, release, reading, published = Event(), Event(), Event(), Event()
    reader = local()

    class HeldSource(SyntheticSource):
        async def start(self, *args, **kwargs):
            entered.set()
            assert await asyncio.to_thread(release.wait, 10)
            return await super().start(*args, **kwargs)

    class PausedMapping(dict):
        def values(self):
            values = tuple(super().values())
            if getattr(reader, "active", False):
                # Retain the real dictionary snapshot while the actual creator finishes.
                reading.set()
                assert published.wait(10)
            return values

    app = create_app(Settings(recordings_enabled=False), source_factory=lambda kind, sid: HeldSource(sid))
    with TestClient(app) as client:
        target = "/api/v1/library/scenes/lab-demo/4"
        requested = client.delete(target)
        assert requested.status_code == 202, requested.text
        manager = app.state.manager
        manager.sessions = PausedMapping(manager.sessions)
        snapshot = manager.platform_sessions

        def paused_consumer():
            reader.active = True
            try:
                result = snapshot()  # Actual platform/deletion consumer, not a replacement result.
                reading.set()
                assert published.wait(10)
                return result
            finally:
                reader.active = False

        monkeypatch.setattr(manager.library_deletions.inspection, "sessions", paused_consumer)
        with ThreadPoolExecutor(max_workers=2) as workers:
            creation = workers.submit(client.post, "/api/v1/sessions", json={
                "sceneVersion": "lab-demo@4", "source": "synthetic",
            })
            approval = created = None
            try:
                assert entered.wait(5)
                approval = workers.submit(decision, client, requested.json()["approvalId"])
                assert reading.wait(5)
                release.set()
                created = creation.result(timeout=10)
                assert created.status_code == 201, created.text
                published.set()
                result = approval.result(timeout=10)
                assert result.status_code == 409, result.text
                assert result.json()["detail"]["code"] == "version_in_use"
                assert any(ref["kind"] == "session" and ref["id"] == created.json()["sessionId"]
                           for ref in result.json()["detail"]["references"])
                assert client.get(target).status_code == 200
                assert client.get("/api/v1/recordings").json() == []
            finally:
                release.set()
                published.set()
                created = created or creation.result(timeout=10)
                if approval is not None:
                    approval.result(timeout=10)
                if created.status_code == 201:
                    client.delete("/api/v1/sessions/" + created.json()["sessionId"])


@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancelled_registration_clears_its_unstarted_reference(tmp_path, monkeypatch, cancel_count):
    from scene_support import user_version

    user_version(tmp_path / "user-library", 4)
    entered, release = Event(), Event()
    sources = []

    def factory(kind, sid):
        sources.append(sid)
        return SyntheticSource(sid)

    manager = create_app(Settings(recordings_enabled=False), source_factory=factory).state.manager
    resolve = manager.library.resolve

    def held_resolution(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return resolve(*args, **kwargs)

    monkeypatch.setattr(manager.library, "resolve", held_resolution)
    creation = asyncio.create_task(manager.create("lab-demo", "synthetic", SessionOptions(),
                                                scene_version="lab-demo@4"))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(cancel_count):
            creation.cancel()
            # Drain the scheduled cancellation callbacks while the actual worker is still held.
            for _ in range(3):
                checkpoint = asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(checkpoint.set_result, None)
                await checkpoint
            assert not creation.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await creation
        assert manager.platform_sessions() == [] and manager.sessions == {}
        assert sources == []
    finally:
        release.set()
        if not creation.done():
            creation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await creation
        await manager.close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_start_keeps_its_reference_until_source_closes(tmp_path, cancel):
    from scene_support import user_version

    user_version(tmp_path / "user-library", 4)
    started, release_start, closing, release_close = (asyncio.Event() for _ in range(4))
    sources = []

    class ClosingSource(SyntheticSource):
        async def start(self, *args, **kwargs):
            started.set()
            await release_start.wait()
            raise RuntimeError("Injected initial source failure")

        async def close(self):
            closing.set()
            await release_close.wait()
            await super().close()

    def factory(kind, sid):
        source = ClosingSource(sid)
        sources.append(source)
        return source

    app = create_app(Settings(recordings_enabled=False), source_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            target = "/api/v1/library/scenes/lab-demo/4"
            requested = await client.delete(target)
            assert requested.status_code == 202, requested.text
            approval = f"/api/v1/approvals/{requested.json()['approvalId']}/decision"
            creation = asyncio.create_task(client.post("/api/v1/sessions", json={
                "sceneVersion": "lab-demo@4", "source": "synthetic",
            }))
            try:
                await asyncio.wait_for(started.wait(), 5)
                if cancel:
                    creation.cancel()
                else:
                    release_start.set()
                await asyncio.wait_for(closing.wait(), 5)
                held = app.state.manager.platform_sessions()
                assert len(held) == 1 and held[0].scene_version.id == "lab-demo@4"
                blocked = await client.post(approval, json={"decision": "approve"})
                assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "version_in_use"
                assert any(ref["kind"] == "session" and ref["id"] == held[0].session_id
                           for ref in blocked.json()["detail"]["references"])
            finally:
                release_start.set()
                release_close.set()
                if cancel:
                    with pytest.raises(asyncio.CancelledError):
                        await creation
                else:
                    response = await creation
                    assert response.status_code == 503, response.text
            assert app.state.manager.platform_sessions() == [] and sources[0]._state == "STOPPED"
            assert (await client.post(approval, json={"decision": "approve"})).status_code == 200


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_source_cleanup_remains_visible_and_blocks_deletion_until_stop_retry(tmp_path, cancel):
    from scene_support import user_version

    user_version(tmp_path / "user-library", 4)
    started, release = asyncio.Event(), asyncio.Event()

    class FailedSource(SyntheticSource):
        resource_alive = True
        allow_close = False
        close_attempts = 0

        async def start(self, *args, **kwargs):
            started.set()
            await release.wait()
            raise RuntimeError("Injected start failure while resource remains owned")

        async def close(self):
            self.close_attempts += 1
            if not self.allow_close:
                raise RuntimeError("Injected close failure; ownership is unresolved")
            await super().close()
            self.resource_alive = False

    source = None

    def factory(kind, sid):
        nonlocal source
        source = FailedSource(sid)
        return source

    app = create_app(Settings(recordings_enabled=False), source_factory=factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            target = "/api/v1/library/scenes/lab-demo/4"
            requested = await client.delete(target)
            assert requested.status_code == 202, requested.text
            approval = f"/api/v1/approvals/{requested.json()['approvalId']}/decision"
            creation = asyncio.create_task(client.post("/api/v1/sessions", json={
                "sceneVersion": "lab-demo@4", "source": "synthetic",
            }))
            session_id = None
            try:
                await asyncio.wait_for(started.wait(), 5)
                assert source is not None
                if cancel:
                    creation.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await creation
                else:
                    release.set()
                    failed = await creation
                    assert failed.status_code == 503, failed.text
                retained = app.state.manager.platform_sessions()
                assert len(retained) == 1 and source.resource_alive
                session_id = retained[0].session_id
                if not cancel:
                    assert failed.json()["detail"]["sessionId"] == session_id
                assert (await client.get("/api/v1/sessions/" + session_id)).json()["state"] == "FAILED"
                assert (await client.post(f"/api/v1/sessions/{session_id}/restart")).status_code == 409
                blocked = await client.post(approval, json={"decision": "approve"})
                assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "version_in_use"
                assert any(ref["kind"] == "session" and ref["id"] == session_id
                           for ref in blocked.json()["detail"]["references"])
                failed_stop = await client.delete("/api/v1/sessions/" + session_id)
                assert failed_stop.status_code == 503 and source.resource_alive
                assert (await client.post(approval, json={"decision": "approve"})).status_code == 409
                assert (await client.get(target)).status_code == 200
                assert (await client.get("/api/v1/recordings")).json() == []
                source.allow_close = True
                assert (await client.delete("/api/v1/sessions/" + session_id)).status_code == 204
                assert not source.resource_alive and source.close_attempts >= 3
                assert (await client.post(approval, json={"decision": "approve"})).status_code == 200
            finally:
                release.set()
                if source is not None:
                    source.allow_close = True
                if not creation.done():
                    creation.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await creation
                if session_id is not None:
                    await client.delete("/api/v1/sessions/" + session_id)
                elif source is not None and source.resource_alive:
                    await source.close()  # The old implementation lost this fault fixture's owner.


def test_actual_held_scene_keeps_templates_and_unknown_transitive_assets_when_index_drifts(tmp_path):
    from template_support import reference_files, scene_files
    from template_support import upload as template_upload

    app = create_app(Settings(recordings_enabled=False))
    with TestClient(app) as client:
        assert upload(client).status_code == 200
        files = reference_files()
        files["asset.usda"] = files["asset.usda"].replace(
            b'def Xform "Asset" {', b'def Xform "Asset" (references = '
            b'@library:assets/labware/test-tube/1/asset.usda@</Asset>) {',
        )
        imported = template_upload(client, files)
        assert imported.status_code == 200, imported.text
        assert next(check for check in imported.json()["validation"]["checks"]
                    if check["id"] == "S7")["status"] == "pass"
        scene, _ = scene_files()
        assert template_upload(client, scene).status_code == 200
        created = client.post("/api/v1/sessions", json={
            "sceneVersion": "template-slice@1", "source": "synthetic",
        })
        assert created.status_code == 201, created.text
        manager = app.state.manager
        session = manager.get(created.json()["sessionId"])
        held_scene = session.scene_version
        asset = app.state.library_assets.resolve("device/slice-reference-centrifuge@1")
        moved_scene, moved_asset = tmp_path / "offline-scene", tmp_path / "offline-asset"
        try:
            # Direct filesystem drift must not erase the actual running source's retained version.
            held_scene.path.rename(moved_scene)
            blocked = client.delete("/api/v1/library/assets/device/slice-reference-centrifuge/1")
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["detail"]["code"] == "version_in_use"
            assert any(ref["kind"] == "session" and ref["id"] == session.session_id
                       for ref in blocked.json()["detail"]["references"])
            assert held_scene.id not in manager.library.versions
            asset.path.rename(moved_asset)
            transitive = client.delete("/api/v1/library/assets/labware/test-tube/1")
            assert transitive.status_code == 409, transitive.text
            assert transitive.json()["detail"]["code"] == "reference_inspection_incomplete"
            assert any(item["kind"] == "session" and item["id"] == session.session_id
                       for item in transitive.json()["detail"]["uninspected"])
            assert client.get("/api/v1/library/assets/labware/test-tube/1").status_code == 200
        finally:
            if moved_asset.exists():
                moved_asset.rename(asset.path)
            if moved_scene.exists():
                moved_scene.rename(held_scene.path)
            client.delete("/api/v1/sessions/" + session.session_id)


@pytest.mark.parametrize("format_version", [1, 2])
def test_complete_and_incomplete_recordings_protect_exact_scene(tmp_path, format_version):
    from scene_support import user_version

    version = 4 if format_version == 2 else 1
    # Legacy v1 maps scene name to @1; use an independent user scene name at that revision.
    path = user_version(tmp_path / "user-library", 4)
    if format_version == 1:
        metadata = yaml.safe_load(path.joinpath("scene.yaml").read_text(encoding="utf-8"))
        metadata["name"] = "recorded-scene"
        path.joinpath("scene.yaml").write_text(yaml.safe_dump(metadata), encoding="utf-8")
        for name in ("devices.yaml", "locations.yaml", "validation.json"):
            file = path / name
            file.write_text(file.read_text(encoding="utf-8").replace("lab-demo", "recorded-scene")
                            .replace("recorded-scene@4", "recorded-scene@1"), encoding="utf-8")
        destination = tmp_path / "user-library/scenes/recorded-scene/1"
        destination.parent.mkdir(parents=True)
        path.rename(destination)
    name = "lab-demo" if format_version == 2 else "recorded-scene"
    with TestClient(create_app()) as client:
        target = f"/api/v1/library/scenes/{name}/{version}"
        detail = client.get(target).json()
        directory = tmp_path / "recordings/pinned-recording"
        directory.mkdir(parents=True)
        metadata = {"formatVersion": format_version, "sessionId": directory.name,
                    "sceneId": name, "status": "incomplete"}
        if format_version == 2:
            metadata |= {"protocolVersion": 2, "kind": "session", "sceneVersion": detail["id"],
                         "sceneContentHash": detail["contentHash"]}
        directory.joinpath("recording.json").write_text(json.dumps(metadata), encoding="utf-8")
        rejected = client.delete(target)
        assert rejected.status_code == 409 and rejected.json()["detail"]["code"] == "version_in_use"
        assert rejected.json()["detail"]["references"] == [{
            "kind": "recording", "id": "pinned-recording", "reason": "Recorded exact scene version",
        }]


def test_deleted_scene_cannot_publish_a_late_validation_report(tmp_path, monkeypatch):
    from scene_support import user_version

    user_version(tmp_path / "user-library", 4)
    with TestClient(create_app()) as client:
        target = "/api/v1/library/scenes/lab-demo/4"
        pending = client.delete(target).json()
        scene = client.app.state.manager.library.resolve("lab-demo@4")
        directory = scene.reports.directory("scene:" + scene.id, scene.content_hash)
        ready, release = Event(), Event()
        run = StructuralValidator.run

        def held_report(validator, *args, **kwargs):
            report = run(validator, *args, **kwargs)
            ready.set()
            assert release.wait(10)
            return report

        with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as worker:
            patch.setattr(StructuralValidator, "run", held_report)
            future = worker.submit(client.post, target + "/validate")
            try:
                assert ready.wait(5)
                approved = decision(client, pending["approvalId"])
                assert approved.status_code == 200, approved.text
            finally:
                release.set()
            checked = future.result(timeout=10)
        assert checked.status_code == 409 and checked.json()["detail"]["code"] == "scene_not_launchable"
        assert not directory.exists()
        assert client.get(target).status_code == 404

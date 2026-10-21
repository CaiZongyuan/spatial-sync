import asyncio
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.api.app.evaluations import EvaluationRequest, EvaluationService
from apps.api.app.session_manager import Session as ManagedSession
from apps.api.app.session_manager import SessionManager
from apps.api.app.settings import Settings
from packages.library.common import BUILTIN_ROOT
from packages.protocol.internal import SessionOptions


class Session(SimpleNamespace):
    @property
    def holds_resources(self):
        return not self.cleanup_complete or self.source is not None or self._starting_source is not None

    async def close(self):
        self.state, self.source, self.cleanup_complete = "STOPPED", None, True


class Manager:
    def __init__(self, root):
        self.settings = SimpleNamespace(state_root=root)
        self.library_assets = SimpleNamespace(roots={"builtin": BUILTIN_ROOT, "user": root / "library"})
        self.sessions = {}
        self._starting_session = None
        self._create_lock = asyncio.Lock()
        self.created = asyncio.Queue()
        self.library = SimpleNamespace(
            resolve=lambda *args, **kwargs: SimpleNamespace(
                id="lab-demo@3",
                metadata={"sources": ["synthetic"]},
                content_hash="224e5a762fb296e3f97468debdcde106e797274100547f2dd88cb17678b83b9a",
                declarations=lambda source: SimpleNamespace(
                    declarations=(
                        SimpleNamespace(
                            binding={
                                "key": "/World/Lab/RobotA",
                                "type": "robot-arm/v1",
                                "program": "robot_arm.virtual",
                            }
                        ),
                    )
                ),
            )
        )

    async def create(self, scene, source, options, *, scene_version=None):
        identifier = "session-" + str(options.seed)
        session = Session(
            session_id=identifier,
            state="RUNNING",
            source=True,
            _starting_source=None,
            cleanup_complete=False,
            evaluation_id=None,
            summary=lambda: {"sessionId": identifier, "seed": options.seed},
        )
        self.sessions[identifier] = session
        self.created.put_nowait(identifier)
        return session


async def test_fifo_lease_transfer_cancel_and_expiry_without_running_a_source(tmp_path):
    manager = Manager(tmp_path)
    service = EvaluationService(manager)
    first_started, finish_first, second_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def evaluate(identifier):
        calls.append(identifier)
        if len(calls) == 1:
            first_started.set()
            await finish_first.wait()
        else:
            second_started.set()

    service._evaluate = evaluate
    await service.startup()
    try:
        first = await service.submit(
            EvaluationRequest(subject="robot_arm.virtual", suite="suite/lab-demo-pick-place@1")
        )
        await asyncio.wait_for(first_started.wait(), 2)
        for seed in (11, 12, 13, 14):
            value, status = await service.create_session(
                None,
                "synthetic",
                SessionOptions(seed=seed),
                scene_version="lab-demo@3",
                request_id=f"request-{seed}",
            )
            assert status == 202 and value["status"] == "queued"
        assert (await service.cancel_request("request-12"))["status"] == "cancelled"
        service.requests["request-14"]["expiresAt"] = 0
        second = await service.submit(
            EvaluationRequest(subject="robot_arm.virtual", suite="suite/lab-demo-pick-place@1")
        )
        assert manager.created.empty()
        finish_first.set()
        assert await asyncio.wait_for(manager.created.get(), 2) == "session-11"
        assert not second_started.is_set()
        manager.sessions["session-11"].state, manager.sessions["session-11"].source = "STOPPED", None
        manager.sessions["session-11"].cleanup_complete = True
        service.wake()
        assert await asyncio.wait_for(manager.created.get(), 2) == "session-13"
        assert not second_started.is_set()
        manager.sessions["session-13"].state, manager.sessions["session-13"].source = "STOPPED", None
        manager.sessions["session-13"].cleanup_complete = True
        service.wake()
        await asyncio.wait_for(second_started.wait(), 2)
        assert calls == [first["evaluationId"], second["evaluationId"]]
        assert service.request("request-12")["status"] == "cancelled"
        assert service.request("request-14")["status"] == "expired"
        assert set(manager.sessions) == {"session-11", "session-13"}
    finally:
        await service.close()


@pytest.mark.parametrize("startup_failure", [False, True])
async def test_failed_cleanup_keeps_slot_even_after_stopped_until_explicit_cleanup(tmp_path, startup_failure):
    manager = Manager(tmp_path)
    service = EvaluationService(manager)
    permit_cleanup = False
    original_create = manager.create

    class Owned(Session):
        def __init__(self, identifier):
            self.session_id = "owned-evaluation"
            self.evaluation_id = identifier
            self.state = "FAILED" if startup_failure else "RUNNING"
            self.source = True
            self._starting_source = None
            self.cleanup_complete = False
            self._recording_revision_ready = True
            self.recording = SimpleNamespace(metadata={"status": "recording"})

        async def close(self):
            if not startup_failure:
                self.state, self.source = "STOPPED", None
            if not permit_cleanup:
                raise OSError(
                    "Source cleanup failed" if startup_failure else "Recording close failed after STOPPED"
                )
            self.state, self.source = "STOPPED", None
            self.recording.metadata["status"] = "complete"
            self.cleanup_complete = True

    async def create(scene, source, options, *, scene_version=None, evaluation_id=None):
        if evaluation_id is None:
            return await original_create(scene, source, options, scene_version=scene_version)
        session = Owned(evaluation_id)
        manager.sessions[session.session_id] = session
        if startup_failure:
            raise HTTPException(503, {"code": "source_unavailable"})
        return session

    async def episode(session, suite, case):
        return {"caseId": case["id"], "success": False}

    service._validate_session = lambda *args: {"source": "synthetic"}
    service._episode = episode
    manager.create = create
    await service.startup()
    try:
        evaluation = await service.submit(
            EvaluationRequest(subject="robot_arm.virtual", suite="suite/lab-demo-pick-place@1")
        )
        for _ in range(200):
            if service._blocked and service.store.records[evaluation["evaluationId"]]["status"] == "failed":
                break
            await asyncio.sleep(0.005)
        assert service._blocked
        record = service.store.records[evaluation["evaluationId"]]
        assert record["status"] == "failed" and record["error"]["code"] == "cleanup_failed"
        assert not service.store.rows
        _, status = await service.create_session(
            None,
            "synthetic",
            SessionOptions(seed=99),
            scene_version="lab-demo@3",
            request_id="request-after-failure",
        )
        assert status == 202 and manager.created.empty()
        assert service.request("request-after-failure")["status"] == "queued"
        service.store.save({"evaluationId": "eval-unrelated", "split": "train", "status": "completed"})
        await service.cleanup("eval-unrelated")
        assert service._blocked and manager.created.empty()
        permit_cleanup = True
        for _ in range(200):
            if service._active_evaluation is None:
                break
            await asyncio.sleep(0.005)
        await service.cleanup(evaluation["evaluationId"])
        assert await asyncio.wait_for(manager.created.get(), 2) == "session-99"
    finally:
        await service.close()


async def test_starting_ticket_cancellation_is_owned_until_new_session_is_closed(tmp_path):
    manager = Manager(tmp_path)
    service = EvaluationService(manager)
    eval_started, finish_eval, source_entered, finish_source = (asyncio.Event() for _ in range(4))
    original_create = manager.create

    async def evaluate(identifier):
        eval_started.set()
        await finish_eval.wait()

    async def create(*args, **kwargs):
        source_entered.set()
        await finish_source.wait()
        return await original_create(*args, **kwargs)

    manager.create, service._evaluate = create, evaluate
    await service.startup()
    try:
        await service.submit(
            EvaluationRequest(subject="robot_arm.virtual", suite="suite/lab-demo-pick-place@1")
        )
        await asyncio.wait_for(eval_started.wait(), 2)
        await service.create_session(
            None,
            "synthetic",
            SessionOptions(seed=55),
            scene_version="lab-demo@3",
            request_id="request-abandoned",
        )
        finish_eval.set()
        await asyncio.wait_for(source_entered.wait(), 2)
        assert service.request("request-abandoned")["status"] == "starting"
        assert (await service.cancel_request("request-abandoned"))["status"] == "cancelling"
        with pytest.raises(HTTPException):
            service.require_interactive()
        finish_source.set()
        for _ in range(200):
            if service.request("request-abandoned")["status"] == "cancelled":
                break
            await asyncio.sleep(0.005)
        assert service.request("request-abandoned")["status"] == "cancelled"
        assert set(manager.sessions) == {"session-55"}
        assert manager.sessions["session-55"].cleanup_complete
        assert not manager.sessions["session-55"].holds_resources
    finally:
        finish_source.set()
        finish_eval.set()
        await service.close()


async def test_ordinary_recording_close_failure_holds_slot_after_stopped(tmp_path):
    manager = Manager(tmp_path)
    service = EvaluationService(manager)
    permit_close = False
    evaluation_started = asyncio.Event()

    async def flush():
        pass

    async def close_recording():
        if not permit_close:
            raise OSError("Recording close failed after source cleanup")

    ordinary = ManagedSession(
        "lab-demo",
        "synthetic",
        SessionOptions(),
        Settings(state_root=tmp_path / "state", recordings_root=tmp_path / "recordings"),
        None,
        SimpleNamespace(retain=lambda: None),
        None,
        SimpleNamespace(flush=flush),
        on_closed=service.wake,
    )
    ordinary.state = "RUNNING"
    ordinary.recording = SimpleNamespace(close=close_recording)
    manager.sessions[ordinary.session_id] = ordinary

    async def evaluate(identifier):
        evaluation_started.set()

    service._evaluate = evaluate
    await service.startup()
    try:
        evaluation = await service.submit(
            EvaluationRequest(subject="robot_arm.virtual", suite="suite/lab-demo-pick-place@1")
        )
        with pytest.raises(OSError):
            await ordinary.close()
        assert ordinary.state == "STOPPED" and ordinary.source is None
        assert ordinary.holds_resources
        service.wake()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(evaluation_started.wait(), 0.05)
        assert service.store.records[evaluation["evaluationId"]]["status"] == "queued"
        permit_close = True
        await ordinary.close()
        await asyncio.wait_for(evaluation_started.wait(), 2)
        assert ordinary.cleanup_complete and not ordinary.holds_resources
    finally:
        permit_close = True
        await ordinary.close()
        await service.close()


async def test_immediate_create_waiting_for_manager_lock_keeps_later_evaluation_queued(tmp_path):
    manager = Manager(tmp_path)
    service = EvaluationService(manager)
    ordinary_waiting, coordinator_waiting, evaluator_called = (asyncio.Event() for _ in range(3))
    original_create = manager.create

    class ObservedAdmission(asyncio.Lock):
        async def acquire(self):
            if asyncio.current_task() is service._worker and self.locked():
                coordinator_waiting.set()
            return await super().acquire()

    async def create(scene, source, options, *, scene_version=None, evaluation_id=None):
        if evaluation_id is None:
            ordinary_waiting.set()
        async with manager._create_lock:
            if evaluation_id is not None:
                evaluator_called.set()
                if any(session.holds_resources for session in manager.sessions.values()):
                    raise HTTPException(409, {"code": "session_exists"})
            return await original_create(scene, source, options, scene_version=scene_version)

    service._admission = ObservedAdmission()
    manager.create = create
    await service.startup()
    await manager._create_lock.acquire()
    ordinary = asyncio.create_task(
        service.create_session(None, "synthetic", SessionOptions(seed=91), scene_version="lab-demo@3")
    )
    try:
        await asyncio.wait_for(ordinary_waiting.wait(), 2)
        evaluation = await service.submit(
            EvaluationRequest(subject="robot_arm.virtual", suite="suite/lab-demo-pick-place@1")
        )
        await service._admission.acquire()
        service.wake()
        await asyncio.wait_for(coordinator_waiting.wait(), 2)
        # Resume the already-waiting coordinator before the already-waiting ordinary creator.
        service._admission.release()
        manager._create_lock.release()
        result, status = await asyncio.wait_for(ordinary, 2)
        assert status == 201 and result["sessionId"] == "session-91"
        await asyncio.sleep(0)
        assert service.store.records[evaluation["evaluationId"]]["status"] == "queued"
        assert service._active_evaluation is None and not evaluator_called.is_set()
        assert service._ordinary_creates == 0
    finally:
        if service._admission.locked():
            service._admission.release()
        if manager._create_lock.locked():
            manager._create_lock.release()
        if not ordinary.done():
            ordinary.cancel()
        await service.close()


@pytest.mark.parametrize("outcome", ["started", "failed", "cancelled"])
async def test_dequeued_ticket_reserves_real_manager_reload_during_starting_write(tmp_path, outcome):
    manager = SessionManager(Settings(
        state_root=tmp_path / "state", recordings_root=tmp_path / "recordings", recordings_enabled=False,
    ))
    intake = Manager(tmp_path)
    manager._library = intake.library
    intake.sessions = manager.sessions
    service = EvaluationService(manager)
    manager.evaluations = service
    old = ManagedSession(
        "lab-demo", "synthetic", SessionOptions(seed=10), manager.settings, None,
        manager.recordings, manager.approvals, manager.plans,
    )
    old.state, old.cleanup_complete = "STOPPED", True
    manager.sessions[old.session_id] = old
    reloads = []

    async def reload_old():
        reloads.append(old.session_id)
        old.state, old.source, old.cleanup_complete = "RUNNING", True, False

    async def create(*args, **kwargs):
        # Reproduce the Manager intake's reuse when a later reload steals the slot.
        if old.holds_resources:
            return old
        return await intake.create(*args, **kwargs)

    old.reload, manager.create = reload_old, create
    entered, release = asyncio.Event(), Event()
    loop = asyncio.get_running_loop()
    save = service._save_request

    def blocked_save(value):
        if value["status"] == "starting":
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5), "Starting persistence was not released"
            if outcome == "failed":
                raise OSError("Injected starting persistence failure")
        save(value)

    service._save_request = blocked_save
    await service.startup()
    service._active_evaluation = "eval-held"
    await service.create_session(
        None, "synthetic", SessionOptions(seed=77), scene_version="lab-demo@3", request_id="request-gap",
    )
    service._active_evaluation = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert not service._queue and not manager._create_lock.locked()
        with pytest.raises(HTTPException) as blocked:
            await manager.reload(old.session_id)
        assert blocked.value.detail["code"] == "evaluation_busy"
        if outcome == "cancelled":
            service._worker.cancel()
            await asyncio.sleep(0)
            assert not service._worker.done()
            with pytest.raises(HTTPException):
                await manager.reload(old.session_id)
        release.set()
        for _ in range(200):
            if service.request("request-gap")["status"] == outcome and service._starting_request is None:
                break
            await asyncio.sleep(0.005)
        assert service.request("request-gap")["status"] == outcome
        assert service._starting_request is None and not reloads and not service._options
        if outcome == "started":
            assert service.request("request-gap")["sessionId"] == "session-77"
            assert manager.sessions["session-77"].summary()["seed"] == 77
        else:
            assert set(manager.sessions) == {old.session_id}
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await service._worker
        service.require_interactive()
    finally:
        release.set()
        old.source = None
        await service.close()
        await manager.close()

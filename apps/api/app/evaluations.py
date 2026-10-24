"""Sequential embodied evaluations and explicit admission to the one session slot."""

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from collections import deque
from copy import deepcopy
from datetime import UTC, datetime
from fnmatch import fnmatchcase

from fastapi import HTTPException, Request
from pydantic import Field, StrictStr

from apps.api.app.evaluation_views import public_evaluation, public_score
from packages.info.conditions import condition_state, validate_conditions
from packages.info.evaluations import EvaluationStore, evaluation_decision, wilson_interval
from packages.info.invocations import TERMINAL
from packages.info.store import StateStore
from packages.library.common import REPO_ROOT
from packages.library.suites import SuiteLibrary
from packages.library.transactions import durable_json
from packages.protocol.internal import SessionOptions
from packages.protocol.schema import ActionRequest, Actor, EntityObservations, EntitySnapshot, Model


def now():
    return datetime.now(UTC).isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def runtime_fingerprint():
    digest = hashlib.sha256()
    for directory in ("packages/programs", "packages/info", "packages/sim_runtime", "apps/api/app/sources"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            digest.update(path.relative_to(REPO_ROOT).as_posix().encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


async def persist(call, *arguments):
    """Finish an owned disk mutation before cancellation starts cleanup or another write."""
    task = asyncio.create_task(asyncio.to_thread(call, *arguments))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


class EvaluationRequest(Model):
    subject: StrictStr = Field(min_length=1)
    suite: StrictStr = Field(min_length=1)
    split: StrictStr = "train"
    planId: StrictStr | None = None
    planRevision: int | None = Field(default=None, ge=1)


class EvaluationService:
    def __init__(self, manager):
        self.manager = manager
        self.suites = SuiteLibrary(manager.library_assets.roots)
        self.store = EvaluationStore(manager.settings.state_root, on_change=self._changed)
        self.requests = {}
        self._options = {}
        self._queue = deque()
        self._wake = asyncio.Event()
        self._worker = None
        self._running = None
        self._closing = False
        self._blocked = set()
        self._active_evaluation = None
        self._starting_request = None
        self._ordinary_creates = 0
        self._runtime_hash = runtime_fingerprint()
        self._admission = asyncio.Lock()
        self._admitting = {}
        self._cancellation_requested = set()

    def _changed(self, record):
        notebooks = getattr(self.manager, "notebooks", None)
        if notebooks is not None:
            notebooks.observe(record)

    def _capture_completion(self, record):
        notebooks = getattr(self.manager, "notebooks", None)
        if notebooks is not None:
            notebooks.complete_evaluation(record)

    async def startup(self):
        await asyncio.to_thread(self.suites.refresh)
        await asyncio.to_thread(self.store.startup)
        directory = self.store.root / "session-requests"
        if directory.exists():
            for path in directory.glob("request-*.json"):
                value = json.loads(await asyncio.to_thread(path.read_bytes))
                if value["status"] in ("queued", "starting", "cancelling"):
                    value.update(status="failed", error={"code": "gateway_restarted"})
                    await asyncio.to_thread(durable_json, path, value)
                self.requests[value["requestId"]] = value
        self._worker = asyncio.create_task(self._run(), name="evaluation-admission")

    def wake(self):
        self._wake.set()

    def _busy(self):
        return (
            self._ordinary_creates > 0
            or self.manager._create_lock.locked()
            or self.manager._starting_session is not None
            or any(session.holds_resources for session in self.manager.sessions.values())
        )

    def require_interactive(self):
        from packages.agents.scope import current_scope

        scope = current_scope()
        if (scope is not None and self._active_evaluation == scope.evaluation_id
                and self._active_evaluation is not None and not self._starting_request and not self._blocked):
            return
        if self._active_evaluation or self._starting_request or self._blocked:
            raise HTTPException(409, {"code": "evaluation_busy", "message": "The session slot is reserved"})

    def status(self):
        return {
            "evaluationId": self._active_evaluation,
            "blocked": bool(self._blocked),
            "queued": len(self._queue),
        }

    def _save_request(self, value):
        durable_json(self.store.root / "session-requests" / (value["requestId"] + ".json"), value)
        self.requests[value["requestId"]] = value

    async def create_session(self, scene_id, source, options, *, scene_version=None, request_id=None):
        async with self._admission:
            if (
                self._active_evaluation
                or self._starting_request
                or self._queue
                or self._blocked
                or request_id in self.requests
            ):
                return await self._queue_session(
                    scene_id,
                    source,
                    options,
                    scene_version=scene_version,
                    request_id=request_id,
                )
            self._ordinary_creates += 1
        # Source startup holds the Manager's create lock, while evaluation submission remains immediate.
        try:
            session = await self.manager.create(scene_id, source, options, scene_version=scene_version)
            return session.summary(), 201
        finally:
            self._ordinary_creates -= 1
            self.wake()

    async def _queue_session(self, scene_id, source, options, *, scene_version=None, request_id=None):
        selected = await asyncio.to_thread(
            self.manager.library.resolve,
            scene_version or scene_id,
            launch=True,
            source=source,
        )
        selected_source = source or selected.metadata["sources"][0]
        selector = {"sceneVersion": selected.id, "source": selected_source}
        identity = fingerprint(selector | options.model_dump(mode="json"))
        if request_id is not None:
            existing = self.requests.get(request_id)
            if existing:
                if existing["requestHash"] != identity:
                    raise HTTPException(409, {"code": "request_conflict"})
                return self.public_request(existing), 202
        request_id = request_id or "request-" + uuid.uuid4().hex
        if (
            not request_id.startswith("request-")
            or len(request_id) > 80
            or any(
                character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for character in request_id
            )
        ):
            raise HTTPException(422, {"code": "invalid_request_id"})
        value = {
            "requestId": request_id,
            "requestHash": identity,
            "status": "queued",
            **selector,
            "at": now(),
            "expiresAt": time.time() + 600,
        }
        self._options[request_id] = options
        await persist(self._save_request, value)
        self._queue.append(("session", request_id))
        self.wake()
        return self.public_request(value), 202

    @staticmethod
    def public_request(value):
        return {
            key: deepcopy(value[key])
            for key in (
                "requestId",
                "status",
                "sceneVersion",
                "source",
                "at",
                "expiresAt",
                "sessionId",
                "error",
            )
            if key in value
        }

    async def cancel_request(self, request_id):
        current = self.request(request_id)
        if current["status"] not in ("queued", "starting"):
            return self.public_request(current)
        async with self._admission:
            value = self.request(request_id)
            if value["status"] == "queued":
                value = value | {"status": "cancelled"}
                await persist(self._save_request, value)
                self._options.pop(request_id, None)
                self._queue = deque(item for item in self._queue if item != ("session", request_id))
                self.wake()
            elif value["status"] == "starting":
                value = value | {"status": "cancelling"}
                await persist(self._save_request, value)
            return self.public_request(value)

    def request(self, identifier):
        if identifier not in self.requests:
            raise HTTPException(404, {"code": "unknown_session_request"})
        return self.requests[identifier]

    async def submit(self, body):
        if body.split != "train":
            raise HTTPException(403, {"code": "test_requires_promotion"})
        if body.subject.startswith(("policy/", "checkpoint/", "program/")):
            raise HTTPException(
                422, {"code": "unsupported_subject", "message": "Select an existing scene-bound program ID"}
            )
        suite = self.suites.resolve(body.suite)
        if suite.metadata["layer"] != "embodied":
            raise HTTPException(422, {"code": "agent_runner_unavailable",
                                      "message": "Agent SDK execution is not available"})
        selected = await asyncio.to_thread(
            self.manager.library.resolve,
            suite.metadata["scene"],
            launch=True,
            source=suite.metadata["source"],
        )
        if selected.content_hash != suite.metadata["sceneContentHash"]:
            raise HTTPException(409, {"code": "scene_changed"})
        declarations = [
            item.binding
            for item in selected.declarations(suite.metadata["source"]).declarations
            if item.binding.get("key") == suite.task["robot"]
            or ("match" in item.binding and fnmatchcase(suite.task["robot"], item.binding["match"]))
        ]
        if (
            len(declarations) != 1
            or declarations[0].get("program") != body.subject
            or declarations[0].get("type") != suite.task["implements"].rsplit(".", 1)[0]
        ):
            raise HTTPException(422, {"code": "subject_mismatch"})
        identifier = "eval-" + uuid.uuid4().hex
        record = {
            "evaluationId": identifier,
            "suite": suite.id,
            "suiteContentHash": suite.content_hash,
            "subject": body.subject,
            "split": "train",
            "status": "queued",
            "episodes": [],
            "at": now(),
            "runtimeHash": self._runtime_hash,
        }
        notebooks = getattr(self.manager, "notebooks", None)
        context = (
            await asyncio.to_thread(notebooks.prepare_evaluation, body, suite, selected)
            if notebooks else None
        )
        async with self._admission:
            if notebooks is not None:
                record.update(notebooks.admit_evaluation(context))
            self._admitting[identifier] = record
            try:
                try:
                    await persist(self.store.save, record)
                except asyncio.CancelledError:
                    # A disconnected caller does not erase a confirmed durable intent.
                    if identifier in self.store.records:
                        self._queue.append(("evaluation", identifier))
                        self.wake()
                    raise
                self._queue.append(("evaluation", identifier))
                self.wake()
            finally:
                self._admitting.pop(identifier, None)
        return deepcopy(record)

    async def _run(self):
        while not self._closing:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1)
            except TimeoutError:
                pass  # Expire explicit tickets even while an interactive session remains active.
            self._wake.clear()
            running = None
            launch = None
            async with self._admission:
                for kind, identifier in list(self._queue):
                    if kind == "session":
                        value = self.requests[identifier]
                        if value["status"] == "queued" and value["expiresAt"] <= time.time():
                            await persist(self._save_request, value | {"status": "expired"})
                            self._options.pop(identifier, None)
                self._queue = deque(
                    (kind, identifier)
                    for kind, identifier in self._queue
                    if kind != "session" or self.requests[identifier]["status"] == "queued"
                )
                if self._closing or self._blocked or self._busy():
                    continue
                while self._queue:
                    kind, identifier = self._queue.popleft()
                    if kind == "session":
                        value = self.requests[identifier]
                        if value["status"] != "queued":
                            continue
                        self._starting_request = identifier
                        launch = (value, self._options.pop(identifier))
                    else:
                        self._active_evaluation = identifier
                        running = self._running = asyncio.create_task(
                            self._evaluate(identifier),
                            name=identifier,
                        )
                    self.wake()
                    break
            if launch is not None:
                await self._launch(*launch)
            if running is not None:
                try:
                    await running
                except asyncio.CancelledError:
                    if self.store.records[identifier]["status"] == "queued":
                        await persist(
                            self.store.save,
                            self.store.records[identifier]
                            | {
                                "status": "cancelled",
                                "endedAt": now(),
                            },
                        )
                    if self._closing:
                        raise
                finally:
                    self._running = None
                    self._active_evaluation = None
                    self._cancellation_requested.discard(identifier)
                    self.wake()

    async def _launch(self, value, options):
        identifier = value["requestId"]
        before = set(self.manager.sessions)
        session = None
        closing = False
        try:
            # The reservation already covers this disk await and its failure/cancellation cleanup.
            async with self._admission:
                current = self.request(identifier)
                if current["status"] == "cancelling":
                    await persist(self._save_request, current | {"status": "cancelled"})
                    return
                if current["status"] != "queued":
                    return
                value = current | {"status": "starting"}
                await persist(self._save_request, value)
            session = await self.manager.create(
                None, value["source"], options, scene_version=value["sceneVersion"]
            )
            async with self._admission:
                if self.requests[identifier]["status"] != "cancelling":
                    await persist(
                        self._save_request,
                        value
                        | {
                            "status": "started",
                            "sessionId": session.session_id,
                        },
                    )
                    return
            closing = True
            await session.close()
            await persist(
                self._save_request, value | {"status": "cancelled", "sessionId": session.session_id}
            )
        except BaseException as error:
            held = session or next(
                (self.manager.sessions[key] for key in self.manager.sessions.keys() - before), None
            )
            result = value | {
                "status": "failed",
                "error": {
                    "code": "cleanup_failed" if closing else "session_start_failed",
                },
            }
            if held is not None:
                result["sessionId"] = held.session_id
            if isinstance(error, asyncio.CancelledError):
                try:
                    if held is not None:
                        await held.close()
                    result.update(status="cancelled")
                    result.pop("error", None)
                except BaseException:
                    result["error"] = {"code": "cleanup_failed"}
            await persist(self._save_request, result)
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            self._starting_request = None
            self.wake()

    async def _evaluate(self, identifier):
        if self.store.records[identifier].get("layer") == "agent":
            return await self.manager.agent_runs.evaluate(identifier)
        record = deepcopy(self.store.records[identifier])
        record.update(status="running", startedAt=now())
        session = None
        started = time.monotonic()
        try:
            await persist(self.store.save, record)
            suite = self.suites.resolve(record["suite"])
            if suite.content_hash != record["suiteContentHash"]:
                raise HTTPException(409, {"code": "suite_changed"})
            for case in suite.metadata["episodes"]:
                if case["split"] != "train":
                    continue
                suite.verify()
                if runtime_fingerprint() != self._runtime_hash:
                    raise HTTPException(409, {"code": "runtime_changed"})
                selected = await asyncio.to_thread(
                    self.manager.library.resolve,
                    suite.metadata["scene"],
                    launch=True,
                    source=suite.metadata["source"],
                )
                if selected.content_hash != suite.metadata["sceneContentHash"]:
                    raise HTTPException(409, {"code": "scene_changed"})
                if "attemptedEpisodes" in record:
                    # Admitted attempts include invalid/cancelled starts for budget
                    # accounting; they never replace the statistical denominator.
                    before_attempt = record["attemptedEpisodes"]
                    record["attemptedEpisodes"] = before_attempt + 1
                    try:
                        await persist(self.store.save, record)
                    except BaseException:
                        # No Source attempt has been issued yet. Preserve the failed
                        # evaluation, but do not manufacture an executed episode.
                        record["attemptedEpisodes"] = before_attempt
                        raise
                session = await self.manager.create(
                    None,
                    suite.metadata["source"],
                    SessionOptions(
                        seed=suite.seeds[case["id"]],
                        continueWithoutObservers=True,
                        approval={"high": "require"},
                    ),
                    scene_version=selected.id,
                    evaluation_id=identifier,
                )
                if (
                    not session.recording
                    or not session._recording_revision_ready
                    or session.recording.metadata["status"] != "recording"
                ):
                    raise HTTPException(503, {"code": "recording_unavailable"})
                key = self._validate_session(session, suite, case, record["subject"])
                if record.get("key", key) != key:
                    raise HTTPException(409, {"code": "not_comparable"})
                record["key"] = key
                outcome = await self._episode(session, suite, case)
                await session.close()
                if session.recording.metadata["status"] != "complete":
                    raise HTTPException(503, {"code": "recording_incomplete"})
                outcome["recordingId"] = session.session_id
                record["episodes"].append(outcome)
                session = None
                await persist(self.store.save, record)
            suite.verify()
            if runtime_fingerprint() != self._runtime_hash:
                raise HTTPException(409, {"code": "runtime_changed"})
            successes = sum(episode["success"] for episode in record["episodes"])
            episodes = len(record["episodes"])
            reference = self.store.reference(record["key"])
            decision = evaluation_decision(
                successes,
                episodes,
                None if reference is None else reference["score"],
            )
            row = {
                "evaluationId": identifier,
                "suite": suite.id,
                "subject": record["subject"],
                "version": self._runtime_hash,
                "key": record["key"],
                "split": "train",
                "successes": successes,
                "episodes": episodes,
                "score": successes / episodes,
                "ci95": wilson_interval(successes, episodes),
                "decision": decision,
                "reference": reference["evaluationId"] if reference else None,
                "phases": {
                    stage["id"]: {
                        "passed": sum(episode["milestones"][stage["id"]] for episode in record["episodes"]),
                        "episodes": episodes,
                        "rate": sum(episode["milestones"][stage["id"]] for episode in record["episodes"])
                        / episodes,
                    }
                    for stage in suite.task["milestones"]
                },
                "recordings": [episode["recordingId"] for episode in record["episodes"]],
                "durationSeconds": time.monotonic() - started,
                "at": now(),
            }
            row.update({name: record[name] for name in ("planId", "planRevision", "goalId")
                        if name in record})
            await persist(self.store.append, row)
            record.update(status="completed", result=row, endedAt=now())
        except BaseException as exc:
            committed = next((row for row in self.store.rows if row["evaluationId"] == identifier), None)
            if committed is not None:
                record.update(status="completed", result=committed, endedAt=now())
                self._capture_completion(record)
                await persist(self.store.save, record)
                if isinstance(exc, asyncio.CancelledError) and self._closing:
                    raise
                return
            record.update(
                status="cancelled" if isinstance(exc, asyncio.CancelledError) else "failed",
                error={
                    "code": exc.detail.get("code", "evaluation_failed")
                    if isinstance(exc, HTTPException)
                    else "evaluation_failed"
                },
                endedAt=now(),
            )
            # A failed start may still own a discoverable source even if create raised.
            owned = [
                item
                for item in self.manager.sessions.values()
                if item.evaluation_id == identifier and item.holds_resources
            ]
            for item in owned:
                try:
                    await item.close()
                except BaseException:
                    self._blocked.add(identifier)
                    record["error"] = {"code": "cleanup_failed"}
            if session is not None and session not in owned:
                try:
                    await session.close()
                except BaseException:
                    self._blocked.add(identifier)
                    record["error"] = {"code": "cleanup_failed"}
            if isinstance(exc, asyncio.CancelledError) and self._closing:
                self._capture_completion(record)
                await persist(self.store.save, record)
                raise
        self._capture_completion(record)
        await persist(self.store.save, record)

    @staticmethod
    def _validate_session(session, suite, case, subject):
        task = suite.task
        if (
            session.scene_version.id != suite.metadata["scene"]
            or session.scene_version.content_hash != suite.metadata["sceneContentHash"]
            or session.kind != suite.metadata["source"]
        ):
            raise HTTPException(409, {"code": "scene_changed"})
        binding = session.catalog.entities.get(task["robot"])
        type_id, action = task["implements"].rsplit(".", 1)
        if not binding or binding["program"] != subject or binding["type"] != type_id:
            raise HTTPException(422, {"code": "subject_mismatch"})
        try:
            session.catalog.validate_params(task["robot"], action, case["params"])

            def schema(key, prop):
                return session.catalog.property(key, prop)["schema"]

            validate_conditions([task["success"]], task["robot"], schema)
            for stage in task["milestones"]:
                validate_conditions([stage["when"]], task["robot"], schema)
        except (KeyError, ValueError, TypeError):
            raise HTTPException(422, {"code": "invalid_suite_binding"}) from None
        source = session.manifest.source
        return {
            "suite": suite.id,
            "suiteContentHash": suite.content_hash,
            "scene": session.scene_version.id,
            "sceneContentHash": session.scene_version.content_hash,
            "source": source.kind,
            "newtonVersion": source.newtonVersion,
            "solver": source.solver,
            "target": task["robot"],
            "bindingHash": fingerprint(binding),
        }

    async def _episode(self, session, suite, case):
        task = suite.task
        mirror = StateStore(session.manifest.sceneRevision, session.epoch)
        mirror.apply(session.info_snapshot())
        stages = {stage["id"]: False for stage in task["milestones"]}

        def observe():
            for stage in task["milestones"]:
                stages[stage["id"]] |= (
                    condition_state(
                        [stage["when"]],
                        task["robot"],
                        mirror.values(),
                        mirror.metadata(),
                    )
                    is True
                )

        observe()
        cursor = session.info.event_seq
        pipeline = await session.action_pipeline()
        actor = Actor(kind="user", name="platform-evaluator")
        invocation = await pipeline.invoke(
            ActionRequest(
                requestId="evaluation-" + uuid.uuid4().hex,
                entityKey=task["robot"],
                action=task["implements"].rsplit(".", 1)[1],
                params=case["params"],
            ),
            "operator",
            actor,
        )
        if invocation.status == "pending_approval":
            invocation = await pipeline.approve(
                invocation.invocationId, "deny", "operator", actor, "Human gates are denied during evaluation"
            )
        deadline = time.monotonic() + task["timeoutSeconds"]
        while True:
            events = await session.info.events_after(cursor, 0 if invocation.status in TERMINAL else 0.1)
            if events["gap"]:
                raise HTTPException(503, {"code": "evaluation_observation_gap"})
            for event in events["events"]:
                value = event["message"]
                if value["type"] in ("entity.snapshot", "entity.observations"):
                    model = EntitySnapshot if value["type"] == "entity.snapshot" else EntityObservations
                    mirror.apply(model.model_validate(value))
                    observe()
            cursor = events["lastSeq"]
            invocation = pipeline.table.get(invocation.invocationId)
            if session.state not in ("RUNNING", "PAUSED"):
                raise HTTPException(503, {"code": "source_unavailable"})
            if invocation.status in TERMINAL:
                break
            if time.monotonic() >= deadline:
                await pipeline.cancel(invocation.invocationId, "operator")
                raise HTTPException(504, {"code": "episode_timeout"})
        success = (
            condition_state([task["success"]], task["robot"], session.info.values(), session.info.metadata())
            is True
        )
        return {
            "caseId": case["id"],
            "success": success,
            "milestones": stages,
            "failedStage": next(
                (name for name, passed in stages.items() if not passed), None if success else "success"
            ),
            "invocationId": invocation.invocationId,
            "invocationStatus": invocation.status,
        }

    async def cleanup(self, identifier):
        if identifier not in self.store.records or self.store.records[identifier]["split"] != "train":
            raise HTTPException(404, {"code": "unknown_evaluation"})
        if self._active_evaluation == identifier:
            raise HTTPException(409, {"code": "evaluation_busy"})
        for session in self.manager.sessions.values():
            if session.evaluation_id == identifier:
                await session.close()
        record = self.store.records[identifier]
        if record.get("layer") == "agent":
            await self.manager.agent_runs.cleanup(record["jobId"])
        self._blocked.discard(identifier)
        self.wake()
        return self.status()

    async def cancel_evaluation(self, identifier):
        record = self.store.records.get(identifier)
        if record is None or record["split"] != "train":
            raise HTTPException(404, {"code": "unknown_evaluation"})
        if self._active_evaluation == identifier and self._running:
            if identifier not in self._cancellation_requested and not self._running.done():
                self._cancellation_requested.add(identifier)
                self._running.cancel()
        elif record["status"] == "queued":
            self._queue = deque(item for item in self._queue if item != ("evaluation", identifier))
            cancelled = record | {"status": "cancelled", "endedAt": now()}
            self._capture_completion(cancelled)
            await persist(self.store.save, cancelled)
        self.wake()
        return public_evaluation(self.store.records[identifier], self.store.rows)

    async def close(self):
        self._closing = True
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        for kind, identifier in self._queue:
            if kind == "session" and self.requests[identifier]["status"] == "queued":
                await self.cancel_request(identifier)
            if kind == "evaluation":
                record = self.store.records[identifier] | {"status": "cancelled", "endedAt": now()}
                await persist(self.store.save, record)


def install_evaluation_routes(app, service, role_for):
    def operator(request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})

    @app.get("/api/v1/suites")
    async def suites():
        return {"suites": service.suites.list()}

    @app.get("/api/v1/suites/{name}/{revision}")
    async def suite(name: str, revision: str):
        return service.suites.resolve(f"suite/{name}@{revision}").public()

    @app.get("/api/v1/suites/{name}/{revision}/scoreboard")
    async def scoreboard(name: str, revision: str, subject: str | None = None,
                         compare: str | None = None, against: str | None = None):
        version = service.suites.resolve(f"suite/{name}@{revision}")
        rows = [row for row in service.store.rows if row["suite"] == version.id and row["split"] == "train"
                and (subject is None or row["subject"] == subject)]
        if (compare is None) != (against is None):
            raise HTTPException(
                422, {"code": "invalid_comparison", "message": "Provide compare and against together"}
            )
        result = {"rows": [public_score(row, rows) for row in rows]}
        if compare is not None:
            selected = [next((row for row in rows if row["evaluationId"] == identifier), None)
                        for identifier in (compare, against)]
            if any(row is None for row in selected):
                raise HTTPException(404, {"code": "unknown_evaluation"})
            first, second = selected
            if first["key"] != second["key"]:
                raise HTTPException(
                    409, {"code": "not_comparable", "message": "Evaluation conditions differ"}
                )
            result["comparison"] = {"compare": compare, "against": against,
                                    "scoreDelta": first["score"] - second["score"]}
        return result

    @app.post("/api/v1/evaluations", status_code=202)
    async def create_evaluation(body: EvaluationRequest, request: Request):
        operator(request)
        return public_evaluation(await service.submit(body), service.store.rows)

    @app.get("/api/v1/evaluations")
    async def evaluations():
        return {
            "evaluations": [
                public_evaluation(value, service.store.rows)
                for value in service.store.records.values() if value["split"] == "train"
            ]
        }

    @app.get("/api/v1/evaluations/{identifier}")
    async def evaluation(identifier: str):
        record = service.store.records.get(identifier)
        if record is None or record["split"] != "train":
            raise HTTPException(404, {"code": "unknown_evaluation"})
        return public_evaluation(record, service.store.rows)

    @app.post("/api/v1/evaluations/{identifier}/cancel")
    async def cancel(identifier: str, request: Request):
        operator(request)
        record = service.store.records.get(identifier)
        if record is None or record["split"] != "train":
            raise HTTPException(404, {"code": "unknown_evaluation"})
        if record.get("layer") == "agent":
            await service.manager.agent_runs.cancel(record["jobId"])
            return public_evaluation(service.store.records[identifier], service.store.rows)
        return await service.cancel_evaluation(identifier)

    @app.post("/api/v1/evaluations/{identifier}/cleanup")
    async def cleanup(identifier: str, request: Request):
        operator(request)
        return await service.cleanup(identifier)

    @app.get("/api/v1/session-requests/{identifier}")
    async def session_request(identifier: str):
        return service.public_request(service.request(identifier))

    @app.delete("/api/v1/session-requests/{identifier}")
    async def cancel_session_request(identifier: str, request: Request):
        operator(request)
        return await service.cancel_request(identifier)

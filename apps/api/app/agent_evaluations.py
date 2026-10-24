"""External Agent handoff on the existing evaluator queue; never starts a runtime."""

import asyncio
import hashlib
import time
import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Literal

import httpx
from fastapi import Request
from fastapi.responses import Response
from pydantic import Field

from apps.api.app.evaluation_views import public_agent_run
from apps.api.app.evaluations import now, persist
from packages.agent_evaluation.audit import CaseAudit
from packages.agent_evaluation.bundle import freeze_harness
from packages.agent_evaluation.evidence import require_case_evidence
from packages.agent_evaluation.scope import CaseGrant, observation_scope
from packages.info.agent_grading import final_observations, grade_agent_case
from packages.info.evaluation_evidence import EvidenceUnavailable
from packages.info.evaluation_identity import verify_identity
from packages.info.plans import PlanError
from packages.library.transactions import durable_json
from packages.protocol.internal import SessionOptions
from packages.protocol.schema import Model
from packages.runtime_records.native import digest, redacted


class RuntimeDeclaration(Model):
    name: str = Field(min_length=1, max_length=128)
    version: str | None = Field(default=None, min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    thinking: str | None = Field(default=None, max_length=128)


class AgentEvaluationRequest(Model):
    requestId: str = Field(min_length=1, max_length=128)
    suite: str = Field(min_length=1, max_length=256)
    harnessVersion: int = Field(ge=1, strict=True)
    runtime: RuntimeDeclaration
    purpose: Literal["train", "verification"] = "train"
    caseId: str | None = Field(default=None, max_length=128)
    wallSeconds: int = Field(default=900, ge=1, le=7200, strict=True)


class CompleteCase(Model):
    caseRunId: str = Field(min_length=1, max_length=128)
    nativeSessionId: str = Field(min_length=1, max_length=4096)
    runtimeRecordId: str | None = Field(default=None, max_length=128)
    runtime: RuntimeDeclaration | None = None


class ExternalEvaluations:
    def __init__(self, app, evaluator, agents, runtime_records):
        self.app, self.evaluator, self.manager = app, evaluator, evaluator.manager
        self.agents, self.runtime_records = agents, runtime_records
        self.grants, self.active, self.signals, self.finalized = {}, {}, {}, {}
        self._submission = asyncio.Lock()

    def directory(self, run):
        return self.evaluator.store.root / "agent-runs" / run["evaluationId"]

    def get(self, identifier):
        record = self.evaluator.store.records.get(identifier)
        if record is None or record.get("layer") != "agent":
            raise PlanError("unknown_agent_run", "Unknown external Agent evaluation", status=404)
        return record

    def document(self, record):
        return public_agent_run(record, self.evaluator.store.rows)

    async def save(self, record):
        await persist(self.evaluator.store.save, record)

    async def submit(self, name, body, *, agent_round=None):
        agent = self.agents.show(name)["id"]
        inputs = {"agentId": agent, **body.model_dump()}
        if agent_round is not None:
            inputs["agentRound"] = agent_round
        request_hash = digest(inputs)
        async with self._submission:
            previous = next(
                (
                    item
                    for item in self.evaluator.store.records.values()
                    if item.get("layer") == "agent"
                    and item.get("agentId") == agent
                    and item.get("requestId") == body.requestId
                ),
                None,
            )
            if previous is not None:
                if previous["requestHash"] != request_hash:
                    raise PlanError(
                        "agent_request_conflict", "This request ID has different fixed inputs", status=409
                    )
                return self.document(previous)
            suite = self.evaluator.suites.resolve(body.suite)
            if suite.metadata["layer"] != "agent" or suite.metadata["target"] != agent:
                raise PlanError("agent_suite_mismatch", "Select this Agent's public Agent Suite", status=422)
            if suite.metadata["source"] != "synthetic":
                raise PlanError(
                    "unsupported_agent_source",
                    "External evaluation v1 uses owned Synthetic Sessions",
                    status=422,
                )
            if body.purpose == "train" and body.caseId is not None:
                raise PlanError(
                    "invalid_agent_scope", "A train evaluation requires the complete train scope", status=422
                )
            public = suite.public()
            cases = public["cases"]
            if body.purpose == "verification":
                cases = [case for case in cases if case["id"] == body.caseId]
                if len(cases) != 1:
                    raise PlanError(
                        "invalid_agent_scope", "Select one public train case for verification", status=422
                    )
            selected = await asyncio.to_thread(
                self.manager.library.resolve,
                suite.metadata["scene"],
                launch=True,
                source=suite.metadata["source"],
            )
            if selected.content_hash != suite.metadata["sceneContentHash"]:
                raise PlanError("scene_changed", "The Suite's fixed Scene changed", status=409)
            harness = await asyncio.to_thread(freeze_harness, self.agents, agent, body.harnessVersion)
            identifier = "eval-" + uuid.uuid4().hex
            repeats = suite.metadata["runsPerCase"] if body.purpose == "train" else 1
            at = now()
            record = {
                "evaluationId": identifier,
                "layer": "agent",
                "subject": agent,
                "agentId": agent,
                "requestId": body.requestId,
                "requestHash": request_hash,
                "harnessVersion": body.harnessVersion,
                "suite": suite.id,
                "suiteContentHash": suite.content_hash,
                "split": "train",
                "purpose": body.purpose,
                "runtime": body.runtime.model_dump(),
                "runtimeHash": self.evaluator._runtime_hash,
                "at": at,
                "createdAt": at,
                "deadlineAt": (datetime.now(UTC) + timedelta(seconds=body.wallSeconds)).isoformat(),
                "status": "queued",
                "episodes": [],
                "cases": [
                    {
                        "caseRunId": "case-" + uuid.uuid4().hex,
                        "caseId": case["id"],
                        "repeat": repeat,
                        "status": "waiting",
                        "artifacts": [],
                        "usage": None,
                    }
                    for case in cases
                    for repeat in range(1, repeats + 1)
                ],
                "cleanup": {"complete": True, "blocked": False},
                "pins": {
                    "harnessHash": harness["contentHash"],
                    "archiveHash": harness["archiveHash"],
                    "skills": harness["skills"],
                    "suiteContentHash": suite.content_hash,
                    "scene": selected.id,
                    "sceneContentHash": selected.content_hash,
                    "source": suite.metadata["source"],
                },
                "publicWork": {
                    "harness": harness,
                    "statements": {case["id"]: case["statement"] for case in cases},
                },
                "key": {
                    "suite": suite.id,
                    "suiteContentHash": suite.content_hash,
                    "scene": selected.id,
                    "sceneContentHash": selected.content_hash,
                    "source": suite.metadata["source"],
                    "model": body.runtime.model,
                    "thinking": body.runtime.thinking,
                    "runtime": body.runtime.name,
                    "runtimeVersion": body.runtime.version,
                    "contextProvenance": "runtime-declaration",
                },
            }
            if agent_round is not None:
                record.update(agentRound=deepcopy(agent_round), **agent_round)
            directory = self.directory(record)
            record["candidateIdentity"] = {
                "kind": "agent-harness",
                "agentId": agent,
                "harnessVersion": body.harnessVersion,
                "contentHash": harness["contentHash"],
                "archiveHash": harness["archiveHash"],
                "skills": harness["skills"],
            }
            record["identitySnapshot"] = await self.evaluator.freeze_identity(
                identifier, suite, selected, agent, candidate=record["candidateIdentity"]
            )
            directory.mkdir(parents=True, exist_ok=False)
            archive = await asyncio.to_thread(self.agents.export, agent, body.harnessVersion)
            if hashlib.sha256(archive).hexdigest() != harness["archiveHash"]:
                raise PlanError("harness_changed", "The fixed Harness archive changed", status=409)
            (directory / "harness.tar").write_bytes(archive)
            await self.evaluator.admit(record)
            return self.document(record)

    async def next(self, identifier):
        run = self.get(identifier)
        grant = self.active.get(identifier)
        if grant is None or grant.revoked or grant.case["status"] != "running":
            return {
                "runId": identifier,
                "status": "finished" if run["status"] in ("completed", "failed", "cancelled") else "pending",
            }
        grant.require()
        case = grant.case
        return {
            "runId": identifier,
            "status": "work",
            "work": {
                "caseRunId": case["caseRunId"],
                "caseId": case["caseId"],
                "repeat": case["repeat"],
                "statement": run["publicWork"]["statements"][case["caseId"]],
                "sessionId": grant.session.session_id,
                "expiresAt": run["deadlineAt"],
                "runtime": run["runtime"],
                "harness": run["publicWork"]["harness"],
                "connection": {
                    "sessionId": grant.session.session_id,
                    "token": grant.token,
                    "tokenType": "Bearer",
                },
            },
        }

    def _native_record(self, body, run):
        if body.runtime is not None and body.runtime.model_dump() != run["runtime"]:
            raise PlanError(
                "runtime_context_mismatch",
                "Observed runtime context differs from fixed declaration",
                status=409,
            )
        if body.runtimeRecordId is None:
            return {}
        record = self.runtime_records.get(body.runtimeRecordId)
        if (
            record["nativeSessionId"] != body.nativeSessionId
            or record["runtime"] != run["runtime"]["name"]
            or record["models"]
            and record["models"] != [run["runtime"]["model"]]
            or record["runtimeVersion"] is not None
            and run["runtime"]["version"] is not None
            and record["runtimeVersion"] != run["runtime"]["version"]
            or record.get("thinking") is not None
            and run["runtime"]["thinking"] is not None
            and record["thinking"] != run["runtime"]["thinking"]
        ):
            raise PlanError(
                "runtime_context_mismatch",
                "Native record identity/context differs from this case",
                status=409,
            )
        return {
            "runtimeRecordId": record["id"],
            "runtimeRecordHash": record["contentHash"],
            "usage": deepcopy(record["usage"]) | {"observationBasis": "completion-snapshot"},
            "observedRuntime": {
                "name": record["runtime"],
                "version": record["runtimeVersion"],
                "models": record["models"],
                "thinking": record["thinking"],
            },
        }

    async def complete(self, identifier, body):
        run = self.get(identifier)
        case = next((case for case in run["cases"] if case["caseRunId"] == body.caseRunId), None)
        if case is None:
            raise PlanError("unknown_case_run", "Unknown case repetition", status=404)
        complete_hash = digest(body.model_dump())
        if "completionHash" in case:
            if case["completionHash"] != complete_hash:
                raise PlanError(
                    "case_completion_conflict", "This case has a different completion signal", status=409
                )
        else:
            grant = self.active.get(identifier)
            if grant is None or grant.case["caseRunId"] != body.caseRunId:
                raise PlanError("case_not_active", "This case is not active", status=409)
            async with grant.lock:
                known = grant.case.get("completionHash")
                if known is not None:
                    if known != complete_hash:
                        raise PlanError(
                            "case_completion_conflict",
                            "This case has a different completion signal",
                            status=409,
                        )
                else:
                    grant.require()
                    if any(
                        previous.get("nativeSessionId") == body.nativeSessionId
                        and previous["caseRunId"] != body.caseRunId
                        for record in self.evaluator.store.records.values()
                        if record.get("layer") == "agent"
                        for previous in record["cases"]
                    ):
                        raise PlanError(
                            "native_session_reused",
                            "Each independent case requires a fresh native Session",
                            status=409,
                        )
                    native = self._native_record(body, run)
                    grant.case.update(
                        nativeSessionId=body.nativeSessionId,
                        completionHash=complete_hash,
                        status="finishing",
                        **native,
                    )
                    try:
                        await self.save(
                            self.evaluator.store.records[identifier] | {"cases": self._live_cases(identifier)}
                        )
                    finally:
                        stored = next(
                            case
                            for case in self.get(identifier)["cases"]
                            if case["caseRunId"] == body.caseRunId
                        )
                        if stored.get("completionHash") == complete_hash:
                            self.signals[body.caseRunId].set()
        if self.get(identifier)["status"] in ("completed", "failed", "cancelled"):
            return self.completion_response(identifier, body.caseRunId)
        event = self.finalized.setdefault(body.caseRunId, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=30)
        except TimeoutError:
            pass
        return self.completion_response(identifier, body.caseRunId)

    def completion_response(self, identifier, case_id):
        latest = self.get(identifier)
        outcome = next(case for case in latest["cases"] if case["caseRunId"] == case_id)
        return {"case": self.public_case(outcome), "run": self.document(latest)}

    def _live_cases(self, identifier):
        cases = deepcopy(self.get(identifier)["cases"])
        grant = self.active.get(identifier)
        if grant:
            cases = [
                deepcopy(grant.case) if case["caseRunId"] == grant.case["caseRunId"] else case
                for case in cases
            ]
        return cases

    @staticmethod
    def public_case(case):
        return deepcopy({key: value for key, value in case.items() if key != "completionHash"})

    async def observations(self, grant):
        base = "/api/v1/sessions/" + grant.session.session_id
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://platform-observer"
        ) as client:
            with observation_scope(grant):
                responses = [
                    await client.get(path, headers={"Authorization": "Bearer " + grant.token})
                    for path in (
                        base,
                        base + "/state",
                        "/api/v1/alerts?sessionId=" + grant.session.session_id,
                        base,
                    )
                ]
                if any(response.status_code != 200 for response in responses):
                    raise PlanError(
                        "agent_evidence_unavailable", "Final platform HTTP observation failed", status=503
                    )
        before, state, alerts, summary = (response.json() for response in responses)
        keys = ("sessionId", "sceneRevision", "epoch")
        if any(before.get(key) != summary.get(key) for key in keys):
            raise PlanError("agent_evidence_unavailable", "Final observation scope changed", status=503)
        try:
            scope = {
                "sessionId": grant.session.session_id,
                "sceneRevision": responses[1].headers["x-spatial-scene-revision"],
                "epoch": int(responses[1].headers["x-spatial-epoch"]),
            }
        except (KeyError, ValueError):
            raise PlanError(
                "agent_evidence_unavailable", "Final observation scope is unavailable", status=503
            ) from None
        return {
            "session": summary,
            "scope": scope,
            "state": state,
            "alerts": alerts["alerts"],
            "observedAt": now(),
        }

    def artifact(self, run, case, name):
        if name not in ("trace", "final", "score"):
            raise PlanError("unknown_agent_artifact", "Unknown Agent evidence", status=404)
        path = self.directory(run) / case["caseRunId"] / (name + (".jsonl" if name == "trace" else ".json"))
        root = self.evaluator.store.root.resolve()
        if (
            not path.exists()
            or path.is_symlink()
            or path.is_junction()
            or path.stat().st_nlink != 1
            or not path.resolve().is_relative_to(root)
        ):
            raise PlanError("agent_artifact_unavailable", "Agent evidence is unavailable", status=404)
        return path.read_bytes()

    async def _finalize_case(self, run, grant, suite):
        case = grant.case
        async with grant.lock:
            final = await self.observations(grant)
            cutoff = time.monotonic()
            if final["session"]["state"] not in ("RUNNING", "PAUSED"):
                raise PlanError("agent_source_failed", "The owned Source is unavailable", status=503)
            grant.audit.seal(final, observed_monotonic=cutoff)
            directory = self.directory(run) / case["caseRunId"]
            await persist(durable_json, directory / "final.json", redacted(final))
            trace = grant.audit.derived()
            try:
                require_case_evidence(suite, case["caseId"], final, trace, grant.session.catalog)
            except EvidenceUnavailable:
                raise PlanError(
                    "agent_evidence_unavailable", "Required physical observations are unavailable", status=503
                ) from None
            values, metadata = final_observations(final["state"], final["session"], final["alerts"])
            score = grade_agent_case(
                suite, case["caseId"], values, trace, cutoff - grant.audit.started, metadata
            )
            await persist(durable_json, directory / "score.json", score)
            case.update(
                score=score["score"],
                dimensions=score["dimensions"],
                metrics=score["metrics"],
                scoringCutoffAt=final["observedAt"],
                recordingId=grant.session.session_id,
            )
            grant.revoked = True
        await grant.session.close()
        grant.audit.close()
        if not grant.session.cleanup_complete or grant.session.recording.metadata["status"] != "complete":
            raise PlanError(
                "agent_recording_incomplete", "Owned recording did not close completely", status=503
            )
        if not grant.audit.healthy:
            raise PlanError("agent_trace_unavailable", "Trusted Trace could not be retained", status=503)
        case["artifacts"] = []
        self.refresh_artifacts(run, case)
        if len(case["artifacts"]) != 3:
            raise PlanError("agent_evidence_unavailable", "Required case evidence is unavailable", status=503)
        case["status"] = "completed"

    def refresh_artifacts(self, run, case):
        artifacts = []
        for name, kind in (("trace", "agent-trace"), ("final", "agent-final"), ("score", "agent-score")):
            try:
                content = self.artifact(run, case, name)
            except PlanError:
                continue
            artifacts.append(
                {
                    "id": name,
                    "kind": kind,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "url": (
                        f"/api/v1/agent-runs/{run['evaluationId']}/cases/{case['caseRunId']}/artifacts/{name}"
                    ),
                }
            )
        case["artifacts"] = artifacts

    @staticmethod
    def cost(cases):
        linked = {
            case["runtimeRecordId"]: case["usage"]
            for case in cases
            if case.get("runtimeRecordId") and case.get("usage")
        }
        result = {
            "source": "runtime-import",
            "observationBasis": "completion-snapshot",
            "runtimeRecordIds": sorted(linked),
        }
        for key in (
            "llmUsd",
            "tokens",
            "inputTokens",
            "outputTokens",
            "cacheReadTokens",
            "cacheWriteTokens",
            "requests",
        ):
            values = [case.get("usage", {}).get(key) if case.get("usage") else None for case in cases]
            result[key] = (
                None
                if not values or any(value is None for value in values)
                else sum(value[key] for value in linked.values())
            )
        sources = {value["costSource"] for value in linked.values()}
        result["costSource"] = (
            "unknown"
            if result["llmUsd"] is None
            else next(iter(sources))
            if len(sources) == 1
            else "runtime-mixed"
        )
        return result

    async def evaluate(self, identifier):
        run = deepcopy(self.get(identifier))
        run.update(status="running", startedAt=now())
        grant = None
        started = time.monotonic()
        remaining = datetime.fromisoformat(run["deadlineAt"]).timestamp() - time.time()
        deadline = time.monotonic() + max(0, remaining)
        try:
            await self.save(run)
            async with asyncio.timeout(max(0, remaining)):
                for case in run["cases"]:
                    suite = self.evaluator.suites.resolve(run["suite"])
                    suite.verify()
                    try:
                        await asyncio.to_thread(
                            verify_identity,
                            run.get("identitySnapshot"),
                            directory=self.evaluator.store.root / "identity-inputs",
                        )
                    except (OSError, ValueError):
                        raise PlanError(
                            "evaluation_conditions_changed", "Fixed implementation inputs changed", status=409
                        ) from None
                    if suite.content_hash != run["pins"]["suiteContentHash"]:
                        raise PlanError("suite_changed", "Pinned Agent Suite changed", status=409)
                    selected = await asyncio.to_thread(
                        self.manager.library.resolve,
                        run["pins"]["scene"],
                        launch=True,
                        source=run["pins"]["source"],
                    )
                    if selected.content_hash != run["pins"]["sceneContentHash"]:
                        raise PlanError("scene_changed", "Pinned Scene changed", status=409)
                    case.update(status="starting", startedAt=now())
                    run["cleanup"] = {"complete": False, "blocked": False}
                    await self.save(run)
                    session = await self.manager.create(
                        None,
                        run["pins"]["source"],
                        SessionOptions(
                            continueWithoutObservers=True, realtimeFactor=30, approval={"high": "require"}
                        ),
                        scene_version=run["pins"]["scene"],
                        evaluation_id=identifier,
                    )
                    session.evaluation_deny_high_risk = True
                    run["key"].update(self.evaluator.bind_identity(run, session))
                    case.update(sessionId=session.session_id, recordingId=session.session_id)
                    if (
                        not session.recording
                        or not session._recording_revision_ready
                        or session.recording.metadata["status"] != "recording"
                    ):
                        raise PlanError("recording_unavailable", "Owned recording is not ready", status=503)
                    directory = self.directory(run) / case["caseRunId"]
                    audit = CaseAudit(session, directory / "trace.jsonl")
                    session.evaluation_audit = audit
                    case.update(status="running", sessionId=session.session_id)
                    grant = CaseGrant(identifier, case, session, deadline, audit)
                    self.signals[case["caseRunId"]] = asyncio.Event()
                    self.finalized[case["caseRunId"]] = asyncio.Event()
                    await self.save(run)
                    self.active[identifier] = self.grants[grant.token] = grant
                    await self.signals[case["caseRunId"]].wait()
                    await self._finalize_case(run, grant, suite)
                    run["episodes"].append(
                        {
                            "caseId": case["caseId"],
                            "repeat": case["repeat"],
                            "score": case["score"],
                            "recordingId": case["recordingId"],
                        }
                    )
                    run["cleanup"] = {"complete": True, "blocked": False}
                    await self.save(run)
                    self.finalized[case["caseRunId"]].set()
                    self.active.pop(identifier, None)
                    grant = None
                if run["purpose"] == "train":
                    try:
                        await asyncio.to_thread(
                            verify_identity,
                            run.get("identitySnapshot"),
                            directory=self.evaluator.store.root / "identity-inputs",
                        )
                    except (OSError, ValueError):
                        raise PlanError(
                            "evaluation_conditions_changed", "Fixed implementation inputs changed", status=409
                        ) from None
                    row = {
                        "evaluationId": identifier,
                        "suite": run["suite"],
                        "subject": run["agentId"],
                        "version": run["harnessVersion"],
                        "key": run["key"],
                        "split": "train",
                        "layer": "agent",
                        "candidateIdentity": run.get("candidateIdentity"),
                        "comparisonIdentity": run.get("comparisonIdentity"),
                        "comparisonState": run.get("comparisonState", "unknown"),
                        "score": sum(case["score"] for case in run["cases"]) / len(run["cases"]),
                        "decision": "reported",
                        "ci95": None,
                        "episodes": len(run["cases"]),
                        "cases": len({case["caseId"] for case in run["cases"]}),
                        "runs": max(case["repeat"] for case in run["cases"]),
                        "recordings": [case["recordingId"] for case in run["cases"]],
                        "traces": [case["artifacts"][0]["url"] for case in run["cases"]],
                        "cost": self.cost(run["cases"]),
                        "durationSeconds": time.monotonic() - started,
                        "at": now(),
                    }
                    await persist(self.evaluator.store.append, row)
                    run["result"] = row
                run.update(status="completed", endedAt=now())
        except BaseException as error:
            committed = next(
                (row for row in self.evaluator.store.rows if row["evaluationId"] == identifier), None
            )
            if committed is not None:
                run.update(status="completed", result=committed, endedAt=now())
                return
            run.update(
                status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                endedAt=now(),
                error={
                    "code": "agent_deadline_expired"
                    if isinstance(error, TimeoutError)
                    else error.detail["code"]
                    if isinstance(error, PlanError)
                    else "agent_evaluation_failed"
                },
            )
            if grant is not None:
                grant.revoked = True
            for case in run["cases"]:
                if case["status"] in ("starting", "running", "finishing"):
                    case.update(
                        status="cancelled" if isinstance(error, asyncio.CancelledError) else "invalid",
                        error=run["error"],
                    )
            owned = [
                session
                for session in self.manager.sessions.values()
                if session.evaluation_id == identifier
                and (session.holds_resources or not session.cleanup_complete)
            ]
            for session in owned:
                try:
                    await session.close()
                except BaseException:
                    self.evaluator._blocked.add(identifier)
            if grant:
                grant.audit.close()
                self.refresh_artifacts(run, grant.case)
            blocked = identifier in self.evaluator._blocked or any(
                session.holds_resources or not session.cleanup_complete for session in owned
            )
            if blocked:
                self.evaluator._blocked.add(identifier)
            run["cleanup"] = {"complete": not blocked, "blocked": blocked}
        finally:
            if grant:
                grant.revoked = True
            self.active.pop(identifier, None)
            await self.save(run)
            if grant:
                self.finalized[grant.case["caseRunId"]].set()

    async def cancel(self, identifier):
        run = self.get(identifier)
        if run["status"] in ("completed", "failed", "cancelled"):
            return self.document(run)
        if self.evaluator._active_evaluation == identifier and self.evaluator._running:
            self.evaluator._running.cancel()
            try:
                await asyncio.shield(self.evaluator._running)
            except asyncio.CancelledError:
                pass
        elif run["status"] == "queued":
            self.evaluator._queue = type(self.evaluator._queue)(
                item for item in self.evaluator._queue if item != ("evaluation", identifier)
            )
            await self.save(run | {"status": "cancelled", "endedAt": now()})
        return self.document(self.get(identifier))

    async def refreshed_cleanup(self, identifier):
        record = self.get(identifier)
        owned = [session for session in self.manager.sessions.values() if session.evaluation_id == identifier]
        complete = all(session.cleanup_complete and not session.holds_resources for session in owned)
        await self.save(record | {"cleanup": {"complete": complete, "blocked": not complete}})


def install_external_evaluations(app, evaluator, agents, runtime_records, role_for):
    service = ExternalEvaluations(app, evaluator, agents, runtime_records)
    app.state.external_evaluations = evaluator.external = service

    def operator(request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Controller operator permission is required", status=403)

    @app.post("/api/v1/agents/{name}/runs", status_code=202)
    async def create_run(name: str, body: AgentEvaluationRequest, request: Request):
        operator(request)
        return await service.submit(name, body)

    @app.get("/api/v1/agent-runs")
    async def runs(agent: str | None = None):
        return {
            "runs": [
                service.document(run)
                for run in evaluator.store.records.values()
                if run.get("layer") == "agent" and (agent is None or run["agentId"] == agent)
            ]
        }

    @app.get("/api/v1/agent-runs/{identifier}")
    async def run(identifier: str):
        return service.document(service.get(identifier))

    @app.post("/api/v1/agent-runs/{identifier}/next")
    async def next_work(identifier: str, request: Request):
        operator(request)
        return await service.next(identifier)

    @app.post("/api/v1/agent-runs/{identifier}/complete")
    async def complete(identifier: str, body: CompleteCase, request: Request):
        operator(request)
        return await service.complete(identifier, body)

    @app.post("/api/v1/agent-runs/{identifier}/cancel")
    async def cancel(identifier: str, request: Request):
        operator(request)
        return await service.cancel(identifier)

    @app.get("/api/v1/agent-runs/{identifier}/cases/{case_id}/artifacts/{name}")
    async def artifact(identifier: str, case_id: str, name: str):
        run = service.get(identifier)
        case = next((case for case in run["cases"] if case["caseRunId"] == case_id), None)
        if case is None or not any(item["id"] == name for item in case["artifacts"]):
            raise PlanError("unknown_agent_artifact", "Unknown public Agent evidence", status=404)
        return Response(
            service.artifact(run, case, name),
            media_type="application/x-ndjson" if name == "trace" else "application/json",
            headers={"Cache-Control": "no-store"},
        )

    return service

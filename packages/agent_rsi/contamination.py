"""Durable exposure evidence stops owned work before restoring exact Reference files."""

import asyncio
import hashlib
import json
import uuid
from copy import deepcopy

from packages.agent_rsi.budgets import native_total
from packages.info.plans import PlanError
from packages.library.agent_transitions import workspace_hash
from packages.library.common import LibraryError
from packages.library.transactions import durable_json
from packages.runtime_records.native import digest
from packages.runtime_records.store import parse_capture


def private_input(service, body):
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from strings(key)
                yield from strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from strings(item)

    texts = tuple(strings(body))
    for suite in service.manager.evaluations.suites.versions.values():
        if suite.metadata.get("layer") != "agent":
            continue
        public = {case["statement"].strip() for case in suite.public()["cases"]}
        for case in suite.metadata["cases"]:
            statement = suite.statements[case["id"]].strip()
            if case["split"] == "test" and len(statement) >= 32 and statement not in public:
                if any(statement in text for text in texts):
                    return True
    return False


def intent(service, record, native_session, evidence, request_id):
    """Caller holds the RSI lock; no private evidence enters public result or notifications."""
    previous = record.context.get("contamination")
    if previous is not None:
        if previous["requestId"] != request_id or previous["evidence"] != evidence:
            raise PlanError(
                "idempotency_conflict", "This round retains its original exposure evidence", status=409
            )
        return record
    if record.plan.status == "promoted":
        raise PlanError("agent_round_stopped", "This round has already been promoted", status=409)
    context = {
        "agentId": record.plan.agentId,
        "goalId": record.plan.goalId,
        "purpose": "contamination-evidence",
    }
    sessions = deepcopy(service.optimizer_sessions)
    sessions[native_session] = sessions[native_session] | {
        "contaminated": True,
        "contaminationContext": context,
    }
    record = record.model_copy(deep=True)
    record.plan.status = "contaminated"
    record.context["contamination"] = {
        "requestId": request_id,
        "evidence": evidence,
        "nativeSessionId": native_session,
        "application": "stopping",
        "cleanup": {"complete": False, "blocked": False},
    }
    # Record the terminal stop and classification before cancellation or filesystem effects.
    service._write(
        history=service.history | {f"{record.plan.id}:{record.plan.revision}": record},
        optimizer_sessions=sessions,
    )
    service.records = service.records | {record.plan.id: record}
    for grant in service.grants.values():
        if grant.agent_id == record.plan.agentId and grant.goal_id == record.plan.goalId:
            grant.revoked = True
    for run in owned_runs(service, record):
        for grant in service.external.grants.values():
            if grant.run_id == run["evaluationId"]:
                grant.revoked = True
    service._notify(record)
    return record


def owned_runs(service, record):
    return [
        run
        for run in service.manager.evaluations.store.records.values()
        if run.get("layer") == "agent"
        and run.get("planId") == record.plan.id
        and run.get("planRevision") == record.plan.revision
    ]


def controller_intent(service, identifier, body, actor):
    with service._lock:
        record = service.get(identifier)
        if record.plan.revision != body.revision:
            raise PlanError("agent_round_stopped", "Select the exact exposed round", status=409)
        capture = service.external.runtime_records.get(body.runtimeRecordId)
        owner = service.optimizer_sessions.get(capture["nativeSessionId"])
        event = next((event for event in capture["events"] if event["id"] == body.eventId), None)
        if (
            owner is None
            or owner["agentId"] != record.plan.agentId
            or owner["goalId"] != record.plan.goalId
            or event is None
            or event["contentHash"] != body.eventHash
            or event.get("role") not in ("user", "assistant")
        ):
            raise PlanError(
                "agent_exposure_evidence_required", "Select fixed Optimizer input evidence", status=409
            )
        if actor.kind == "agent" and not private_input(service, event["content"]):
            raise PlanError(
                "agent_exposure_evidence_required", "Denied reads alone are not exposure evidence", status=409
            )
        evidence = {
            "kind": "native-event",
            "runtimeRecordId": capture["id"],
            "runtimeRecordHash": capture["contentHash"],
            "eventId": event["id"],
            "eventHash": event["contentHash"],
        }
        previous = record.context.get("contamination", {})
        retained = previous.get("evidence", {})
        if previous.get("requestId") == body.requestId and all(
            retained.get(key) == evidence[key] for key in ("kind", "runtimeRecordId", "eventId", "eventHash")
        ):
            evidence["runtimeRecordHash"] = retained["runtimeRecordHash"]
        return intent(service, record, capture["nativeSessionId"], evidence, body.requestId)


def ingest(service, identifier, body):
    record = service.get(identifier)
    stop = record.context.get("contamination")
    parsed = parse_capture(body)
    if stop is None or parsed["nativeSessionId"] != stop["nativeSessionId"]:
        raise PlanError(
            "runtime_capture_scope_conflict", "Select this stopped Optimizer's capture", status=409
        )
    result = service.external.runtime_records.ingest(
        body, private_context=service.private_native_context(stop["nativeSessionId"])
    )
    return {"runtimeRecordId": result["session"]["id"], "idempotent": result["idempotent"]}


async def finish(service, identifier):
    record = service.get(identifier)
    stop = record.context["contamination"]
    if stop["application"] == "complete":
        return service.document(record)
    context = service.private_native_context(stop["nativeSessionId"])
    await asyncio.to_thread(service.external.runtime_records.mark_private, stop["nativeSessionId"], context)
    for run in owned_runs(service, record):
        await service.external.cancel(run["evaluationId"])
        if not service.external.get(run["evaluationId"])["cleanup"]["complete"]:
            try:
                await service.manager.evaluations.cleanup(run["evaluationId"], private=run["split"] == "test")
            except Exception:
                pass  # The persisted cleanup block is the outcome; retry keeps the same ownership.
    with service._lock:
        record = service.get(identifier).model_copy(deep=True)
        stop = record.context["contamination"]
        complete = all(run["cleanup"]["complete"] for run in owned_runs(service, record))
        stop["cleanup"] = {"complete": complete, "blocked": not complete}
        if not complete:
            stop["application"] = "cleanup_blocked"
            service._save(record)
            return service.document(record)
        try:
            agent = service.agents.show(record.plan.agentId)
            if agent["referenceVersion"] != record.plan.referenceVersion:
                raise PlanError("agent_reference_mismatch", "The frozen Reference changed", status=409)
            expected_version = agent["currentVersion"]
            if expected_version not in (record.plan.referenceVersion, record.plan.candidateVersion):
                raise PlanError("harness_transition_conflict", "Preserve the current editor", status=409)
            expected_hash = (
                record.context["candidateHash"]
                if expected_version == record.plan.candidateVersion
                else (record.context["reference"]["harnessHash"])
            )
            transition = stop.get("transition")
            if transition is None:
                transition = {"expected_version": expected_version, "expected_hash": expected_hash}
                stop["transition"] = transition
                service._save(record)
                record = record.model_copy(deep=True)
                stop = record.context["contamination"]
            receipt = service.agents.transition(
                record.plan.agentId,
                target_version=record.plan.referenceVersion,
                **transition,
                request_id="contaminated-" + digest({"planId": identifier, "revision": record.plan.revision}),
                outcome_version=record.plan.candidateVersion,
                outcome="rejected",
            )
            if (
                service.agents.show(record.plan.agentId)["currentVersion"] != record.plan.referenceVersion
                or workspace_hash(service.agents._path(record.plan.agentId) / "harness")
                != record.context["reference"]["harnessHash"]
            ):
                raise PlanError("harness_transition_conflict", "Reference editor bytes changed", status=409)
        except (LibraryError, PlanError) as error:
            stop.update(application="restore_blocked", errorCode=error.detail["code"])
            service._save(record)
            return service.document(record)
        stop.update(application="complete", files=receipt)
        if record.result is None and record.runId:
            run = service.external.get(record.runId)
            record.result = {
                "decision": "contaminated",
                "reason": "private_exposure",
                "score": None,
                "ci95": None,
                "evaluationId": record.runId,
                "application": "complete",
                "referenceAdvanced": False,
                "cost": native_total(service, plan=identifier),
                "executionCost": service.external.cost(run["cases"]),
                "at": run.get("endedAt"),
                "files": receipt,
            }
        service._save(record)
        service._notify(record)
        if record.runId and record.result["decision"] == "contaminated":
            service.publish_notebook(record)
        return service.document(record)


async def recover(service):
    for record in tuple(service.records.values()):
        if record.context.get("contamination", {}).get("application") not in (None, "complete"):
            await finish(service, record.plan.id)


async def observe_input(service, grant, body, request_id):
    if grant.kind != "optimizer" or not private_input(service, body):
        return
    evidence_id = uuid.uuid4().hex
    raw = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
    path = service.manager.settings.state_root / "agent-rsi-exposures" / (evidence_id + ".json")
    await asyncio.to_thread(
        durable_json,
        path,
        {
            "principalId": grant.identifier,
            "nativeSessionId": grant.native_session_id,
            "requestId": request_id,
            "input": body,
            "inputHash": hashlib.sha256(raw).hexdigest(),
        },
    )
    evidence = {
        "kind": "workflow-input",
        "evidenceId": evidence_id,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "requestId": request_id,
    }
    with service._lock:
        records = [
            record
            for record in service.records.values()
            if record.plan.agentId == grant.agent_id
            and record.plan.goalId == grant.goal_id
            and record.plan.status not in ("promoted", "rejected", "inconclusive", "contaminated")
        ]
        for record in records:
            intent(service, record, grant.native_session_id, evidence, "input-" + evidence_id)
        if not records:
            owner = deepcopy(service.optimizer_sessions[grant.native_session_id])
            owner.update(
                contaminated=True,
                contaminationContext={
                    "agentId": grant.agent_id,
                    "goalId": grant.goal_id,
                    "purpose": "contamination-evidence",
                },
            )
            service._write(optimizer_sessions=service.optimizer_sessions | {grant.native_session_id: owner})
            grant.revoked = True
    for record in records:
        await finish(service, record.plan.id)
    raise PlanError("agent_workflow_contaminated", "Private input stopped this workflow", status=409)

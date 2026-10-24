"""Promotion owns private work on B's queue; only a human may advance Reference."""

import asyncio
import hashlib
import time
import uuid
from copy import deepcopy

from packages.agent_rsi.budgets import require_budget
from packages.agent_rsi.grants import connection
from packages.agent_rsi.scope import WorkflowGrant
from packages.info.approvals import ApprovalConflict
from packages.info.plans import PlanError
from packages.library.agent_transitions import workspace_hash
from packages.runtime_records.native import digest, normalize
from packages.runtime_records.store import Associations


def get(service, identifier):
    value = service.promotions.get(identifier)
    if value is None:
        raise PlanError("unknown_agent_promotion", "Unknown Agent promotion", status=404)
    return value


def public(value):
    return {
        key: deepcopy(value.get(key))
        for key in ("id", "planId", "planRevision", "agentId", "goalId", "status", "approvalId")
    }


def save(service, value):
    service._write(promotions=service.promotions | {value["id"]: value})


def prepare(service, identifier, body, actor):
    with service._lock:
        record = service.get(identifier)
        if record.plan.revision != body.revision:
            raise PlanError("agent_round_stopped", "Select the completed current round", status=409)
        previous = next(
            (value for value in service.promotions.values() if value["planId"] == identifier), None
        )
        inputs = body.model_dump(mode="json")
        if previous is not None:
            if previous["inputs"] != inputs:
                raise PlanError(
                    "idempotency_conflict", "This round has a fixed promotion request", status=409
                )
            return previous
        if (
            record.plan.status != "accepted"
            or not record.result
            or record.result["application"] != "complete"
        ):
            raise PlanError("agent_promotion_not_ready", "Complete an accepted train round first", status=409)
        agent = service.agents.show(record.plan.agentId)
        if agent["referenceVersion"] != record.plan.referenceVersion:
            raise PlanError("agent_reference_mismatch", "The Reference has changed", status=409)
        require_candidate(service, record)
        service.complete_train(record.runId, record.plan.agentId)
        if (
            service.identities(service.external.get(record.runId))
            != record.context["reference"]["comparison"]
        ):
            raise PlanError("not_comparable", "Train evidence no longer matches the Reference", status=409)
        suite = service.manager.evaluations.suites.resolve(record.plan.suite)
        count = sum(case["split"] == "test" for case in suite.metadata["cases"])
        if not count:
            raise PlanError("agent_test_unavailable", "This Suite has no promotion scope", status=409)
        require_budget(service, record, additional_cases=2 * count * suite.metadata["runsPerCase"])
        value = {
            "id": "promotion-" + uuid.uuid4().hex,
            "planId": identifier,
            "planRevision": record.plan.revision,
            "agentId": record.plan.agentId,
            "goalId": record.plan.goalId,
            "status": "preparing",
            "inputs": inputs,
            "requestedBy": actor.model_dump(mode="json"),
            "runs": {},
            "nativeBindings": {},
        }
        save(service, value)
        return value


def require_candidate(service, record):
    agent = service.agents.show(record.plan.agentId)
    if (
        agent["currentVersion"] != record.plan.candidateVersion
        or workspace_hash(service.agents._path(record.plan.agentId) / "harness")
        != record.context["candidateHash"]
    ):
        raise PlanError("harness_transition_conflict", "Candidate editor bytes changed", status=409)


async def request(service, identifier, body, actor):
    from apps.api.app.agent_evaluations import AgentEvaluationRequest

    value = await asyncio.to_thread(prepare, service, identifier, body, actor)
    if value["status"] != "preparing":
        return public(value)
    record = service.get(identifier)
    for side, version in (
        ("reference", record.plan.referenceVersion),
        ("candidate", record.plan.candidateVersion),
    ):
        context = {
            "planId": identifier,
            "planRevision": record.plan.revision,
            "goalId": record.plan.goalId,
            "promotionId": value["id"],
            "promotionSide": side,
        }
        payload = AgentEvaluationRequest(
            requestId="promotion-" + digest({"promotion": value["id"], "side": side}),
            suite=record.plan.suite,
            harnessVersion=version,
            runtime=record.plan.runtime.model_dump(mode="json"),
            wallSeconds=record.plan.budget.wallSeconds,
        )
        run = await service.external.submit(record.plan.agentId, payload, agent_promotion=context)
        with service._lock:
            value = deepcopy(get(service, value["id"]))
            value["runs"][side] = run["id"]
            save(service, value)
    with service._lock:
        value = deepcopy(get(service, value["id"]))
        value["status"] = "evaluating"
        save(service, value)
    return public(value)


def require_admission(service, run):
    with service._lock:
        context = run["agentPromotion"]
        value = get(service, context["promotionId"])
        record = service.get(value["planId"])
        side = context["promotionSide"]
        expected_version = (
            record.plan.referenceVersion if side == "reference" else record.plan.candidateVersion
        )
        expected_hash = (
            record.context["reference"]["harnessHash"]
            if side == "reference"
            else record.context["candidateHash"]
        )
        approval = service.manager.approvals.records.get(record.approvalId)
        if (
            value["status"] not in ("preparing", "evaluating")
            or record.plan.status != "accepted"
            or approval is None
            or approval.status != "approved"
            or side not in ("reference", "candidate")
            or run["split"] != "test"
            or run["planId"] != record.plan.id
            or run["planRevision"] != record.plan.revision
            or run["goalId"] != record.plan.goalId
            or run["agentId"] != record.plan.agentId
            or run["suite"] != record.plan.suite
            or run["harnessVersion"] != expected_version
            or run["pins"]["harnessHash"] != expected_hash
            or run["runtime"] != record.plan.runtime.model_dump(mode="json")
        ):
            raise PlanError(
                "agent_promotion_stopped", "Private work no longer matches approved intent", status=409
            )
        require_candidate(service, record)
        require_budget(service, record, run)


def issue_controller(service, identifier, seconds):
    with service._lock:
        if service.audit_error:
            raise PlanError("agent_trace_unavailable", "Workflow Trace is unavailable", status=503)
        value = get(service, identifier)
        if value["status"] not in ("preparing", "evaluating"):
            raise PlanError("agent_promotion_stopped", "This private handoff is stopped", status=409)
        grant = WorkflowGrant(
            service,
            "promotion-controller",
            value["agentId"],
            value["goalId"],
            time.monotonic() + seconds,
            promotion_id=identifier,
        )
        service.grants[grant.token] = grant
        return connection(grant)


async def next_work(service, identifier):
    value = get(service, identifier)
    if value["status"] not in ("preparing", "evaluating"):
        return {"status": "finished"}
    for side in ("reference", "candidate"):
        run_id = value["runs"].get(side)
        if run_id and service.external.get(run_id)["status"] not in ("completed", "failed", "cancelled"):
            work = await service.external.next(run_id)
            # The controller needs only this fresh Target's work, never the aggregate test scores.
            return {key: item for key, item in work.items() if key != "runId"}
    await asyncio.to_thread(refresh, service, identifier)
    return {"status": "finished"}


def owning_run(service, value, case_id):
    for run_id in value["runs"].values():
        run = service.external.get(run_id)
        case = next((case for case in run["cases"] if case["caseRunId"] == case_id), None)
        if case is not None:
            return run, case
    raise PlanError("unknown_case_run", "Unknown private work item", status=404)


async def bind_native(service, identifier, body):
    value = get(service, identifier)
    run, _ = owning_run(service, value, body.caseRunId)
    grant = service.external.active.get(run["evaluationId"])
    if value["status"] != "evaluating" or grant is None or grant.case["caseRunId"] != body.caseRunId:
        raise PlanError("case_not_active", "This private work item is not active", status=409)
    async with grant.lock:
        grant.require()
        with service._lock:
            value = deepcopy(get(service, identifier))
            known = service.private_native_context(body.nativeSessionId)
            if known is not None:
                if known["caseRunId"] != body.caseRunId or known["promotionId"] != identifier:
                    raise PlanError("native_session_reused", "Use a fresh private Target", status=409)
            elif (
                body.nativeSessionId in service.optimizer_sessions
                or grant.case.get("boundNativeSessionId") is not None
                or any(
                    body.nativeSessionId in (case.get("nativeSessionId"), case.get("boundNativeSessionId"))
                    for candidate in service.manager.evaluations.store.records.values()
                    if candidate.get("layer") == "agent"
                    for case in candidate["cases"]
                )
            ):
                raise PlanError("native_session_reused", "Use a fresh private Target", status=409)
            context = {
                "promotionId": identifier,
                "caseRunId": body.caseRunId,
                "evaluationId": run["evaluationId"],
                "agentId": run["agentId"],
                "harnessVersion": run["harnessVersion"],
                "planId": run["planId"],
                "planRevision": run["planRevision"],
                "goalId": run["goalId"],
            }
            value["nativeBindings"][body.nativeSessionId] = context
            save(service, value)
        await asyncio.to_thread(service.external.runtime_records.mark_private, body.nativeSessionId, context)
        grant.case["boundNativeSessionId"] = body.nativeSessionId
        await service.external.save(run | {"cases": service.external._live_cases(run["evaluationId"])})
    return {"status": "bound", "caseRunId": body.caseRunId}


def ingest(service, identifier, body):
    value = get(service, identifier)
    # Normalize identity before private intake; a caller cannot choose the classification.
    native = body.records
    if native is None:
        import json

        try:
            native = [json.loads(line) for line in body.content.splitlines() if line.strip()]
        except (ValueError, RecursionError) as error:
            raise PlanError("invalid_runtime_jsonl", "Invalid native capture", status=422) from error
    parsed = normalize(body.format, native)
    context = value["nativeBindings"].get(parsed["nativeSessionId"])
    if context is None:
        raise PlanError("private_native_binding_required", "Bind the fresh Target before capture", status=409)
    associations = Associations.model_validate(
        {key: context[key] for key in ("agentId", "harnessVersion", "planId", "planRevision", "goalId")}
    )
    result = service.external.runtime_records.ingest(
        body.model_copy(update={"associations": associations}), private_context=context
    )
    return {"runtimeRecordId": result["session"]["id"], "idempotent": result["idempotent"]}


async def complete(service, identifier, body):
    value = get(service, identifier)
    if value["status"] != "evaluating":
        raise PlanError("agent_promotion_stopped", "This private handoff is stopped", status=409)
    run, _ = owning_run(service, value, body.caseRunId)
    result = await service.external.complete(run["evaluationId"], body)
    await asyncio.to_thread(refresh, service, identifier)
    return result


def complete_evidence(service, run):
    suite = service.manager.evaluations.suites.resolve(run["suite"])
    expected = {
        (case["id"], repeat)
        for case in suite.metadata["cases"]
        if case["split"] == "test"
        for repeat in range(1, suite.metadata["runsPerCase"] + 1)
    }
    observed = [(case["caseId"], case["repeat"]) for case in run["cases"]]
    if (
        run["status"] != "completed"
        or not run["cleanup"]["complete"]
        or len(expected) != len(observed)
        or set(observed) != expected
        or run.get("result") is None
        or run["result"] not in service.manager.evaluations.store.rows
        or any(case["status"] != "completed" for case in run["cases"])
    ):
        raise PlanError("agent_test_incomplete", "Complete valid private evidence is required", status=409)
    for case in run["cases"]:
        if {item["id"] for item in case["artifacts"]} != {"trace", "final", "score"}:
            raise PlanError("agent_test_incomplete", "Private evidence is incomplete", status=409)
        for item in case["artifacts"]:
            if hashlib.sha256(service.external.artifact(run, case, item["id"])).hexdigest() != item["sha256"]:
                raise PlanError("agent_evidence_changed", "Private evidence changed", status=409)
    return service.identities(run)


def refresh(service, identifier):
    with service._lock:
        value = deepcopy(get(service, identifier))
        if value["status"] not in ("evaluating", "cleanup_blocked") or set(value["runs"]) != {
            "reference",
            "candidate",
        }:
            return value
        runs = [service.external.get(value["runs"][side]) for side in ("reference", "candidate")]
        if not all(run["status"] in ("completed", "failed", "cancelled") for run in runs):
            return value
        if not all(run["cleanup"]["complete"] for run in runs):
            value["status"] = "cleanup_blocked"
        else:
            try:
                if complete_evidence(service, runs[0]) != complete_evidence(service, runs[1]):
                    raise PlanError("not_comparable", "Private fixed conditions differ", status=409)
            except PlanError as error:
                value.update(status="inconclusive", errorCode=error.detail["code"])
            else:
                approval = service.manager.approvals.submit(
                    subject="plan",
                    subject_id=value["planId"],
                    requester=service.get(value["planId"]).requestedBy,
                    title="Review Agent Reference promotion",
                    details={
                        "agentPromotionId": identifier,
                        "revision": value["planRevision"],
                        "agentId": value["agentId"],
                        "reviewUrl": "/api/v1/agent-promotions/" + identifier + "/review",
                    },
                )
                value.update(status="waiting-human", approvalId=approval.approvalId)
        save(service, value)
        return value


def review(service, identifier):
    value = refresh(service, identifier)
    result = public(value)
    if value["status"] in ("waiting-human", "applying", "promoted", "denied"):
        runs = {side: service.external.get(identity) for side, identity in value["runs"].items()}
        result["test"] = {side: service.external.document(run, private=True) for side, run in runs.items()}
        for side, run in result["test"].items():
            for case in run["cases"]:
                for artifact in case["artifacts"]:
                    artifact["url"] = (
                        "/api/v1/agent-promotions/"
                        + identifier
                        + "/review/runs/"
                        + side
                        + "/cases/"
                        + case["caseRunId"]
                        + "/artifacts/"
                        + artifact["id"]
                    )
        result["candidateBelowReference"] = (
            runs["candidate"]["result"]["score"] < runs["reference"]["result"]["score"]
        )
    return result


def decide(service, approval, decision, actor, note):
    with service._lock:
        value = deepcopy(get(service, approval.details["agentPromotionId"]))
        current = service.manager.approvals.records[approval.approvalId]
        expected = {"approve": "approved", "deny": "denied"}.get(decision)
        if (
            expected is None
            or value.get("approvalId") != approval.approvalId
            or value["status"] not in ("waiting-human", "applying", "promoted", "denied")
            or current.status not in ("pending", expected)
        ):
            raise ApprovalConflict("This promotion is unavailable for that decision")
        if value["status"] in ("promoted", "denied"):
            return public(value)
        record = service.get(value["planId"])
        if record.plan.status not in ("accepted", "promoted"):
            raise ApprovalConflict("The round is stopped")
        require_candidate(service, record)
        reference, candidate = (
            service.external.get(value["runs"][side]) for side in ("reference", "candidate")
        )
        if complete_evidence(service, reference) != complete_evidence(service, candidate):
            raise PlanError("not_comparable", "Private fixed conditions differ", status=409)
        value["status"] = "applying" if decision == "approve" else "denied"
        save(service, value)
        if current.status == "pending":
            service.manager.approvals.decide(approval.approvalId, decision, actor, note)
        if decision == "deny":
            return public(value)
        receipt = service.agents.transition(
            record.plan.agentId,
            target_version=record.plan.candidateVersion,
            expected_version=record.plan.candidateVersion,
            expected_hash=record.context["candidateHash"],
            request_id="promote-" + digest({"promotionId": value["id"]}),
            outcome_version=record.plan.candidateVersion,
            outcome="accepted",
            advance_reference=True,
        )
        require_candidate(service, record)
        if service.agents.show(record.plan.agentId)["referenceVersion"] != record.plan.candidateVersion:
            raise PlanError("agent_reference_mismatch", "Reference advancement is incomplete", status=409)
        train = service.complete_train(record.runId, record.plan.agentId)
        comparison = service.identities(train)
        baseline = deepcopy(record.context["reference"]) | {
            "requestId": "promotion-" + value["id"],
            "evaluationId": record.runId,
            "harnessVersion": record.plan.candidateVersion,
            "harnessHash": record.context["candidateHash"],
            "archiveHash": record.context["candidateArchiveHash"],
            "comparison": comparison,
            "score": train["result"]["score"],
            "at": train["result"]["at"],
        }
        baselines = dict(service.baselines)
        key = digest({"agent": record.plan.agentId, "comparison": comparison})
        if key in baselines:
            old = baselines[key]
            baselines["history-" + digest(old)] = old
        baselines[key] = baseline
        record = record.model_copy(deep=True)
        record.plan.status = "promoted"
        record.result["referenceAdvanced"] = True
        value.update(status="promoted", files=receipt)
        service._write(
            history=service.history | {f"{record.plan.id}:{record.plan.revision}": record},
            baselines=baselines,
            promotions=service.promotions | {value["id"]: value},
        )
        service.records = service.records | {record.plan.id: record}
        service._notify(record)
        return public(value)

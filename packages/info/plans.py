"""Operation plans track real calls; they never schedule device work."""

import time
import uuid
from copy import deepcopy
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from packages.info.approvals import ApprovalConflict
from packages.info.conditions import evaluate_conditions, validate_conditions
from packages.info.invocations import TERMINAL
from packages.info.schema_subset import json_equal
from packages.protocol.schema import Actor, EntityEvent, OperationPlan, PlanFullUpdate, PlanStepUpdate


class PlanError(Exception):
    def __init__(self, code, message, *, status=409, **details):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, **details}


class StoredPlan(BaseModel):
    plan: OperationPlan
    sessionId: str
    sceneRevision: str
    epoch: int
    requestedBy: Actor
    createdAtMs: float
    humanRequired: bool = False
    approvalId: str | None = None
    results: dict[int, dict] = Field(default_factory=dict)
    changes: list[dict] = Field(default_factory=list)


def step_content(step):
    return step.model_dump(mode="json", exclude={"status"}, exclude_none=True)


def validate_plan(document, session, *, plan_id=None, revision=1):
    """Validate submitted content against the actual target Catalog, never current preconditions."""
    value = deepcopy(document)
    value.setdefault("id", plan_id or "plan-" + uuid.uuid4().hex[:16])
    value.setdefault("kind", "operation")
    value.setdefault("target", "session")
    value["revision"] = revision
    value.setdefault("status", "draft")
    try:
        plan = OperationPlan.model_validate(value)
    except ValidationError as exc:
        errors = [
            {"location": list(item["loc"]), "message": item["msg"], "type": item["type"]}
            for item in exc.errors(include_url=False, include_context=False)
        ]
        raise PlanError("invalid_plan", "Invalid operation plan", status=422, errors=errors) from exc
    if plan_id and plan.id != plan_id:
        raise PlanError("invalid_plan", "Plan ID does not match the revision target", status=422)
    if plan.target != "session":
        raise PlanError("target_unavailable", "A live device host is not connected", status=503)
    if any(hasattr(step, "do") and step.do.entity.startswith("/Real/") for step in plan.steps):
        raise PlanError("target_unavailable", "A live device host is not connected", status=503)
    errors, seen = [], set()
    if plan.status != "draft" or not plan.steps:
        errors.append({"stepId": None, "message": "Submit nonempty draft content; lifecycle is server-owned"})
    for step in plan.steps:
        if step.id in seen:
            errors.append({"stepId": step.id, "message": "Duplicate step ID"})
        seen.add(step.id)
        try:
            if step.status != "pending":
                raise ValueError("Step status is server-owned")
            if hasattr(step, "do"):
                action = step.do
                session.catalog.action(action.entity, action.action)
                action.params = session.catalog.validate_params(action.entity, action.action, action.params)
                conditions = [step.expect] if step.expect is not None else []
                default_entity = action.entity
            else:
                condition = step.wait if hasattr(step, "wait") else step.check
                conditions, default_entity = [condition], condition.get("entity")
                if not default_entity:
                    raise ValueError("Check and wait conditions require an entity")
            validate_conditions(
                conditions,
                default_entity,
                lambda entity, prop: session.catalog.property(entity, prop)["schema"],
            )
        except (KeyError, ValueError, ValidationError) as exc:
            errors.append({"stepId": step.id, "message": str(exc)})
    if errors:
        raise PlanError("invalid_plan", "Plan validation failed", status=422, errors=errors)
    return plan


class PlanStore:
    def __init__(self, path: Path, approvals):
        self.path, self.approvals = path, approvals
        self.records: dict[str, StoredPlan] = {}
        self.history: dict[str, dict[int, StoredPlan]] = {}
        self.bindings: dict[str, tuple[str, int, int]] = {}
        self.requests = {}
        self._started = False

    def startup(self):
        if self._started:
            return
        if self.path.exists():
            complete = 0
            for line in self.path.read_bytes().splitlines(keepends=True):
                if not line.endswith(b"\n"):
                    with self.path.open("r+b") as stream:
                        stream.truncate(complete)
                    break
                self._retain(StoredPlan.model_validate_json(line))
                complete += len(line)
        # Gateway sessions are transient. A recovered plan is history, never a restart grant.
        for record in list(self.records.values()):
            if record.plan.status not in ("completed", "abandoned"):
                updated = record.model_copy(deep=True)
                updated.plan.status = "abandoned"
                for step in updated.plan.steps:
                    if step.status == "running":
                        step.status = "failed"
                if updated.approvalId:
                    approval = self.approvals.records.get(updated.approvalId)
                    if approval and approval.status == "pending":
                        self.approvals.set_status(approval.approvalId, "unavailable")
                self._save(updated)
        for approval in list(self.approvals.records.values()):
            if (
                approval.subject == "plan"
                and approval.status == "pending"
                and approval.details.get("plan", {}).get("kind") == "operation"
            ):
                self.approvals.set_status(approval.approvalId, "unavailable")
        self._started = True

    def _retain(self, record):
        self.records[record.plan.id] = record
        self.history.setdefault(record.plan.id, {})[record.plan.revision] = record

    def _save(self, record):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(record.model_dump_json() + "\n")
        self._retain(record)

    def get(self, plan_id, revision=None):
        self.startup()
        record = (
            self.history.get(plan_id, {}).get(revision) if revision is not None else self.records.get(plan_id)
        )
        if record is None:
            raise PlanError("unknown_plan", "Unknown plan or revision", status=404)
        return record

    def document(self, record):
        approval = self.approvals.records.get(record.approvalId)
        return {
            **record.plan.model_dump(mode="json"),
            "sessionId": record.sessionId,
            "sceneRevision": record.sceneRevision,
            "epoch": record.epoch,
            "requestedBy": record.requestedBy.model_dump(),
            "createdAtMs": record.createdAtMs,
            "approvalId": record.approvalId,
            "approvalStatus": approval.status if approval else None,
            "approvalMode": "human" if record.humanRequired else "auto",
            "stepResults": deepcopy(record.results),
            "changes": deepcopy(record.changes),
        }

    @staticmethod
    def _envelope(session):
        return {
            "sceneRevision": session.manifest.sceneRevision,
            "epoch": session.epoch,
            "simTime": session.sim_time,
            "sampleTimeMs": time.monotonic() * 1000,
        }

    def _full(self, record, session):
        session.broadcast_info(PlanFullUpdate(**self._envelope(session), plan=record.plan))

    def require_target(self, record, session):
        if (record.sessionId, record.sceneRevision, record.epoch) != (
            session.session_id,
            session.manifest.sceneRevision,
            session.epoch,
        ):
            raise PlanError(
                "invalid_plan_reference", "Plan belongs to another session, scene revision or epoch"
            )

    def create(self, document, session, actor, *, plan_id=None):
        self.startup()
        previous = self.get(plan_id) if plan_id else None
        if previous:
            self.require_target(previous, session)
            if any(step.status == "running" for step in previous.plan.steps):
                raise PlanError("plan_busy", "Wait for active steps before revising")
        plan = validate_plan(
            document, session, plan_id=plan_id, revision=previous.plan.revision + 1 if previous else 1
        )
        if not previous and plan.id in self.records:
            raise PlanError("plan_exists", "Plan ID already exists")
        results, changes = {}, []
        if previous:
            incoming = {step.id: step for step in plan.steps}
            for old in previous.plan.steps:
                new = incoming.get(old.id)
                unchanged = new is not None and step_content(old) == step_content(new)
                if old.status == "done" and not unchanged:
                    raise PlanError(
                        "invalid_plan",
                        "Completed steps cannot be changed or removed",
                        status=422,
                        errors=[{"stepId": old.id, "message": "Preserve completed step content"}],
                    )
                if unchanged and old.status == "done":
                    new.status = "done"
                    results[old.id] = deepcopy(previous.results.get(old.id, {}))
                if not unchanged:
                    changes.append({"stepId": old.id, "change": "removed" if new is None else "changed"})
            changes.extend(
                {"stepId": step.id, "change": "added"}
                for step in plan.steps
                if step.id not in {old.id for old in previous.plan.steps}
            )
            if previous.approvalId:
                approval = self.approvals.records[previous.approvalId]
                if approval.status == "pending":
                    self.approvals.set_status(approval.approvalId, "unavailable")
        human = any(
            hasattr(step, "do")
            and (
                step.do.entity.startswith("/Real/")
                or session.catalog.action(step.do.entity, step.do.action).get("risk") == "high"
            )
            for step in plan.steps
        )
        plan.status = "submitted" if human else "approved"
        record = StoredPlan(
            plan=plan,
            sessionId=session.session_id,
            sceneRevision=session.manifest.sceneRevision,
            epoch=session.epoch,
            requestedBy=actor,
            createdAtMs=time.time() * 1000,
            humanRequired=human,
            results=results,
            changes=changes,
        )
        if human:
            approval = self.approvals.submit(
                subject="plan",
                subject_id=plan.id,
                session_id=session.session_id,
                requester=actor,
                title=plan.title,
                details={
                    "revision": plan.revision,
                    "sceneRevision": record.sceneRevision,
                    "epoch": record.epoch,
                    "plan": plan.model_dump(mode="json"),
                },
            )
            record.approvalId = approval.approvalId
        self._save(record)
        self._full(record, session)
        return record

    def claim_request(self, session, request_id, context):
        key = session.session_id, request_id
        identity = (context or {}).copy()
        if key in self.requests and self.requests[key] != identity:
            raise PlanError("idempotency_conflict", "This request ID already belongs to another plan context")
        self.requests[key] = identity

    def action(self, session, invocation, context=None):
        """Return a server-owned one-use approval grant, never a client approval bit."""
        record, step = None, None
        if context:
            record = self.get(context["planId"])
            self.require_target(record, session)
            if context.get("planRevision", record.plan.revision) != record.plan.revision:
                raise PlanError("invalid_plan_reference", "Plan revision is no longer current")
            step = next((item for item in record.plan.steps if item.id == context["stepId"]), None)
            if step is None:
                raise PlanError("invalid_plan_reference", "Unknown plan step")
        exact = bool(
            step
            and hasattr(step, "do")
            and step.do.entity == invocation.entityKey
            and step.do.action == invocation.action
            and json_equal(
                step.do.params,
                session.catalog.validate_params(invocation.entityKey, invocation.action, invocation.params),
            )
        )
        consumed = bool(record and record.results.get(step.id, {}).get("consumed"))
        high = session.catalog.action(invocation.entityKey, invocation.action).get("risk") == "high"
        if exact and (step.status in ("done", "running") or (high and consumed)):
            exact = False
        if exact and not self.approved(record):
            raise PlanError("plan_not_approved", "This plan revision requires approval")
        active = [
            item
            for item in self.records.values()
            if item.sessionId == session.session_id
            and item.sceneRevision == session.manifest.sceneRevision
            and item.epoch == session.epoch
        ]
        outside = not exact and bool(context or active)
        out_of_order = exact and any(
            item.status not in ("done", "skipped")
            for item in record.plan.steps[: record.plan.steps.index(step)]
        )
        if exact:
            self.bindings[invocation.invocationId] = record.plan.id, record.plan.revision, step.id
            updated = record.model_copy(deep=True)
            updated.results[step.id] = {
                "invocationId": invocation.invocationId,
                "invocationIds": [
                    *record.results.get(step.id, {}).get("invocationIds", []),
                    invocation.invocationId,
                ],
                "consumed": True,
                "outOfOrder": bool(out_of_order),
            }
            updated.plan.status = "running"
            self._save(updated)
            self._full(updated, session)
            self._step(updated, step.id, "running", session)
        if outside or exact:
            session.broadcast_info(
                EntityEvent(
                    **self._envelope(session),
                    entityKey=invocation.entityKey,
                    event="plan.invocation",
                    data={
                        "invocationId": invocation.invocationId,
                        "planId": record.plan.id if record else None,
                        "revision": record.plan.revision if record else None,
                        "stepId": step.id if step else None,
                        "outOfPlan": outside,
                        "outOfOrder": bool(out_of_order),
                    },
                )
            )
        return bool(exact and record.humanRequired), bool(outside and high)

    def _step(self, record, step_id, status, session, **result):
        updated = record.model_copy(deep=True)
        step = next(item for item in updated.plan.steps if item.id == step_id)
        step.status = status
        updated.results.setdefault(step_id, {}).update(result)
        self._save(updated)
        session.broadcast_info(
            PlanStepUpdate(
                **self._envelope(session),
                planId=updated.plan.id,
                revision=updated.plan.revision,
                stepId=step_id,
                status=status,
            )
        )
        if status in ("done", "failed"):
            if all(item.status in ("done", "skipped") for item in updated.plan.steps):
                updated = updated.model_copy(deep=True)
                updated.plan.status = "completed"
                self._save(updated)
                self._full(updated, session)

    def invocation(self, session, update):
        binding = self.bindings.get(update.invocationId)
        if not binding:
            return
        record = self.get(binding[0], binding[1])
        if record.results.get(binding[2], {}).get("invocationId") != update.invocationId:
            return
        step = next(item for item in record.plan.steps if item.id == binding[2])
        if update.status in TERMINAL:
            failed = (
                evaluate_conditions(
                    [step.expect], step.do.entity, session.info.values(), session.info.metadata()
                )
                if update.status == "succeeded" and step.expect
                else []
            )
            status = "done" if update.status == "succeeded" and not failed else "failed"
            self._step(record, step.id, status, session, invocationStatus=update.status, failed=failed)

    def begin_condition(self, session, condition, kind, context):
        if not context:
            return None
        record = self.get(context["planId"])
        self.require_target(record, session)
        if context.get("planRevision", record.plan.revision) != record.plan.revision:
            raise PlanError("invalid_plan_reference", "Plan revision is no longer current")
        step = next((item for item in record.plan.steps if item.id == context["stepId"]), None)
        if step is None or not hasattr(step, kind) or not json_equal(getattr(step, kind), condition):
            raise PlanError("invalid_plan_reference", "Condition does not match this plan step")
        if not self.approved(record):
            raise PlanError("plan_not_approved", "This plan revision requires approval")
        if step.status == "running":
            raise PlanError("plan_busy", "This step is already running")
        order = any(
            item.status not in ("done", "skipped")
            for item in record.plan.steps[: record.plan.steps.index(step)]
        )
        updated = record.model_copy(deep=True)
        updated.plan.status = "running"
        self._save(updated)
        self._full(updated, session)
        self._step(updated, step.id, "running", session, outOfOrder=order)
        return record.plan.id, record.plan.revision, step.id

    def end_condition(self, session, binding, success, **details):
        if binding:
            record = self.get(binding[0], binding[1])
            self._step(record, binding[2], "done" if success else "failed", session, **details)

    def snapshots(self, session):
        return [
            PlanFullUpdate(**self._envelope(session), plan=record.plan)
            for record in self.records.values()
            if (record.sessionId, record.sceneRevision, record.epoch)
            == (session.session_id, session.manifest.sceneRevision, session.epoch)
        ]

    def invalidate(self, session, reason):
        for record in list(self.records.values()):
            if record.sessionId != session.session_id or record.plan.status in ("completed", "abandoned"):
                continue
            updated = record.model_copy(deep=True)
            updated.plan.status = "abandoned"
            for step in updated.plan.steps:
                if step.status == "running":
                    step.status = "failed"
                    updated.results.setdefault(step.id, {})["reason"] = reason
            if updated.approvalId:
                approval = self.approvals.records.get(updated.approvalId)
                if approval and approval.status == "pending":
                    self.approvals.set_status(approval.approvalId, "unavailable")
            self._save(updated)
            self._full(updated, session)

    def decide(self, approval, decision, session, actor, note):
        record = self.get(approval.subjectId)
        self.require_target(record, session)
        if record.approvalId != approval.approvalId or record.plan.revision != approval.details["revision"]:
            raise ApprovalConflict("Approval belongs to an obsolete plan revision")
        if decision == "revoke" and any(step.status != "pending" for step in record.plan.steps):
            raise ApprovalConflict("Plan execution has already begun")
        expected = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[decision]
        if approval.status == expected and (
            (decision == "approve" and record.plan.status in ("approved", "running", "completed"))
            or (decision != "approve" and record.plan.status == "abandoned")
        ):
            return
        if approval.status != expected:
            if approval.status not in (("approved", "denied") if decision == "revoke" else ("pending",)):
                raise ApprovalConflict("This plan approval has already been processed")
            self.approvals.decide(approval.approvalId, decision, actor, note)
        updated = record.model_copy(deep=True)
        updated.plan.status = "approved" if decision == "approve" else "abandoned"
        self._save(updated)
        self._full(updated, session)

    def approved(self, record):
        if record.plan.status not in ("approved", "running"):
            return False
        if not record.humanRequired:
            return True
        approval = self.approvals.records.get(record.approvalId)
        if not approval or approval.status != "approved":
            return False
        details = approval.details
        if (
            approval.sessionId,
            approval.subjectId,
            details.get("revision"),
            details.get("sceneRevision"),
            details.get("epoch"),
        ) != (record.sessionId, record.plan.id, record.plan.revision, record.sceneRevision, record.epoch):
            return False
        try:
            declared = OperationPlan.model_validate(details.get("plan"))
        except ValidationError:
            return False
        return (
            declared.model_dump(exclude={"status", "steps"}),
            [step_content(step) for step in declared.steps],
        ) == (
            record.plan.model_dump(exclude={"status", "steps"}),
            [step_content(step) for step in record.plan.steps],
        )

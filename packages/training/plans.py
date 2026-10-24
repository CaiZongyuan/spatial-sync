"""Persistent training intent and human decisions, independent of transient sessions."""

import threading
import time
import uuid
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from packages.info.approvals import ApprovalConflict
from packages.info.plans import PlanError
from packages.library.common import LibraryError
from packages.library.transactions import durable_json
from packages.protocol.schema import Actor
from packages.training.schema import TrainingPlan


class StoredTrainingPlan(BaseModel):
    plan: TrainingPlan
    requestedBy: Actor
    createdAtMs: float
    approvalId: str | None = None
    changes: list[dict] = Field(default_factory=list)
    goalContext: dict | None = None
    # The shared attention journal supports plans that have no session context.
    sessionId: None = None
    sceneRevision: None = None
    epoch: int = 0


class TrainingPlanStore:
    def __init__(self, path: Path, approvals, tasks, *, on_change=None):
        self.path, self.approvals, self.tasks = path, approvals, tasks
        self.on_change = on_change
        self.programs = None
        self.records: dict[str, StoredTrainingPlan] = {}
        self.history: dict[str, dict[int, StoredTrainingPlan]] = {}
        self._lock = threading.RLock()
        self.job_in_use = lambda identity, revision: False
        self.additional_in_use = lambda identity, revision: False
        self.loop_budgets = None

    def startup(self):
        if self.path.exists():
            from json import loads

            for value in loads(self.path.read_text(encoding="utf-8")):
                self._retain(StoredTrainingPlan.model_validate(value))
        current = {record.approvalId for record in self.records.values()}
        for approval in list(self.approvals.records.values()):
            if (approval.subject == "plan" and approval.status == "pending"
                    and approval.details.get("plan", {}).get("kind") == "training"
                    and approval.details.get("plan", {}).get("layer", "embodied") == "embodied"
                    and approval.approvalId not in current):
                self.approvals.set_status(approval.approvalId, "unavailable")
        for record in self.records.values():
            self._notify(record)

    def _retain(self, record):
        self.records[record.plan.id] = record
        self.history.setdefault(record.plan.id, {})[record.plan.revision] = record

    def _save(self, record):
        history = {(item.plan.id, item.plan.revision): item for versions in self.history.values()
                   for item in versions.values()}
        history[record.plan.id, record.plan.revision] = record
        try:
            durable_json(self.path, [item.model_dump(mode="json", by_alias=True)
                                     for item in history.values()])
        except OSError as exc:
            raise PlanError("plan_persistence_unavailable", "Training plan save failed", status=503) from exc
        self._retain(record)

    def get(self, identifier, revision=None):
        record = (self.records.get(identifier) if revision is None
                  else self.history.get(identifier, {}).get(revision))
        if record is None:
            raise PlanError("unknown_plan", "Unknown training plan or revision", status=404)
        return record

    def snapshot(self):
        """Stable membership for worker-thread projections; no disk work under this lock."""
        with self._lock:
            records = tuple(record for versions in self.history.values() for record in versions.values())
            return records, dict(self.records)

    def document(self, record):
        approval = self.approvals.records.get(record.approvalId)
        status = {"pending": "submitted", "approved": "approved", "denied": "rejected",
                  "revoked": "draft", "unavailable": "abandoned"}.get(
                      approval.status if approval else None, "abandoned")
        document = {**record.plan.model_dump(mode="json", by_alias=True), "status": status,
                "requestedBy": record.requestedBy.model_dump(), "createdAtMs": record.createdAtMs,
                "approvalId": record.approvalId, "approvalStatus": approval.status if approval else None,
                "approvalMode": "human", "changes": record.changes}
        if record.goalContext is not None:
            from copy import deepcopy

            document["loopBudget"] = deepcopy(record.goalContext["review"])
        return document

    def _notify(self, record):
        if self.on_change:
            updated = record.model_copy(deep=True)
            updated.plan.status = self.document(record)["status"]
            self.on_change(updated)

    def _require_program(self, plan, task_snapshot):
        if plan.program is None:
            return
        if self.programs is None:
            raise ValueError("Program library is unavailable")
        program = self.programs.show(plan.program.id)
        task = task_snapshot["task"]
        scene = self.tasks.scenes().resolve(task["scene"])
        if (
            program["contentHash"] != plan.program.contentHash
            or program["metadata"]["implements"] != task["implements"]
            or "newton" not in scene.metadata["sources"]
        ):
            raise ValueError("Program version/hash, Task interface or simulation Source differs")

    def submit(self, document, actor, *, plan_id=None):
        with self._lock:
            previous_revision = self.get(plan_id) if plan_id else None
            value = dict(document)
            value.setdefault("id", plan_id or "plan-" + uuid.uuid4().hex[:16])
            value["revision"] = previous_revision.plan.revision + 1 if previous_revision else 1
            try:
                plan = TrainingPlan.model_validate(value)
                if plan.status != "draft" or (plan_id and plan.id != plan_id):
                    raise ValueError("Submit draft content; identity and lifecycle are server-owned")
                task_snapshot = self.tasks.show(plan.task)
                self._require_program(plan, task_snapshot)
                task = task_snapshot["task"]
                if plan.evaluation.scene != task["scene"]:
                    raise ValueError("Plan evaluation must use the pinned Task scene")
                previous = previous_revision or (self.get(plan.previous) if plan.previous else None)
                if previous and previous.plan.task.split("@")[0] != plan.task.split("@")[0]:
                    raise ValueError("Previous plan must address the same Task")
                if plan.previous == plan.id:
                    raise ValueError("A plan cannot name itself as the previous round")
                if not plan_id and plan.id in self.records:
                    raise ValueError("Plan ID already exists")
            except (ValidationError, ValueError, LibraryError, PlanError) as exc:
                errors = ([{"location": list(item["loc"]), "message": item["msg"]}
                           for item in exc.errors(include_input=False, include_url=False)]
                          if isinstance(exc, ValidationError) else [{"message": str(exc)}])
                raise PlanError("invalid_plan", "Invalid training plan", status=422, errors=errors) from exc
            fresh = plan.model_dump(mode="json", by_alias=True)
            goal_context = self.loop_budgets.prepare(fresh, task_snapshot) if self.loop_budgets else None
            if self.loop_budgets is not None:
                from packages.training.dispatch import require_goal_owner

                require_goal_owner(self.loop_budgets.manager, fresh["goalId"], "embodied")
            plan = TrainingPlan.model_validate(fresh)
            old = previous.plan.model_dump(mode="json", by_alias=True) if previous else {}
            changes = [{"field": key, "before": old.get(key), "after": item}
                       for key, item in fresh.items()
                       if key not in ("id", "revision", "status", "previous") and old.get(key) != item]
            plan.status = "submitted"
            record = StoredTrainingPlan(plan=plan, requestedBy=actor, createdAtMs=time.time() * 1000,
                                        changes=changes, goalContext=goal_context)
            self._save(record)
            try:
                approval = self.approvals.submit(subject="plan", subject_id=plan.id, requester=actor,
                                                 title=plan.title,
                                                 details={"revision": plan.revision,
                                                          "plan": plan.model_dump(mode="json", by_alias=True),
                                                          "changes": changes,
                                                          **({"loopBudget": goal_context["review"]}
                                                             if goal_context is not None else {})})
            except OSError as exc:
                raise PlanError("plan_persistence_unavailable", "Training approval save failed",
                                status=503) from exc
            record = record.model_copy(update={"approvalId": approval.approvalId})
            try:
                self._save(record)
            except PlanError:
                self.approvals.set_status(approval.approvalId, "unavailable")
                raise
            if previous_revision and previous_revision.approvalId:
                old_approval = self.approvals.records[previous_revision.approvalId]
                if old_approval.status == "pending":
                    self.approvals.set_status(old_approval.approvalId, "unavailable")
            self._notify(record)
            return self.document(record)

    def can_revoke(self, approval):
        record = self.records.get(approval.subjectId)
        return bool(record and record.approvalId == approval.approvalId
                    and approval.status in ("approved", "denied")
                    and not self.job_in_use(record.plan.id, record.plan.revision)
                    and not self.additional_in_use(record.plan.id, record.plan.revision))

    def decide(self, approval, decision, actor, note):
        with self._lock:
            record = self.get(approval.subjectId)
            current = self.approvals.records[approval.approvalId]
            if record.approvalId != approval.approvalId:
                raise ApprovalConflict("This training plan revision is no longer current")
            expected = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[decision]
            if current.status == expected:
                return
            if (decision == "revoke" and not self.can_revoke(current)) or (
                decision != "revoke" and current.status != "pending"
            ):
                raise ApprovalConflict("This training plan approval has already been processed")
            if decision == "approve":
                if record.plan.program is not None:
                    try:
                        self._require_program(record.plan, self.tasks.show(record.plan.task))
                    except (ValueError, LibraryError) as exc:
                        raise ApprovalConflict("Frozen program/Task is unavailable or differs") from exc
                if self.loop_budgets is not None:
                    self.loop_budgets.require_fresh_approval(record)
            self.approvals.decide(approval.approvalId, decision, actor, note)
            self._notify(record)

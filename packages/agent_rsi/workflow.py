"""Durable Agent intent consumes existing approvals, Harness and evaluator facts."""

import hashlib
import io
import json
import tarfile
import threading
import time
import uuid
from copy import deepcopy

from pydantic import ValidationError

from packages.agent_rsi.changes import validate_change
from packages.agent_rsi.schema import AgentPlan, StoredAgentPlan
from packages.info.approvals import ApprovalConflict
from packages.info.evaluation_identity import verify_identity
from packages.info.plans import PlanError
from packages.library.common import LibraryError
from packages.library.transactions import durable_json
from packages.runtime_records.native import digest


def comparison_identity(run):
    """#132 supplies these server-produced facts; absence never creates equivalence."""
    value = run.get("comparisonIdentity")
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not value[key].strip() for key in ("environment", "judge")
    ):
        raise PlanError("not_comparable", "Server environment/judge identities are unavailable", status=409)
    return {"conditions": deepcopy(run["key"]), "implementation": deepcopy(value)}


class AgentRSI:
    def __init__(self, manager, agents, external):
        self.manager, self.agents, self.external = manager, agents, external
        self.path = manager.settings.state_root / "agent-rsi.json"
        self.records, self.history, self.baselines, self.promotions, self.proposals = {}, {}, {}, {}, {}
        self.optimizer_sessions, self.grants = {}, {}
        self.audit_error, self._audit_lock = False, threading.Lock()
        self.recovery = []
        self._lock = threading.RLock()
        self.identities = comparison_identity
        self.clock = time.time

    def startup(self):
        with self._lock:
            from packages.agent_rsi.grants import recover_trace

            recover_trace(self)
            self.recovery = self.agents.recover()
            if self.path.exists():
                value = json.loads(self.path.read_bytes())
                if value["version"] != 1:
                    raise ValueError("Unsupported Agent RSI records")
                self.baselines, self.promotions = value["baselines"], value["promotions"]
                self.proposals = value.get("proposals", {})
                self.optimizer_sessions = value.get("optimizerSessions", {})
                self.history = {
                    key: StoredAgentPlan.model_validate(record) for key, record in value["plans"].items()
                }
                for record in self.history.values():
                    previous = self.records.get(record.plan.id)
                    if previous is None or previous.plan.revision < record.plan.revision:
                        self.records[record.plan.id] = record
            current = {record.approvalId for record in self.records.values()}
            for approval in list(self.manager.approvals.records.values()):
                if (
                    approval.subject == "plan"
                    and approval.status == "pending"
                    and approval.details.get("plan", {}).get("layer") == "agent"
                    and approval.approvalId not in current
                ):
                    self.manager.approvals.set_status(approval.approvalId, "unavailable")
            for record in self.records.values():
                self._notify(record)

    def _write(
        self, *, history=None, baselines=None, promotions=None, proposals=None, optimizer_sessions=None
    ):
        history = self.history if history is None else history
        baselines = self.baselines if baselines is None else baselines
        promotions = self.promotions if promotions is None else promotions
        proposals = self.proposals if proposals is None else proposals
        optimizer_sessions = self.optimizer_sessions if optimizer_sessions is None else optimizer_sessions
        try:
            durable_json(
                self.path,
                {
                    "version": 1,
                    "baselines": baselines,
                    "promotions": promotions,
                    "proposals": proposals,
                    "optimizerSessions": optimizer_sessions,
                    "plans": {key: value.model_dump(mode="json") for key, value in history.items()},
                },
            )
        except (OSError, ValueError) as error:
            raise PlanError(
                "agent_rsi_unavailable", "Agent RSI publication is unavailable", status=503
            ) from error
        self.history, self.baselines, self.promotions = history, baselines, promotions
        self.proposals = proposals
        self.optimizer_sessions = optimizer_sessions

    def _save(self, record):
        self._write(history=self.history | {f"{record.plan.id}:{record.plan.revision}": record})
        self.records = self.records | {record.plan.id: record}

    def get(self, identifier, revision=None):
        value = (
            self.records.get(identifier) if revision is None else self.history.get(f"{identifier}:{revision}")
        )
        if value is None:
            raise PlanError("unknown_plan", "Unknown Agent Plan or revision", status=404)
        return value

    def snapshot(self):
        with self._lock:
            return tuple(self.history.values()), dict(self.records)

    def document(self, record):
        from packages.agent_rsi.budgets import public_cost

        approval = self.manager.approvals.records.get(record.approvalId)
        status = record.plan.status
        if status in ("draft", "submitted", "approved", "rejected") and record.result is None:
            status = {
                "pending": "submitted",
                "approved": "approved",
                "denied": "rejected",
                "revoked": "draft",
                "unavailable": "abandoned",
            }.get(approval.status if approval else None, "abandoned")
        return record.plan.model_dump(mode="json") | {
            "status": status,
            "approvalId": record.approvalId,
            "approvalStatus": approval.status if approval else None,
            "approvalMode": "human",
            "requestedBy": record.requestedBy.model_dump(mode="json"),
            "createdAtMs": record.createdAtMs,
            "changes": deepcopy(record.changes),
            "context": {
                "reference": self.public_baseline(record.context["reference"]),
                "candidateHash": record.context["candidateHash"],
                "candidateArchiveHash": record.context["candidateArchiveHash"],
            },
            "runId": record.runId,
            "activation": deepcopy(record.context.get("activation")),
            "result": {
                key: public_cost(self, value) if key in ("cost", "executionCost") else deepcopy(value)
                for key, value in record.result.items()
            }
            if record.result
            else None,
            "promotions": [
                {key: value.get(key) for key in ("id", "status", "approvalId")}
                for value in self.promotions.values()
                if value["planId"] == record.plan.id
            ],
            "stop": {
                key: deepcopy(value)
                for key, value in record.context.get("contamination", {}).items()
                if key in ("application", "cleanup", "files", "errorCode")
            }
            or None,
            "recovery": [
                deepcopy(value) for value in self.recovery if value["agentId"] == record.plan.agentId
            ],
        }

    def _notify(self, record):
        projected = record.model_copy(deep=True)
        projected.plan.status = self.document(record)["status"]
        self.manager.inbox.plan(projected)

    def current_identity(self, run):
        try:
            if not isinstance(run.get("identitySnapshot"), dict):
                raise ValueError("Frozen implementation is unavailable")
            suite = self.manager.evaluations.suites.resolve(run["suite"])
            scene = self.manager.library.resolve(run["pins"]["scene"], source=run["pins"]["source"])
            if (
                suite.content_hash != run["suiteContentHash"]
                or scene.content_hash != run["pins"]["sceneContentHash"]
                or run["runtimeHash"] != self.manager.evaluations._runtime_hash
            ):
                raise ValueError("Fixed evaluation packages or runtime changed")
            verify_identity(
                run["identitySnapshot"], directory=self.manager.evaluations.store.root / "identity-inputs"
            )
        except (OSError, ValueError, KeyError, TypeError, LibraryError) as error:
            raise PlanError(
                "not_comparable", "Fixed implementation evidence is unavailable or changed", status=409
            ) from error
        return self.identities(run)

    def complete_train(self, evaluation_id, agent):
        run = self.external.get(evaluation_id)
        suite = self.manager.evaluations.suites.resolve(run["suite"])
        expected = {
            (case["id"], repeat)
            for case in suite.public()["cases"]
            for repeat in range(1, suite.metadata["runsPerCase"] + 1)
        }
        observed = [(case["caseId"], case["repeat"]) for case in run["cases"]]
        row = next(
            (
                value
                for value in self.manager.evaluations.store.rows
                if value["evaluationId"] == evaluation_id
            ),
            None,
        )
        if (
            run["agentId"] != agent
            or run["status"] != "completed"
            or run["purpose"] != "train"
            or run["split"] != "train"
            or not run["cleanup"]["complete"]
            or len(observed) != len(expected)
            or set(observed) != expected
            or any(case["status"] != "completed" for case in run["cases"])
            or row is None
            or row != run.get("result")
            or row["decision"] != "reported"
        ):
            raise PlanError(
                "agent_evaluation_incomplete", "A complete valid train scope is required", status=409
            )
        for case in run["cases"]:
            if {artifact["id"] for artifact in case["artifacts"]} != {"trace", "final", "score"}:
                raise PlanError(
                    "agent_evidence_unavailable", "Complete case evidence is required", status=409
                )
            for artifact in case["artifacts"]:
                content = self.external.artifact(run, case, artifact["id"])
                if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                    raise PlanError(
                        "agent_evidence_changed", "Frozen evaluation evidence changed", status=409
                    )
        self.current_identity(run)
        return run

    def baseline(self, identifier, evaluation_id, request_id):
        with self._lock:
            agent = self.agents.show(identifier)
            known = next(
                (
                    value
                    for value in self.baselines.values()
                    if value["agentId"] == agent["id"] and value["requestId"] == request_id
                ),
                None,
            )
            if known is not None:
                if known["evaluationId"] != evaluation_id:
                    raise PlanError(
                        "idempotency_conflict", "Baseline retry changed its evaluation", status=409
                    )
                return self.public_baseline(known)
            run = self.complete_train(evaluation_id, agent["id"])
            version = self.agents.version(identifier, agent["referenceVersion"])
            if (
                run["harnessVersion"] != version["version"]
                or run["pins"]["harnessHash"] != version["contentHash"]
            ):
                raise PlanError(
                    "agent_reference_mismatch", "Evaluate the exact Reference Harness", status=409
                )
            comparison = self.identities(run)
            key = digest({"agent": agent["id"], "comparison": comparison})
            value = {
                "agentId": agent["id"],
                "requestId": request_id,
                "evaluationId": evaluation_id,
                "harnessVersion": version["version"],
                "harnessHash": version["contentHash"],
                "archiveHash": version["archiveHash"],
                "suite": run["suite"],
                "runtime": deepcopy(run["runtime"]),
                "comparison": comparison,
                "score": run["result"]["score"],
                "at": run["result"]["at"],
            }
            previous = self.baselines.get(key)
            if previous is not None and previous != value:
                raise PlanError(
                    "agent_baseline_exists", "This condition group has a formal Reference", status=409
                )
            self._write(baselines=self.baselines | {key: value})
            return self.public_baseline(value)

    def public_baseline(self, value, *, current=False):
        from apps.api.app.evaluation_views import public_conditions

        # Internal environment/judge IDs can bind private inputs; expose safe equivalence only.
        result = {key: deepcopy(item) for key, item in value.items() if key != "comparison"} | {
            "conditionsGroup": value["evaluationId"],
            "conditions": public_conditions(value["comparison"]["conditions"]),
        }
        if current:
            try:
                actual = self.current_identity(self.external.get(value["evaluationId"]))
                if actual != value["comparison"]:
                    raise PlanError("not_comparable", "Fixed Reference conditions changed", status=409)
            except (PlanError, LibraryError):
                result.update(
                    score=None,
                    recordedScore=value["score"],
                    comparisonState="unknown",
                    comparisonReason="not_comparable",
                )
            else:
                result.update(recordedScore=value["score"], comparisonState="known", comparisonReason=None)
        return result

    def propose(self, identifier, body):
        with self._lock:
            agent = self.agents.show(identifier)
            key = digest({"agent": agent["id"], "request": body.requestId})
            inputs_hash = digest(body.model_dump(mode="json"))
            previous = self.proposals.get(key)
            if previous is not None and previous["inputsHash"] != inputs_hash:
                raise PlanError(
                    "idempotency_conflict", "Candidate request changed its fixed input", status=409
                )
            if previous is not None and previous.get("version") is not None:
                return self.proposal_document(identifier, previous["version"])
            if agent["referenceVersion"] != body.referenceVersion:
                raise PlanError("agent_reference_mismatch", "Select the current Reference", status=409)
            original = {
                item["path"]: item["content"]
                for item in self.agents.files(identifier, body.referenceVersion)["files"]
            }
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w") as archive:
                for item in body.files:
                    text = item.content
                    if item.path in original and "\r\n" in original[item.path]:
                        text = text.replace("\r\n", "\n").replace("\n", "\r\n")
                    content = text.encode()
                    entry = tarfile.TarInfo(item.path)
                    entry.size = len(content)
                    archive.addfile(entry, io.BytesIO(content))
            archive = output.getvalue()
            validate_change(self.agents, identifier, body.referenceVersion, archive, body.change)
            intent = {
                "agentId": agent["id"],
                "inputsHash": inputs_hash,
                "referenceVersion": body.referenceVersion,
                "status": "prepared",
                "change": body.change.model_dump(mode="json"),
            }
            self._write(proposals=self.proposals | {key: intent})
            version = self.agents.stage(identifier, archive, reference_version=body.referenceVersion)
            self._write(
                proposals=self.proposals | {key: intent | {"status": "staged", "version": version["version"]}}
            )
            return self.proposal_document(identifier, version["version"])

    def proposal_document(self, identifier, version):
        from packages.library.agent_transitions import workspace_hash

        record = self.agents.version(identifier, version)
        current = self.agents.show(identifier)["currentVersion"]
        applied = (
            current == version
            and workspace_hash(self.agents._path(identifier) / "harness") == record["contentHash"]
        )
        return record | {"staged": True, "applied": applied}

    def submit(self, document, actor, *, plan_id=None):
        with self._lock:
            previous = self.get(plan_id) if plan_id else None
            if previous and (
                previous.context.get("runRequest") is not None or previous.plan.status == "contaminated"
            ):
                raise PlanError(
                    "agent_plan_in_use", "A started or contaminated Agent Plan cannot be revised", status=409
                )
            value = dict(document)
            value.setdefault("id", plan_id or "plan-" + uuid.uuid4().hex[:16])
            value["revision"] = previous.plan.revision + 1 if previous else 1
            try:
                plan = AgentPlan.model_validate(value)
                from packages.training.dispatch import require_goal_owner

                require_goal_owner(
                    self.manager, plan.goalId, "agent", agent_id=plan.agentId, suite=plan.suite
                )
                if plan.status != "draft" or (plan_id and plan.id != plan_id):
                    raise ValueError("Plan identity and lifecycle are server-owned")
                if not plan_id and plan.id in self.records:
                    raise ValueError("Plan identity already exists")
                if previous and previous.plan.goalId != plan.goalId:
                    raise ValueError("A revision cannot change its Goal")
                if plan.previous:
                    ancestor = self.get(plan.previous)
                    if (
                        ancestor.plan.id == plan.id
                        or ancestor.plan.agentId != plan.agentId
                        or ancestor.plan.goalId != plan.goalId
                    ):
                        raise ValueError("Previous round must address this Agent and Goal")
                reference = next(
                    (
                        value
                        for value in self.baselines.values()
                        if value["agentId"] == plan.agentId
                        and value["evaluationId"] == plan.referenceEvaluationId
                    ),
                    None,
                )
                if reference is None:
                    raise ValueError("Select a measured formal Reference")
                if (
                    reference["harnessVersion"] != plan.referenceVersion
                    or reference["suite"] != plan.suite
                    or reference["runtime"] != plan.runtime.model_dump(mode="json")
                ):
                    raise ValueError("Plan must preserve fixed Reference conditions")
                run = self.complete_train(plan.referenceEvaluationId, plan.agentId)
                if self.identities(run) != reference["comparison"]:
                    raise ValueError("Reference comparison conditions changed")
                agent = self.agents.show(plan.agentId)
                candidate = self.agents.version(plan.agentId, plan.candidateVersion)
                if (
                    agent["referenceVersion"] != plan.referenceVersion
                    or agent["currentVersion"] not in (plan.referenceVersion, plan.candidateVersion)
                    or plan.candidateVersion <= plan.referenceVersion
                ):
                    raise ValueError("Select the current candidate and unchanged Reference")
                paths = validate_change(
                    self.agents, plan.agentId, plan.referenceVersion, plan.candidateVersion, plan.change
                )
            except (ValidationError, ValueError, LibraryError) as error:
                raise PlanError("invalid_agent_plan", "Invalid bounded Agent Plan", status=422) from error
            plan.status = "submitted"
            record = StoredAgentPlan(
                plan=plan,
                requestedBy=actor,
                createdAtMs=time.time() * 1000,
                context={
                    "reference": deepcopy(reference),
                    "candidateHash": candidate["contentHash"],
                    "candidateArchiveHash": candidate["archiveHash"],
                },
                changes=[{"field": path} for path in paths],
            )
            self._save(record)
            approval = self.manager.approvals.submit(
                subject="plan",
                subject_id=plan.id,
                requester=actor,
                title=plan.title,
                details={
                    "revision": plan.revision,
                    "plan": plan.model_dump(mode="json"),
                    "changes": record.changes,
                    "reference": self.public_baseline(reference),
                },
            )
            record = record.model_copy(update={"approvalId": approval.approvalId})
            try:
                self._save(record)
            except PlanError:
                self.manager.approvals.set_status(approval.approvalId, "unavailable")
                raise
            if previous and previous.approvalId:
                old = self.manager.approvals.records[previous.approvalId]
                if old.status == "pending":
                    self.manager.approvals.set_status(old.approvalId, "unavailable")
            self._notify(record)
            return self.document(record)

    def can_revoke(self, approval):
        record = self.records.get(approval.subjectId)
        return bool(
            record
            and record.approvalId == approval.approvalId
            and record.context.get("runRequest") is None
            and approval.status in ("approved", "denied")
        )

    def decide(self, approval, decision, actor, note):
        with self._lock:
            record = self.get(approval.subjectId)
            current = self.manager.approvals.records[approval.approvalId]
            if (
                record.approvalId != approval.approvalId
                or record.plan.status == "contaminated"
                or record.context.get("runRequest") is not None
            ):
                raise ApprovalConflict("Agent Plan revision is no longer available for this decision")
            expected = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[decision]
            if current.status == expected:
                return
            if (decision == "revoke" and not self.can_revoke(current)) or (
                decision != "revoke" and current.status != "pending"
            ):
                raise ApprovalConflict("This Agent Plan approval has already been processed")
            if decision == "approve":
                from packages.agent_rsi.budgets import authority

                if record.plan.budgetException is not None and not note.strip():
                    raise PlanError(
                        "agent_budget_exception_reason_required",
                        "A person must record a reason for this finite budget exception",
                        status=409,
                    )
                original, _ = authority(self, record.plan.goalId)
                if original is not None and original.plan.goalBudget != record.plan.goalBudget:
                    raise PlanError(
                        "agent_goal_budget_changed", "Goal limits cannot reset on a new Plan", status=409
                    )
            self.manager.approvals.decide(approval.approvalId, decision, actor, note)
            self._notify(record)

    def prepare_run(self, identifier, revision, request_id):
        with self._lock:
            record = self.get(identifier)
            if revision != record.plan.revision:
                raise PlanError("plan_not_approved", "Select the approved current revision", status=409)
            approval = self.manager.approvals.records.get(record.approvalId)
            if (
                approval is None
                or approval.status != "approved"
                or record.plan.status in ("contaminated", "rejected", "promoted")
            ):
                raise PlanError("plan_not_approved", "An approved live Agent Plan is required", status=409)
            known = record.context.get("runRequest")
            if known is not None and known != request_id:
                raise PlanError(
                    "idempotency_conflict", "This Plan already owns an execution intent", status=409
                )
            reference = self.complete_train(record.plan.referenceEvaluationId, record.plan.agentId)
            if self.identities(reference) != record.context["reference"]["comparison"]:
                raise PlanError("not_comparable", "Reference conditions changed before admission", status=409)
            suite = self.manager.evaluations.suites.resolve(record.plan.suite)
            required = len(suite.public()["cases"]) * suite.metadata["runsPerCase"]
            if required > record.plan.budget.cases:
                raise PlanError(
                    "agent_budget_exhausted", "Plan cannot cover the complete train scope", status=409
                )
            from packages.agent_rsi.budgets import require_budget

            dispatch_id = "rsi-" + digest({"plan": identifier, "revision": revision, "request": request_id})
            admitted = any(
                run.get("agentRound", {}).get("planId") == identifier and run.get("requestId") == dispatch_id
                for run in self.manager.loop_budgets._evaluation_records()
            )
            require_budget(self, record, additional_cases=0 if admitted else required)
            agent = self.agents.show(record.plan.agentId)
            if agent["referenceVersion"] != record.plan.referenceVersion or agent["currentVersion"] not in (
                record.plan.referenceVersion,
                record.plan.candidateVersion,
            ):
                raise PlanError(
                    "agent_reference_mismatch", "Agent versions changed before admission", status=409
                )
            record = record.model_copy(deep=True)
            record.context["runRequest"] = request_id
            record.plan.status = "activating"
            record.context["activation"] = {"status": "applying"}
            self._save(record)
            self._notify(record)
            from packages.library.agent_transitions import workspace_hash

            try:
                current = self.agents.show(record.plan.agentId)
                if current["currentVersion"] == record.plan.referenceVersion:
                    self.agents.transition(
                        record.plan.agentId,
                        target_version=record.plan.candidateVersion,
                        expected_version=record.plan.referenceVersion,
                        expected_hash=record.context["reference"]["harnessHash"],
                        request_id="activate-" + digest({"planId": identifier, "revision": revision}),
                        outcome=None,
                    )
                path = self.agents._path(record.plan.agentId) / "harness"
                if (
                    self.agents.show(record.plan.agentId)["currentVersion"] != record.plan.candidateVersion
                    or workspace_hash(path) != record.context["candidateHash"]
                ):
                    raise PlanError(
                        "harness_transition_conflict",
                        "Candidate editor bytes changed before dispatch",
                        status=409,
                    )
            except (LibraryError, PlanError) as error:
                failed = record.model_copy(deep=True)
                failed.plan.status = "activation_blocked"
                failed.context["activation"] = {"status": "blocked", "errorCode": error.detail["code"]}
                self._save(failed)
                self._notify(failed)
                raise PlanError(
                    error.detail["code"],
                    "Candidate files require recovery before dispatch",
                    status=error.status,
                ) from error
            record = record.model_copy(deep=True)
            record.plan.status = "evaluating"
            record.context["activation"] = {"status": "complete"}
            self._save(record)
            self._notify(record)
            return {
                "requestId": dispatch_id,
                "suite": record.plan.suite,
                "harnessVersion": record.plan.candidateVersion,
                "runtime": record.plan.runtime.model_dump(mode="json"),
                "purpose": "train",
                "wallSeconds": record.plan.budget.wallSeconds,
            }, {"planId": identifier, "planRevision": revision, "goalId": record.plan.goalId}

    def require_admission(self, run):
        if run.get("agentPromotion") is not None:
            from packages.agent_rsi.promotions import require_admission

            return require_admission(self, run)
        context = run.get("agentRound")
        if context is None:
            return
        with self._lock:
            record = self.get(context["planId"])
            approval = self.manager.approvals.records.get(record.approvalId)
            if (
                approval is None
                or approval.status != "approved"
                or record.plan.revision != context["planRevision"]
                or record.plan.goalId != context["goalId"]
                or record.plan.status != "evaluating"
                or record.plan.agentId != run["agentId"]
                or record.plan.candidateVersion != run["harnessVersion"]
                or record.plan.suite != run["suite"]
                or record.plan.runtime.model_dump(mode="json") != run["runtime"]
                or record.context["candidateHash"] != run["pins"]["harnessHash"]
            ):
                raise PlanError(
                    "plan_not_approved", "Agent execution no longer matches approved intent", status=409
                )
            from packages.agent_rsi.budgets import require_budget

            require_budget(self, record, run)

    def retain_run(self, identifier, revision, run_id):
        with self._lock:
            record = self.get(identifier, revision).model_copy(deep=True)
            if record.runId not in (None, run_id):
                raise PlanError(
                    "idempotency_conflict", "Plan already owns a different evaluation", status=409
                )
            record.runId = run_id
            self._save(record)
            self._notify(record)
            return self.document(record)

    def settle(self, identifier, revision):
        with self._lock:
            record = self.get(identifier)
            if record.plan.revision != revision or record.plan.status == "contaminated":
                raise PlanError("agent_round_stopped", "This round cannot be settled", status=409)
            if record.result and record.result.get("application") == "complete":
                self.publish_notebook(record)
                return self.document(record)
            if record.runId is None:
                raise PlanError("agent_round_incomplete", "Start the approved evaluation first", status=409)
            run = self.external.get(record.runId)
            if run["status"] not in ("completed", "failed", "cancelled") or not run["cleanup"]["complete"]:
                raise PlanError(
                    "agent_round_incomplete", "Evaluation and owned cleanup must finish", status=409
                )
            if record.result is None:
                decision, reason, score = "inconclusive", run.get("error", {}).get("code"), None
                if run["status"] == "completed":
                    try:
                        self.complete_train(run["evaluationId"], record.plan.agentId)
                        comparable = self.identities(run) == record.context["reference"]["comparison"]
                    except (PlanError, LibraryError) as error:
                        reason = error.detail["code"]
                    else:
                        if comparable:
                            score = run["result"]["score"]
                            decision = (
                                "accepted" if score > record.context["reference"]["score"] else "rejected"
                            )
                        else:
                            reason = "not_comparable"
                record = record.model_copy(deep=True)
                record.result = {
                    "decision": decision,
                    "reason": reason,
                    "score": score,
                    "ci95": None,
                    "evaluationId": run["evaluationId"],
                    "application": "applying",
                    "referenceAdvanced": False,
                    "cost": self.external.cost(run["cases"]),
                    "at": run.get("endedAt"),
                }
                from packages.agent_rsi.budgets import native_total

                record.result["executionCost"] = record.result["cost"]
                record.result["cost"] = native_total(self, plan=record.plan.id)
                record.plan.status = "retaining" if decision == "accepted" else "restoring"
                self._save(record)  # Decision intent precedes the first filesystem effect.
            accepted = record.result["decision"] == "accepted"
            try:
                receipt = self.agents.transition(
                    record.plan.agentId,
                    target_version=(
                        record.plan.candidateVersion if accepted else record.plan.referenceVersion
                    ),
                    expected_version=record.plan.candidateVersion,
                    expected_hash=record.context["candidateHash"],
                    request_id="settle-" + digest({"planId": identifier, "revision": revision}),
                    outcome_version=record.plan.candidateVersion,
                    outcome="accepted" if accepted else "rejected",
                )
            except LibraryError as error:
                failed = record.model_copy(deep=True)
                failed.result.update(application="blocked", errorCode=error.detail["code"])
                failed.plan.status = "restore_blocked"
                self._save(failed)
                self._notify(failed)
                raise PlanError(
                    error.detail["code"], "Harness files require recovery", status=error.status
                ) from error
            record = record.model_copy(deep=True)
            record.result.update(application="complete", files=receipt)
            record.plan.status = record.result["decision"]
            self._save(record)
            self._notify(record)
            self.publish_notebook(record)
            return self.document(record)

    def publish_notebook(self, record):
        from packages.agent_rsi.notebooks import notebook_entry

        run = self.external.get(record.runId)
        self.manager.notebooks.store.add(notebook_entry(record, run))

    def reconcile(self):
        for record in tuple(self.history.values()):
            if record.result and record.result.get("application") == "complete":
                self.publish_notebook(record)

    def goals(self):
        return sorted(
            {record.plan.goalId for record in self.history.values()}
            | {value["goalId"] for value in self.optimizer_sessions.values()}
        )

    def public_notebook_entry(self, entry, *, identity_cache=None):
        from packages.agent_rsi.budgets import public_cost
        from packages.agent_rsi.notebooks import remaining_gap

        result = deepcopy(entry)
        result["cost"] = public_cost(self, entry["cost"])
        cache = {} if identity_cache is None else identity_cache

        def identity(identifier):
            if identifier not in cache:
                try:
                    cache[identifier] = self.current_identity(self.external.get(identifier))
                except (PlanError, LibraryError):
                    cache[identifier] = None
            return cache[identifier]

        try:
            record = self.get(entry["plan"]["id"], entry["plan"]["revision"])
            result["currentDisposition"] = record.plan.status
            candidate, reference = identity(record.runId), identity(record.plan.referenceEvaluationId)
            comparable = bool(
                entry["comparison"]["state"] == "comparable"
                and entry["score"] is not None
                and entry["reportedDecision"] in ("accepted", "rejected")
                and record.plan.status != "contaminated"
                and candidate
                and reference
                and candidate == reference == record.context["reference"]["comparison"]
            )
        except PlanError:
            comparable = False
        if not comparable:
            current = entry["comparison"] | {
                "state": "unknown",
                "reason": "not_comparable",
                "referenceEvaluationId": None,
                "referenceScore": None,
                "remainingGapReduction": None,
                "gapReason": "not_comparable",
            }
            if current != entry["comparison"]:
                result["recordedComparison"] = deepcopy(entry["comparison"])
                result["comparison"] = current
        else:
            gap, reason = remaining_gap(
                entry["comparison"]["referenceScore"], entry["score"]["score"], comparable=True
            )
            if (gap, reason) != (
                entry["comparison"]["remainingGapReduction"],
                entry["comparison"]["gapReason"],
            ):
                result["recordedComparison"] = deepcopy(entry["comparison"])
                result["comparison"].update(remainingGapReduction=gap, gapReason=reason)
        return result

    def record_tool(self, grant, method, path, status, request_id=None):
        from packages.agent_rsi.grants import record_tool

        return record_tool(self, grant, method, path, status, request_id=request_id)

    def public_recordings(self, agent_id):
        return {
            case["recordingId"]
            for run in self.manager.evaluations.store.records.values()
            if run.get("layer") == "agent" and run.get("split") == "train" and run["agentId"] == agent_id
            for case in run["cases"]
            if case.get("recordingId")
        }

    def private_recording(self, recording_id):
        from packages.info.evaluation_privacy import private_recording

        return private_recording(self.manager, recording_id)

    def native_associations(self, identity):
        capture = self.external.runtime_records.records.get(identity, {})
        context = (
            capture.get("privateContext") or self.private_native_context(capture.get("nativeSessionId")) or {}
        )
        if context:
            return {
                key: context[key]
                for key in ("agentId", "harnessVersion", "planId", "planRevision", "goalId")
                if key in context
            }
        optimizer = self.optimizer_sessions.get(capture.get("nativeSessionId"))
        if optimizer is not None:
            # An Optimizer grant binds its Goal; its conversation may span several Plans.
            return {key: optimizer[key] for key in ("agentId", "goalId")}
        for run in tuple(self.manager.evaluations.store.records.values()):
            if (run.get("agentRound") is not None or run.get("agentPromotion") is not None) and any(
                case.get("runtimeRecordId") == identity for case in run["cases"]
            ):
                return {
                    "agentId": run["agentId"],
                    "harnessVersion": run["harnessVersion"],
                    "planId": run["planId"],
                    "planRevision": run["planRevision"],
                    "goalId": run["goalId"],
                }
        return {}

    def private_native_context(self, native_session_id):
        optimizer = self.optimizer_sessions.get(native_session_id, {})
        if optimizer.get("contaminationContext") is not None:
            return deepcopy(optimizer["contaminationContext"])
        for promotion in tuple(self.promotions.values()):
            context = promotion.get("nativeBindings", {}).get(native_session_id)
            if context is not None:
                return deepcopy(context)
        return None

    def loop(self, goal):
        self.reconcile()
        cache = {}
        entries = [
            self.public_notebook_entry(entry, identity_cache=cache)
            for entry in self.manager.notebooks.store.list(goal)
            if entry.get("layer") == "agent"
        ]
        rounds = [entry | {"round": index + 1} for index, entry in enumerate(entries)]
        from packages.agent_rsi.budgets import budget

        latest_promotion = next(
            (value for value in reversed(tuple(self.promotions.values())) if value["goalId"] == goal), None
        )

        return {
            "layer": "agent",
            "goalId": goal,
            "rounds": rounds,
            "plans": [
                self.document(record) for record in self.records.values() if record.plan.goalId == goal
            ],
            "reference": entries[-1]["comparison"] if entries else None,
            "candidate": entries[-1] if entries else None,
            "deployment": {key: latest_promotion.get(key) for key in ("id", "status", "approvalId")}
            if latest_promotion
            else {"status": "not_requested"},
            "budget": budget(self, goal),
        }

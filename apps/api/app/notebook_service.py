"""Link real evaluator receipts to public Lab Notebooks without exporting private suites."""

import threading
from copy import deepcopy

from apps.api.app.evaluation_views import public_episode
from packages.info.evaluation_identity import comparison_identity, implementation_identities
from packages.info.plans import PlanError
from packages.library.common import LibraryError
from packages.training.loop_budgets import goal_id, plan_budget
from packages.training.notebook_contract import NotebookEntry
from packages.training.notebook_rules import RULE_VERSION, judge_hypothesis, remaining_gap_reduction
from packages.training.notebooks import LabNotebooks

TERMINAL = {"completed", "failed", "cancelled"}


class NotebookService:
    def __init__(self, manager):
        self.manager = manager
        self.store = LabNotebooks(manager.settings.state_root, manager.library_assets.roots["user"])
        self.started = False
        self.error = False
        self._lock = threading.RLock()
        self.identities = implementation_identities

    def startup(self):
        self.store.startup()
        self.started = True
        self.reconcile()

    def prepare_evaluation(self, body, suite, scene):
        if body.planId is None:
            if body.planRevision is not None:
                raise PlanError("invalid_plan_reference", "A revision requires a Plan ID", status=422)
            return None
        record = self.manager.training_plans.get(body.planId)
        plan = self.manager.training_plans.document(record)
        if plan["status"] != "approved" or body.planRevision not in (None, plan["revision"]):
            raise PlanError("plan_not_approved", "Select the current approved Plan revision", status=409)
        task = self.manager.tasks.show(plan["task"])
        metadata = task["task"]
        if (
            metadata["scene"] != scene.id
            or metadata["robot"] != suite.task["robot"]
            or metadata["implements"] != suite.task["implements"]
        ):
            # Validate public binding compatibility without returning private grading predicates.
            raise PlanError(
                "evaluation_plan_mismatch", "Evaluation and Plan have different fixed bindings", status=409
            )
        try:
            implementations = deepcopy(self.identities())
        except (OSError, ValueError) as error:
            raise PlanError(
                "comparison_evidence_unavailable", "Required comparison evidence is unavailable", status=503
            ) from error
        return {
            "version": 1,
            "planId": plan["id"],
            "planRevision": plan["revision"],
            "planApprovalId": record.approvalId,
            "goalId": goal_id(plan),
            "taskContentHash": task["contentHash"],
            "plan": {
                "id": plan["id"],
                "revision": plan["revision"],
                "task": plan["task"],
                "goalId": goal_id(plan),
                "goal": plan["goal"],
                "title": plan["title"],
                "changedFields": [change["field"] for change in record.changes],
                "targetSuccessRate": (
                    plan["evaluation"]["threshold"] / plan["evaluation"]["episodes"]
                    if plan["evaluation"].get("threshold") is not None
                    else None
                ),
            },
            "hypotheses": deepcopy(plan["hypotheses"]),
            "ruleVersion": RULE_VERSION,
            "implementations": implementations,
            "comparisonInputs": {
                "suite": suite.id,
                "suiteContentHash": suite.content_hash,
                "scene": scene.id,
                "sceneContentHash": scene.content_hash,
                "assets": sorted(
                    (*scene.asset_dependencies(), *scene.template_dependencies()), key=lambda item: item["id"]
                ),
            },
            "allocatedEpisodes": sum(case["split"] == "train" for case in suite.metadata["episodes"]),
        }

    def admit_evaluation(self, context):
        """Called inside the evaluator's existing admission lock immediately before durable save."""
        if context is None:
            return {}
        current = self.manager.training_plans.get(context["planId"])
        plan = self.manager.training_plans.document(current)
        if (
            plan["status"] != "approved"
            or plan["revision"] != context["planRevision"]
            or current.approvalId != context["planApprovalId"]
        ):
            raise PlanError("plan_not_approved", "Plan changed before evaluation admission", status=409)
        self.manager.jobs._require_available()
        used = 0
        for record in self.manager.loop_budgets._evaluation_records():
            if record.get("planId") != context["planId"]:
                continue
            attempts = record.get("attemptedEpisodes", len(record.get("episodes", [])))
            if self.manager.loop_budgets._held_evaluation(record):
                attempts = max(attempts, record.get("allocatedEpisodes", 0))
            used += attempts
        if used + context["allocatedEpisodes"] > plan_budget(plan)["episodes"]:
            raise PlanError(
                "plan_evaluation_budget_exhausted",
                "A fresh approved Plan is required for more evaluation attempts",
                status=409,
            )
        return {
            "planId": context["planId"],
            "planRevision": context["planRevision"],
            "goalId": context["goalId"],
            "allocatedEpisodes": context["allocatedEpisodes"],
            "attemptedEpisodes": 0,
            "notebookContext": context,
        }

    def plan_in_use(self, identity, revision):
        return any(
            record.get("planId") == identity
            and record.get("planRevision") == revision
            and self.manager.loop_budgets._held_evaluation(record)
            for record in self.manager.loop_budgets._evaluation_records()
        )

    def complete_evaluation(self, record):
        if record.get("notebookContext") is None or "notebookExecution" in record:
            return
        try:
            record["notebookExecution"] = deepcopy(self.identities())
        except (OSError, ValueError):
            record["notebookExecution"] = (
                None  # Missing evidence never becomes a fabricated historical identity.
            )

    @staticmethod
    def _comparison_key(record):
        context = record.get("notebookContext")
        identity = comparison_identity(record)
        if (
            context is None
            or identity is None
            or record.get("key") is None
            or record.get("notebookExecution") != context["implementations"]
        ):
            return None
        return {
            "inputs": context["comparisonInputs"],
            "execution": record["key"],
            "environment": identity["environment"],
            "judge": identity["judge"],
        }

    def _comparison(self, record, result, current_implementations):
        records = self.manager.evaluations.store.records
        own_key = self._comparison_key(record)
        group = record["evaluationId"]
        if own_key is not None:
            for entry in tuple(self.store.entries.values()):
                peer = records.get(entry["evaluationId"])
                if peer is not None and self._comparison_key(peer) == own_key:
                    group = entry["comparison"]["conditionsGroup"]
                    break  # A committed public anchor is stable across later IDs/timestamps.
        reference_id = result.get("reference") if result else None
        reference = records.get(reference_id)
        if reference is not None and reference.get("split") != "train":
            reference = None
            reference_id = None
        reference_score = reference.get("result", {}).get("score") if reference else None
        context = record["notebookContext"]
        if current_implementations is None:
            state, reason = "unknown", "Execution completion identity was not retained"
        elif context["implementations"] != current_implementations:
            state, reason = "changed_during_execution", "Implementation changed after evaluation admission"
        elif not result or own_key is None:
            state, reason = "unknown", "Complete comparison evidence is unavailable"
        elif reference_id is None:
            state, reason = "baseline", None
        elif reference is None or self._comparison_key(reference) is None:
            state, reason = "unknown", "Reference has no frozen comparison evidence"
        elif self._comparison_key(reference) != own_key:
            state, reason = "different", "Evaluation environment or judge differs from Reference"
        else:
            state, reason = "comparable", None
        gap = remaining_gap_reduction(
            result.get("score") if result else None, reference_score, comparable=state == "comparable"
        )
        return {
            "state": state,
            "reason": reason,
            "conditionsGroup": group,
            "referenceEvaluationId": reference_id,
            "referenceScore": reference_score,
            "remainingGapReduction": gap["value"],
            "gapReason": gap["reason"],
        }

    def _entry(self, record, current_implementations):
        context = record["notebookContext"]
        result = record.get("result") if record["status"] == "completed" else None
        score = None
        if result is not None:
            score = {key: result[key] for key in ("successes", "episodes", "score", "ci95")}
            score["phases"] = {
                name: {key: phase[key] for key in ("passed", "episodes", "rate")}
                for name, phase in result.get("phases", {}).items()
            }
        deployment = next(
            (
                approval
                for approval in reversed(tuple(self.manager.approvals.records.values()))
                if approval.subject == "policy"
                and approval.details.get("evaluationId") == record["evaluationId"]
            ),
            None,
        )
        comparison = self._comparison(record, result, current_implementations)
        hypotheses = []
        for definition in context["hypotheses"]:
            judgement = judge_hypothesis(
                definition, result or {"split": "train"}, complete=result is not None
            )
            if context.get("ruleVersion") != RULE_VERSION:
                judgement.update(
                    verdict="inconclusive", observed=None, ci95=None, reason="rule_version_unavailable"
                )
            if comparison["state"] == "changed_during_execution":
                judgement.update(
                    verdict="inconclusive",
                    observed=None,
                    ci95=None,
                    reason="implementation_changed_during_execution",
                )
            hypotheses.append({"definition": definition, "automatic": judgement, "overrides": []})
        known_gpu = sum(
            job["chargedGpuHours"]
            for job in tuple(self.manager.jobs.records.values())
            if job["planId"] == context["planId"]
        )
        if self.manager.jobs._failure:
            known_gpu = None
        return NotebookEntry.model_validate(
            {
                "entryId": "entry-" + record["evaluationId"].removeprefix("eval-"),
                "evaluationId": record["evaluationId"],
                "goalId": context["goalId"],
                "plan": context["plan"],
                "suite": record["suite"],
                "subject": record["subject"],
                "candidateVersion": (record.get("candidateIdentity") or {}).get("contentHash")
                or context["implementations"]["candidate"],
                "status": record["status"],
                "at": record["at"],
                "endedAt": record.get("endedAt"),
                "score": score,
                "partialEpisodes": [public_episode(value) for value in record.get("episodes", [])],
                "attemptedEpisodes": record.get("attemptedEpisodes"),
                "reportedDecision": result["decision"]
                if result and result["decision"] != "reported"
                else None,
                "comparison": comparison,
                "deployment": {"status": deployment.status, "approvalId": deployment.approvalId}
                if deployment
                else {"status": "not_requested"},
                "hypotheses": hypotheses,
                "recordings": result.get("recordings", [])
                if result
                else [
                    value["recordingId"] for value in record.get("episodes", []) if value.get("recordingId")
                ],
                "errorCode": record.get("error", {}).get("code"),
                "knownJobGpuHours": known_gpu,
                "costCoverage": "Known Job high-water at this receipt; "
                "evaluator GPU and LLM usage are not metered here",
            }
        ).model_dump(mode="json", by_alias=True)

    def observe(self, record):
        if not self.started or record.get("split") != "train" or record["status"] not in TERMINAL:
            return
        if record.get("notebookContext") is None:
            return  # Never infer a historical Plan from a title, Task name or score.
        try:
            with self._lock:
                identity = "entry-" + record["evaluationId"].removeprefix("eval-")
                if identity not in self.store.entries:
                    self.store.add(self._entry(record, record.get("notebookExecution")))
                else:
                    self.store.publish(identity)
        except (OSError, ValueError, KeyError, LibraryError):
            self.error = (
                True  # Evaluator truth/usage stay committed; public reads retry or report unavailable.
            )

    def reconcile(self):
        with self._lock:
            self.error = False
            for record in tuple(self.manager.evaluations.store.records.values()):
                self.observe(record)
            if self.error:
                raise PlanError("notebook_unavailable", "Lab Notebook publication is unavailable", status=503)

    def goals(self):
        records, _ = self.manager.training_plans.snapshot()
        return sorted({goal_id(record.plan.model_dump(mode="json")) for record in records})

    def notebook(self, goal, *, refuted=False):
        self.reconcile()
        if goal not in self.goals():
            raise PlanError("unknown_goal", "Unknown improvement Goal", status=404)
        return {
            "goalId": goal,
            "entries": [self.public_entry(entry) for entry in self.store.list(goal, refuted=refuted)],
        }

    def public_entry(self, entry):
        """Current comparison evidence is a read projection, separate from historical decisions/audits."""
        result = deepcopy(entry)
        record = self.manager.evaluations.store.records.get(entry["evaluationId"], {})
        if record.get("notebookContext") is None:
            current = entry["comparison"] | {
                "state": "unknown",
                "reason": "Complete comparison evidence is unavailable",
                "conditionsGroup": entry["evaluationId"],
                "remainingGapReduction": None,
                "gapReason": "not_comparable",
            }
        else:
            current = self._comparison(
                record,
                record.get("result") if record.get("status") == "completed" else None,
                record.get("notebookExecution"),
            )
        if current["state"] not in ("baseline", "comparable"):
            current.update(referenceEvaluationId=None, referenceScore=None)
        if current != entry["comparison"]:
            result["recordedComparison"] = deepcopy(entry["comparison"])
            result["comparison"] = current
        return result

    def loop(self, goal):
        entries = self.notebook(goal)["entries"]
        plans = []
        rounds = {}
        for entry in entries:
            rounds.setdefault(entry["plan"]["id"], len(rounds) + 1)
            plans.append({"round": rounds[entry["plan"]["id"]], **entry})
        latest = entries[-1] if entries else None
        original = self.manager.evaluations.store.records.get(latest["evaluationId"], {}) if latest else {}
        scene = original.get("notebookContext", {}).get("comparisonInputs", {}).get("scene")
        return {
            "goalId": goal,
            "rounds": plans,
            "budget": self.manager.loop_budgets.summary(goal),
            "reference": latest["comparison"] if latest else None,
            "candidate": {
                "evaluationId": latest["evaluationId"],
                "subject": latest["subject"],
                "version": latest["candidateVersion"],
                "score": latest["score"],
                "reportedDecision": latest["reportedDecision"],
            }
            if latest
            else None,
            "deployment": latest["deployment"] if latest else {"status": "not_requested"},
            "defaultProgram": {
                "program": latest["subject"],
                "scene": scene,
                "evaluationId": latest["evaluationId"],
            }
            if latest
            else None,
        }

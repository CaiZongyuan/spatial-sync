"""Goal budgets project existing durable Plan, Approval, Job and Evaluation facts.

There is no second usage ledger. A Plan ID owns one pool across its revisions.
"""

import hashlib
import json
import re
import time
from collections import defaultdict
from copy import deepcopy

from packages.info.plans import PlanError
from packages.library.common import LibraryError
from packages.training.schema import LoopBudget


def goal_id(plan):
    return plan.get("goalId") or "goal/" + plan["task"].split("/", 1)[1].rsplit("@", 1)[0]


def plan_budget(plan):
    if plan.get("layer") == "agent":
        return {name: plan["budget"][name] for name in ("caseRuns", "llmUsd")}
    return {
        "gpuHours": plan["budget"]["gpuHours"],
        "episodes": plan["budget"].get("episodes") or plan["evaluation"]["episodes"],
    }


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class LoopBudgets:
    def __init__(self, manager, *, clock=time.time):
        self.manager, self.clock = manager, clock

    def _records(self, goal):
        records, current = self.manager.training_plans.snapshot()
        return [record for record in records if goal_id(record.plan.model_dump(mode="json")) == goal], current

    def _approval(self, record):
        return self.manager.approvals.records.get(record.approvalId)

    def _ever_approved(self, record):
        approval = self._approval(record)
        return (
            [decision.atMs / 1000 for decision in approval.decisions if decision.decision == "approve"]
            if approval
            else []
        )

    def _evaluation_records(self):
        service = self.manager.evaluations
        if service is None:
            return ()
        pending = tuple(getattr(service, "_admitting", {}).values())
        saved = tuple(service.store.records.values())
        return tuple({record["evaluationId"]: record for record in (*pending, *saved)}.values())

    def _legacy_limit(self, record):
        """Use only the first approval's own immutable Task, never a later declaration."""
        approval = self._approval(record)
        tasks = getattr(self.manager, "tasks", None)
        if (record.plan.layer == "agent" or tasks is None
                or approval.details.get("plan", {}).get("task") != record.plan.task):
            return None, None
        try:
            task = tasks.show(record.plan.task)  # TaskLibrary verifies immutable package bytes/hash.
            if task["id"] != record.plan.task or not re.fullmatch(r"[a-f0-9]{64}", task["contentHash"]):
                return None, None
            original_hashes = {
                job["taskContentHash"]
                for job in tuple(self.manager.jobs.records.values())
                if job["planId"] == record.plan.id
                and job["planRevision"] == record.plan.revision
                and job.get("taskContentHash") is not None
            }
            if original_hashes and original_hashes != {task["contentHash"]}:
                return None, None
            limit = LoopBudget.model_validate(task["task"]["loopBudget"]).model_dump(mode="json")
            return limit, {"task": record.plan.task, "taskContentHash": task["contentHash"]}
        except (LibraryError, OSError, ValueError, KeyError):
            return None, None

    def _held_job(self, job):
        if job["status"] in ("queued", "running"):
            return True
        return any(
            lease["jobId"] == job["jobId"] and lease["leaseId"] == job["leaseId"]
            for node in tuple(self.manager.jobs.nodes.values())
            for lease in tuple(node["leases"].values())
        )

    def _held_evaluation(self, record):
        service = self.manager.evaluations
        return (
            record["status"] in ("queued", "running")
            or service
            and record["evaluationId"] in service._blocked
        )

    def _pools(self, records, current_plans, proposed=None, *, reserve_proposed=True):
        grouped = defaultdict(list)
        for record in records:
            grouped[record.plan.id].append(record)
        if proposed is not None:
            grouped.setdefault(proposed["id"], [])
        jobs = tuple(self.manager.jobs.records.values())
        evaluations = self._evaluation_records()
        pools = []
        for identity, versions in grouped.items():
            selected = proposed if proposed is not None and proposed["id"] == identity else (
                versions[-1].plan.model_dump(mode="json") if versions else {}
            )
            if selected.get("layer") == "agent":
                pools.append(self._agent_pool(identity, versions, current_plans.get(identity),
                                               proposed if reserve_proposed else None))
                continue
            matching_jobs = [job for job in jobs if job["planId"] == identity]
            matching_evaluations = [value for value in evaluations if value.get("planId") == identity]
            actual_gpu = sum(job["chargedGpuHours"] for job in matching_jobs)
            actual_episodes = sum(
                value.get("attemptedEpisodes", len(value.get("episodes", [])))
                for value in matching_evaluations
            )
            held_jobs = [job for job in matching_jobs if self._held_job(job)]
            held_evaluations = [value for value in matching_evaluations if self._held_evaluation(value)]
            held_revisions = {job["planRevision"] for job in held_jobs} | {
                value["planRevision"] for value in held_evaluations
            }
            current = current_plans.get(identity)
            rights = []
            for version in versions:
                approval = self._approval(version)
                is_current = version is current and (proposed is None or proposed["id"] != identity)
                if (
                    version.plan.revision in held_revisions
                    or is_current
                    and approval
                    and approval.status == "approved"
                ):
                    rights.append(plan_budget(version.plan.model_dump(mode="json")))
            if reserve_proposed and proposed is not None and proposed["id"] == identity:
                rights.append(plan_budget(proposed))
            # allowedGpuHours is derived from shared remaining capacity, not an
            # independent reservation or enforced running limit. Use admitted estimates.
            job_capacity = actual_gpu + sum(
                max(
                    0,
                    (job.get("estimatedGpuHours") or 0) - job["chargedGpuHours"],
                )
                for job in held_jobs
                if job.get("budgetReserved") or self.manager.jobs._has_lease(job)
            )
            evaluation_capacity = actual_episodes + sum(
                max(
                    0,
                    value.get("allocatedEpisodes", 0)
                    - value.get("attemptedEpisodes", len(value.get("episodes", []))),
                )
                for value in held_evaluations
            )
            effective_gpu = max(actual_gpu, job_capacity, *(value["gpuHours"] for value in rights))
            effective_episodes = max(
                actual_episodes, evaluation_capacity, *(value["episodes"] for value in rights)
            )
            pools.append(
                {
                    "planId": identity,
                    "actual": {"gpuHours": actual_gpu, "episodes": actual_episodes},
                    "effective": {"gpuHours": effective_gpu, "episodes": effective_episodes},
                    "unspentCommitment": {
                        "gpuHours": effective_gpu - actual_gpu,
                        "episodes": effective_episodes - actual_episodes,
                    },
                    "heldJobs": [job["jobId"] for job in held_jobs],
                }
            )
        return pools

    def _agent_pool(self, identity, versions, current, proposed):
        service = getattr(self.manager, "agent_runs", None)
        runs = ([run for run in tuple(service.records.values()) if run["planId"] == identity]
                if service else [])
        held = [run for run in runs if run["status"] in ("queued", "running", "cancelling")
                or run.get("cleanupIncomplete")]
        held_revisions = {run["planRevision"] for run in held}
        rights = [plan_budget(record.plan.model_dump(mode="json")) for record in versions
                  if record.plan.revision in held_revisions or (
                      record is current and (proposed is None or proposed["id"] != identity)
                      and self._approval(record) and self._approval(record).status == "approved")]
        if proposed is not None and proposed["id"] == identity:
            rights.append(plan_budget(proposed))
        case_runs = sum(run.get("attemptedCaseRuns", 0) for run in runs)
        money = sum(run.get("usage", {}).get("knownLlmUsd", 0) for run in runs)
        unknown = any(run.get("usage", {}).get("llmUsd") is None
                      and run.get("usage", {}).get("unknownUsageRequests", 0) for run in runs)
        # Provider snapshot reserves only the unspent suffix of an uncertain call.
        in_flight = sum(run.get("usage", {}).get("reservedUsd", 0) for run in runs)
        future_money = sum(max(0, run["allocatedLlmUsd"] - run.get("usage", {}).get("knownLlmUsd", 0)
                               - run.get("usage", {}).get("reservedUsd", 0)) for run in held)
        future_cases = sum(max(0, run["allocatedCaseRuns"] - run.get("attemptedCaseRuns", 0)) for run in held)
        effective = {"caseRuns": max([case_runs + future_cases, *(value["caseRuns"] for value in rights)]),
                     "llmUsd": max([money + in_flight + future_money,
                                    *(value["llmUsd"] for value in rights)])}
        return {"planId": identity, "actual": {"caseRuns": case_runs, "llmUsd": None if unknown else money},
                "knownActual": {"caseRuns": case_runs, "llmUsd": money}, "effective": effective,
                "unspentCommitment": {"caseRuns": effective["caseRuns"] - case_runs,
                                      "llmUsd": effective["llmUsd"] - money},
                "heldJobs": [run["jobId"] for run in held]}

    def summary(self, goal, *, proposed=None, declared=None, reserve_proposed=True):
        self.manager.jobs._require_available()
        records, current_plans = self._records(goal)
        approved = [(at, record) for record in records for at in self._ever_approved(record)]
        by_approval = {record.approvalId: record for _, record in approved}
        try:
            ordered = self.manager.approvals.first_decision(by_approval, "approve")
        except (OSError, ValueError, KeyError) as error:
            raise PlanError("loop_budget_unavailable", "Approval audit is unavailable", status=503) from error
        authority = (ordered[1].atMs / 1000, by_approval[ordered[0]]) if ordered is not None else None
        legacy_evidence = None
        if authority is None and approved:
            limit, limit_source = None, "unknown"
        elif authority is None:
            agent_source = proposed is not None and proposed.get("layer") == "agent"
            limit, limit_source = deepcopy(declared), (
                "proposed-agent-plan" if agent_source else "proposed-task-snapshot"
            ) if declared else "unknown"
        elif authority[1].goalContext is not None:
            limit, limit_source = (
                deepcopy(authority[1].goalContext["declaredBudget"]),
                "approved-agent-plan"
                if authority[1].goalContext.get("sourceSnapshot", {}).get("kind") == "agent-plan"
                else "approved-task-snapshot",
            )
        else:
            limit, legacy_evidence = self._legacy_limit(authority[1])
            limit_source = "legacy-immutable-task" if limit is not None else "unknown"
        first_at = authority[0] if authority is not None else None
        deadline = first_at + limit["days"] * 86400 if first_at is not None and limit else None
        expired = deadline is not None and self.clock() >= deadline
        pools = self._pools(records, current_plans, proposed, reserve_proposed=reserve_proposed)
        names = [name for name in ("gpuHours", "episodes", "caseRuns", "llmUsd")
                 if any(name in pool["actual"] for pool in pools)] if pools else ["gpuHours", "episodes"]
        known = {name: sum(pool.get("knownActual", pool["actual"]).get(name, 0) for pool in pools)
                 for name in names}
        actual = {name: None if any(pool["actual"].get(name, 0) is None for pool in pools) else known[name]
                  for name in names}
        effective = {name: sum(pool["effective"].get(name, 0) for pool in pools) for name in actual}
        unspent = {name: effective[name] - known[name] for name in actual}
        exhausted = []
        if limit:
            for name in names:
                bound = limit.get(name)
                if bound is not None and (effective[name] > bound or bound > 0 and effective[name] >= bound):
                    exhausted.append(name)
            if expired:
                exhausted.append("days")
        basis = {
            "goalId": goal,
            "limit": limit,
            "effective": effective,
            "authority": None
            if authority is None
            else {
                "planId": authority[1].plan.id,
                "revision": authority[1].plan.revision,
            },
            "firstApprovedAt": first_at,
            "deadline": deadline,
            "expired": expired,
            "legacyEvidence": legacy_evidence,
        }
        return {
            "goalId": goal,
            "limit": limit,
            "limitSource": limit_source,
            "authority": basis["authority"],
            "limitEvidence": legacy_evidence,
            "limitReason": "approval_order_unavailable"
            if approved and authority is None
            else "first_approval_budget_evidence_unavailable"
            if authority and limit is None
            else None,
            "actual": actual,
            "unspentCommitment": unspent,
            "effective": effective,
            "remaining": {name: max(0, limit[name] - effective[name])
                          if limit and limit.get(name) is not None and actual[name] is not None else None
                          for name in actual},
            "firstApprovedAt": first_at,
            "deadline": deadline,
            "elapsedDays": max(0, (self.clock() - first_at) / 86400) if first_at is not None else 0,
            "exhausted": bool(exhausted),
            "exhaustedDimensions": exhausted,
            "pools": pools,
            "basis": digest(basis),
            "coverage": {
                "gpuHours": "Job charged high-water counters; other GPU usage is not metered here",
                "episodes": "Explicitly linked admitted evaluation attempts, including invalid attempts",
                "llmUsd": "Managed Agent provider observations and unspent reservations"
                if "llmUsd" in names else "unavailable: no managed Agent usage is linked to this Goal",
            },
        }

    def prepare(self, plan, task):
        goal = goal_id(plan)
        plan["goalId"] = goal
        current = self.manager.training_plans.records.get(plan["id"])
        if current is not None and goal_id(current.plan.model_dump(mode="json")) != goal:
            raise PlanError(
                "goal_changed", "A Plan revision cannot move actual usage to another Goal", status=422
            )
        agent = task.get("kind") == "agent-plan"
        declared = deepcopy(task["humanDeclaredLimit"] if agent else task["task"]["loopBudget"])
        review = self.summary(goal, proposed=plan, declared=declared)
        before = self.summary(goal, proposed=plan, declared=declared, reserve_proposed=False)
        review.update(exhaustedBefore=before["exhausted"], remainingBefore=before["remaining"])
        return {
            "version": 1,
            "goalId": goal,
            **({"sourceSnapshot": deepcopy(task)} if agent else {"taskContentHash": task["contentHash"]}),
            "declaredBudget": declared,
            "review": review,
        }

    def require_fresh_approval(self, record):
        if record.goalContext is None:
            return  # Old approvals keep their existing semantics; no invented historical authority.
        plan = record.plan.model_dump(mode="json")
        current = self.summary(
            record.goalContext["goalId"], proposed=plan, declared=record.goalContext["declaredBudget"]
        )
        if current["basis"] != record.goalContext["review"]["basis"]:
            raise PlanError(
                "loop_budget_changed", "Goal budget changed; submit a fresh Plan revision", status=409
            )

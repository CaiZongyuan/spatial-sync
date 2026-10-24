"""Goal budgets project existing durable Plan, Approval, Job and Evaluation facts.

There is no second usage ledger. A Plan ID owns one pool across its revisions.
"""

import hashlib
import json
import time
from collections import defaultdict
from copy import deepcopy

from packages.info.plans import PlanError


def goal_id(plan):
    return plan.get("goalId") or "goal/" + plan["task"].split("/", 1)[1].rsplit("@", 1)[0]


def plan_budget(plan):
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
        return [
            record
            for versions in self.manager.training_plans.history.values()
            for record in versions.values()
            if goal_id(record.plan.model_dump(mode="json")) == goal
        ]

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
        return tuple(service.store.records.values()) if service else ()

    def _held_job(self, job):
        if job["status"] in ("queued", "running"):
            return True
        return any(
            lease["jobId"] == job["jobId"] and lease["leaseId"] == job["leaseId"]
            for node in self.manager.jobs.nodes.values()
            for lease in node["leases"].values()
        )

    def _held_evaluation(self, record):
        service = self.manager.evaluations
        return (
            record["status"] in ("queued", "running")
            or service
            and record["evaluationId"] in service._blocked
        )

    def _pools(self, records, proposed=None, *, reserve_proposed=True):
        grouped = defaultdict(list)
        for record in records:
            grouped[record.plan.id].append(record)
        if proposed is not None:
            grouped.setdefault(proposed["id"], [])
        jobs = tuple(self.manager.jobs.records.values())
        evaluations = self._evaluation_records()
        pools = []
        for identity, versions in grouped.items():
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
            current = self.manager.training_plans.records.get(identity)
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
            # Existing Jobs use a shared Plan-ID pool. Estimates/explicit overbudget
            # grants can retain more capacity, but an old full Plan is never added twice.
            job_capacity = actual_gpu + sum(
                max(
                    0,
                    (job.get("allowedGpuHours") or job.get("estimatedGpuHours") or 0)
                    - job["chargedGpuHours"],
                )
                for job in held_jobs
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

    def summary(self, goal, *, proposed=None, declared=None, reserve_proposed=True):
        self.manager.jobs._require_available()
        records = self._records(goal)
        approved = [(at, record) for record in records for at in self._ever_approved(record)]
        authorities = [(at, record) for at, record in approved if record.goalContext is not None]
        authority = min(authorities, key=lambda item: (item[0], item[1].plan.id)) if authorities else None
        limit = deepcopy(authority[1].goalContext["declaredBudget"] if authority else declared)
        first_at = min((at for at, _ in approved), default=None)
        deadline = first_at + limit["days"] * 86400 if first_at is not None and limit else None
        expired = deadline is not None and self.clock() >= deadline
        pools = self._pools(records, proposed, reserve_proposed=reserve_proposed)
        actual = {name: sum(pool["actual"][name] for pool in pools) for name in ("gpuHours", "episodes")}
        effective = {name: sum(pool["effective"][name] for pool in pools) for name in actual}
        unspent = {name: effective[name] - actual[name] for name in actual}
        exhausted = []
        if limit:
            if effective["gpuHours"] > limit["gpuHours"] or (
                limit["gpuHours"] > 0 and effective["gpuHours"] >= limit["gpuHours"]
            ):
                exhausted.append("gpuHours")
            if effective["episodes"] >= limit["episodes"]:
                exhausted.append("episodes")
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
        }
        return {
            "goalId": goal,
            "limit": limit,
            "limitSource": "approved-task-snapshot"
            if authority
            else "proposed-task-snapshot"
            if limit
            else "unknown",
            "actual": actual,
            "unspentCommitment": unspent,
            "effective": effective,
            "remaining": {name: max(0, limit[name] - effective[name]) if limit else None for name in actual},
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
                "llmUsd": "unavailable: managed Agent execution is outside this issue",
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
        declared = deepcopy(task["task"]["loopBudget"])
        review = self.summary(goal, proposed=plan, declared=declared)
        before = self.summary(goal, proposed=plan, declared=declared, reserve_proposed=False)
        review.update(exhaustedBefore=before["exhausted"], remainingBefore=before["remaining"])
        return {
            "version": 1,
            "goalId": goal,
            "taskContentHash": task["contentHash"],
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

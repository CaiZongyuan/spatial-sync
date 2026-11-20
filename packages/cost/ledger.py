"""GPU entries commit atomically with their Job's charged high-water counter."""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from datetime import datetime

from packages.cost.ranges import overlap_fraction, reporting_window
from packages.info.plans import PlanError
from packages.library.transactions import durable_json
from packages.models import ModelResources
from packages.runtime_records.costs import aggregate_imported, observed_at

LLM_FIELDS = (
    "requests",
    "failures",
    "inputTokens",
    "outputTokens",
    "cacheReadTokens",
    "cacheWriteTokens",
    "tokens",
    "llmUsd",
    "successRate",
    "cacheHitRate",
)
DIMENSIONS = {"agent", "model", "node", "goal", "plan", "job"}


def report_signature(report):
    return hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()


class CostLedger:
    def __init__(self, manager):
        self.manager = manager
        self.resources = ModelResources(manager.settings.state_root)
        self.read_path = manager.settings.state_root / "cost-reads.json"

    def prepare(self, jobs):
        for job in jobs.values():
            if "costLedger" in job:
                continue
            charged = job["chargedGpuHours"] * 3600
            at = job["finishedAt"] or job["startedAt"] or job["createdAt"]
            report = job.get("lastNodeReport") or {}
            reported = report.get("elapsedSeconds", 0) if job["executionDevice"] == "cuda" else 0
            job["costLedger"] = {
                "version": 1,
                "throughSeconds": charged,
                "lastObservedAt": at,
                "reportedSeconds": reported,
                "memorySamples": [],
                "memoryReport": report_signature(report),
                "entries": [],
            }
            attempts = job.get("attempts", [])
            if attempts:
                latest = attempts[-1]
                job["costLedger"].update(
                    leaseId=latest["leaseId"], attemptThroughSeconds=latest["gpuSeconds"],
                )
            if charged:
                job["costLedger"]["entries"].append(
                    {
                        "startAt": job["startedAt"] or job["createdAt"],
                        "endAt": at,
                        "gpuSeconds": charged,
                        "throughSeconds": charged,
                        "source": "historical-counter",
                        "priceRevision": None,
                        "usdPerHour": None,
                        "priceReason": "Price at historical usage was not retained",
                    }
                )

    def capture(self, jobs, now):
        self.prepare(jobs)
        prices = None
        for job in jobs.values():
            state = job["costLedger"]
            attempts = job.get("attempts", [])
            attempt = attempts[-1] if attempts else None
            node_id = attempt["nodeId"] if attempt else job["nodeId"]
            if not attempt and job["leaseId"] and job["leaseId"] != state.get("leaseId"):
                node = self.manager.jobs.nodes.get(job["nodeId"], {})
                lease = node.get("leases", {}).get(job["resourceId"])
                if lease and lease["leaseId"] == job["leaseId"]:
                    # Queue time is known not to own this GPU.
                    state["lastObservedAt"] = max(state["lastObservedAt"], lease["assignedAt"])
                    state["leaseId"] = job["leaseId"]
            report = job.get("lastNodeReport") or {}
            if job["executionDevice"] == "cuda":
                state["reportedSeconds"] = max(state["reportedSeconds"], report.get("elapsedSeconds", 0))
                memory = report.get("memoryUsedMb")
                signature = report_signature(report)
                if memory is not None and state.get("memoryReport") != signature:
                    state["memorySamples"].append({"at": now, "memoryUsedMb": memory, "nodeId": node_id})
                    state["memoryReport"] = signature
            pending = [(attempt, now)]
            if attempt and attempt["leaseId"] != state.get("leaseId"):
                previous = next(
                    (row for row in reversed(attempts) if row["leaseId"] == state.get("leaseId")), None,
                )
                if previous is not None:
                    # Reconciliation can accrue the prior lease's timeout and assign
                    # the next lease before either change reaches this transaction.
                    pending.insert(0, (previous, previous["finishedAt"]))

            for charged_attempt, at in pending:
                if charged_attempt:
                    if charged_attempt["leaseId"] != state.get("leaseId"):
                        state.update(
                            leaseId=charged_attempt["leaseId"], attemptThroughSeconds=0.0,
                            lastObservedAt=max(state["lastObservedAt"], charged_attempt["startedAt"]),
                        )
                    attempt_seconds = charged_attempt["gpuSeconds"]
                    delta = attempt_seconds - state.get("attemptThroughSeconds", 0.0)
                    reported_seconds = charged_attempt["reportedGpuSeconds"]
                    charged_node = charged_attempt["nodeId"]
                else:
                    attempt_seconds = job["chargedGpuHours"] * 3600
                    delta = attempt_seconds - state["throughSeconds"]
                    reported_seconds = state["reportedSeconds"]
                    charged_node = node_id
                if delta <= 0:
                    continue
                if prices is None:
                    try:
                        prices = self.resources.prices()
                    except (OSError, ValueError):
                        prices = {"revision": None, "gpus": [], "unavailable": True}
                rate = next(
                    (row["usdPerHour"] for row in prices["gpus"] if row["nodeId"] == charged_node), None,
                )
                through_seconds = state["throughSeconds"] + delta
                entry = {
                    "nodeId": charged_node,
                    "startAt": min(at, state["lastObservedAt"]),
                    "endAt": at,
                    "gpuSeconds": delta,
                    "throughSeconds": through_seconds,
                    "source": "lease-timeout" if attempt_seconds > reported_seconds else "node-report",
                    "priceRevision": prices["revision"],
                    "usdPerHour": rate,
                    "priceReason": (
                        "Price table is unavailable"
                        if prices.get("unavailable")
                        else "GPU price is not configured"
                    )
                    if rate is None
                    else None,
                }
                if charged_attempt:
                    entry.update(leaseId=charged_attempt["leaseId"], attemptThroughSeconds=attempt_seconds)
                    state["attemptThroughSeconds"] = attempt_seconds
                state["entries"].append(entry)
                state.update(throughSeconds=through_seconds, lastObservedAt=at)

    @staticmethod
    def attribution(job):
        if job.get("source") == "runtime-import":
            refs = job["associations"]
            return {
                "agent": refs.get("agentId"),
                "model": job["models"][0] if len(job["models"]) == 1 else None,
                "node": None,
                "goal": refs.get("goalId"),
                "plan": f"{refs['planId']}@{refs['planRevision']}" if refs.get("planId") else None,
                "job": None,
            }
        actor = job["requestedBy"]
        return {
            "agent": actor["name"] if actor["kind"] == "agent" else None,
            "model": None,
            "node": job["nodeId"],
            "goal": job["plan"].get("goalId") or job["plan"].get("goal"),
            "plan": f"{job['planId']}@{job['planRevision']}",
            "job": job["jobId"],
        }

    @staticmethod
    def _nodes(job):
        if job.get("source") == "runtime-import":
            return {None}
        attempts = job.get("attempts", [])
        return {row["nodeId"] for row in attempts} if attempts else {job["nodeId"]}

    def _jobs(self, filters):
        self.manager.jobs._require_available()
        return [
            job
            for job in self._all_items()
            if all(
                value in self._nodes(job) if key == "node" else self.attribution(job)[key] == value
                for key, value in filters.items() if value is not None
            )
        ]

    def _all_items(self):
        runtime = getattr(self.manager, "runtime_records", None)
        imported = []
        for record in runtime.records.values() if runtime else ():
            rsi = getattr(self.manager, "agent_rsi", None)
            execution = rsi.native_associations(record["id"]) if rsi is not None else {}
            imported.append(
                record
                | {
                    "associations": record["associations"] | execution,
                    **({"visibility": "private"} if runtime.is_private(record) else {}),
                }
            )
        return [*self.manager.jobs.records.values(), *imported]

    def window(self, key, *, start=None, end=None):
        earliest = min(
            (
                observed_at(item) if item.get("source") == "runtime-import" else item["createdAt"]
                for item in self._all_items()
            ),
            default=None,
        )
        return reporting_window(key, self.manager.jobs.clock(), start=start, end=end, earliest=earliest)

    def aggregate(self, jobs, window, *, include_end=True, node=None):
        seconds = reported = historical = unpriced = known_usd = 0.0
        peak, revisions, warnings = None, set(), set()
        for job in jobs:
            if job.get("source") == "runtime-import":
                continue
            state = job["costLedger"]
            attempts = {row["leaseId"]: row for row in job.get("attempts", [])}
            for entry in state["entries"]:
                if node is not None and entry.get("nodeId", job["nodeId"]) != node:
                    continue
                fraction = overlap_fraction(entry, window.start, window.end, include_end=include_end)
                if not fraction:
                    continue
                delta = entry["gpuSeconds"] * fraction
                seconds += delta
                # Later accepted reports can confirm a conservative timeout prefix without recharging it.
                attempt = attempts.get(entry.get("leaseId"))
                reported_seconds = attempt["reportedGpuSeconds"] if attempt else state["reportedSeconds"]
                through_seconds = entry.get("attemptThroughSeconds", entry["throughSeconds"])
                confirmed = min(entry["gpuSeconds"],
                                max(0, reported_seconds - (through_seconds - entry["gpuSeconds"])))
                reported += confirmed * fraction
                if entry["source"] == "historical-counter":
                    historical += delta
                if entry["usdPerHour"] is None:
                    unpriced += delta
                    warnings.add(entry["priceReason"])
                else:
                    known_usd += delta / 3600 * entry["usdPerHour"]
                if entry["priceRevision"] is not None:
                    revisions.add(entry["priceRevision"])
            for sample in state["memorySamples"]:
                if (window.start <= sample["at"] <= window.end
                        and (node is None or sample.get("nodeId", job["nodeId"]) == node)):
                    peak = max(peak or 0, sample["memoryUsedMb"])
        result = {
            **dict.fromkeys(LLM_FIELDS),
            "gpuSeconds": seconds,
            "gpuHours": seconds / 3600,
            "reportedGpuSeconds": reported,
            "estimatedGpuSeconds": max(0, seconds - reported),
            "historicalGpuSeconds": historical,
            "memoryPeakMb": peak,
            "gpuUsd": None if unpriced else known_usd,
            "knownGpuUsd": known_usd,
            "unpricedGpuSeconds": unpriced,
            "priceRevisions": sorted(revisions),
            "priceWarnings": sorted(warnings),
            "runtimeSessionCount": 0,
        }
        imported = [job for job in jobs if job.get("source") == "runtime-import"]
        if imported:
            result.update(aggregate_imported(imported, window, include_end))
        return result

    def summary(self, window, filters):
        jobs = self._jobs(filters)
        choices = {
            dimension: sorted(
                {
                    self.attribution(job)[dimension]
                    for job in self._all_items()
                    if self.attribution(job)[dimension] is not None
                }
            )
            for dimension in DIMENSIONS
        }
        choices["node"] = sorted({node for job in self._all_items() for node in self._nodes(job) if node})
        return {
            "version": 1,
            "window": window.document(),
            "filters": filters,
            "totals": self.aggregate(jobs, window, node=filters.get("node")),
            "cards": {
                key: self.aggregate(jobs, self.window(key), node=filters.get("node"))
                for key in ("today", "7d", "all")
            },
            "choices": choices,
            "availability": {
                "llm": bool(
                    getattr(self.manager, "runtime_records", None) and self.manager.runtime_records.records
                ),
                "reason": "Imported runtime reports only; missing counters remain unknown"
                if getattr(self.manager, "runtime_records", None) and self.manager.runtime_records.records
                else "No runtime usage has been imported; no requests or tokens are inferred",
            },
            "pricingPolicy": (
                "Each GPU increment retains the price revision observed when it was committed; "
                "historical entries remain unpriced"
            ),
        }

    def series(self, window, filters):
        jobs = self._jobs(filters)
        return {
            "window": window.document(),
            "buckets": [
                {
                    "start": start,
                    "end": end,
                    **self.aggregate(
                        jobs,
                        type(window)(window.key, start, end, window.bucket_seconds),
                        include_end=end == window.end, node=filters.get("node"),
                    ),
                }
                for start, end in window.buckets()
            ],
        }

    def breakdown(self, window, filters, by):
        if by not in DIMENSIONS:
            raise PlanError("invalid_cost_dimension", "Use agent, model, node, goal, plan or job", status=422)
        groups = defaultdict(list)
        selected_node = filters.get("node")
        for job in self._jobs(filters):
            keys = (self._nodes(job) if not selected_node else {selected_node}) if by == "node" else (
                self.attribution(job)[by],
            )
            for key in keys:
                groups[key].append(job)
        rows = []
        for key, jobs in groups.items():
            group_node = key if by == "node" else selected_node
            values = self.aggregate(jobs, window, node=group_node)
            rows.append(
                {
                    "key": key,
                    "label": key or "未关联",
                    **values,
                    "jobs": [
                        {
                            "jobId": job["jobId"],
                            "nodeId": group_node or job["nodeId"],
                            "status": job["status"],
                            **self.aggregate([job], window, node=group_node),
                        }
                        for job in jobs
                        if job.get("source") != "runtime-import"
                    ],
                    "runtimeSessions": [
                        {
                            "id": record["id"],
                            "runtime": record["runtime"],
                            "status": record["status"],
                            **self.aggregate([record], window),
                        }
                        for record in jobs
                        if record.get("source") == "runtime-import" and record.get("visibility") != "private"
                    ],
                    "restrictedRuntimeSessionCount": sum(
                        record.get("visibility") == "private" for record in jobs
                    ),
                    "runtimeDetailsCoverage": "Totals include restricted native usage; "
                    "private capture details are hidden",
                }
            )
        total = sum(row["gpuSeconds"] for row in rows)
        return {
            "window": window.document(),
            "by": by,
            "rows": [row | {"gpuShare": row["gpuSeconds"] / total if total else None} for row in rows],
        }

    def _reads(self):
        return json.loads(self.read_path.read_text(encoding="utf-8")) if self.read_path.exists() else {}

    def anomalies(self):
        self.manager.jobs._require_available()
        reads, rows = self._reads(), []
        for job in self.manager.jobs.records.values():
            if job["status"] != "failed":
                continue
            rows.append(
                {
                    "id": f"job:{job['jobId']}",
                    "kind": "job",
                    "severity": "error",
                    "title": "训练作业失败",
                    "detail": job["reason"] or "Node reported failure",
                    "at": job["finishedAt"],
                    "jobId": job["jobId"],
                    "plan": f"{job['planId']}@{job['planRevision']}",
                }
            )
        service = self.manager.evaluations
        for evaluation in service.store.records.values() if service else ():
            code = (evaluation.get("error") or {}).get("code", "")
            if evaluation["split"] == "train" and evaluation["status"] == "failed" and "timeout" in code:
                timestamp = evaluation.get("endedAt") or evaluation["at"]
                rows.append(
                    {
                        "id": f"evaluation:{evaluation['evaluationId']}",
                        "kind": "evaluation",
                        "severity": "warning",
                        "title": "评估超时",
                        "detail": code,
                        "at": datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp(),
                        "evaluationId": evaluation["evaluationId"],
                    }
                )
        rows.sort(key=lambda row: (row["at"] or 0, row["id"]), reverse=True)
        return {
            "anomalies": [
                row | {"read": row["id"] in reads, "readReceipt": reads.get(row["id"])} for row in rows
            ],
            "unreadCount": sum(row["id"] not in reads for row in rows),
        }

    def acknowledge(self, identity, actor):
        if not any(row["id"] == identity for row in self.anomalies()["anomalies"]):
            raise PlanError("unknown_cost_anomaly", "Unknown cost anomaly", status=404)
        reads = self._reads()
        if identity not in reads:
            reads[identity] = {"at": self.manager.jobs.clock(), "actor": actor}
            durable_json(self.read_path, reads)
        return deepcopy(reads[identity])

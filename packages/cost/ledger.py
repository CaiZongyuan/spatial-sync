"""GPU entries commit atomically with their Job's charged high-water counter."""

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from datetime import datetime

from packages.agents.provider import TOKEN_KEYS, usage_snapshot
from packages.cost.ranges import overlap_fraction, reporting_window
from packages.info.plans import PlanError
from packages.library.transactions import durable_json
from packages.models import ModelResources

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
            if job["leaseId"] and job["leaseId"] != state.get("leaseId"):
                node = self.manager.jobs.nodes.get(job["nodeId"], {})
                lease = node.get("leases", {}).get(job["resourceId"])
                if lease and lease["leaseId"] == job["leaseId"]:
                    # Queue time is known not to own this GPU. Only a new
                    # fenced dispatch advances the initial observation bound.
                    state["lastObservedAt"] = max(state["lastObservedAt"], lease["assignedAt"])
                    state["leaseId"] = job["leaseId"]
            report = job.get("lastNodeReport") or {}
            if job["executionDevice"] == "cuda":
                state["reportedSeconds"] = max(state["reportedSeconds"], report.get("elapsedSeconds", 0))
                memory = report.get("memoryUsedMb")
                signature = report_signature(report)
                if memory is not None and state.get("memoryReport") != signature:
                    state["memorySamples"].append({"at": now, "memoryUsedMb": memory})
                    state["memoryReport"] = signature
            charged = job["chargedGpuHours"] * 3600
            if charged <= state["throughSeconds"]:
                continue
            if prices is None:
                try:
                    prices = self.resources.prices()
                except (OSError, ValueError):
                    prices = {"revision": None, "gpus": [], "unavailable": True}
            rate = next((row["usdPerHour"] for row in prices["gpus"] if row["nodeId"] == job["nodeId"]), None)
            state["entries"].append(
                {
                    "startAt": min(now, state["lastObservedAt"]),
                    "endAt": now,
                    "gpuSeconds": charged - state["throughSeconds"],
                    "throughSeconds": charged,
                    "source": "lease-timeout" if charged > state["reportedSeconds"] else "node-report",
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
            )
            state.update(throughSeconds=charged, lastObservedAt=now)

    @staticmethod
    def attribution(job):
        actor = job["requestedBy"]
        return {
            "agent": job["agent"] if job.get("kind") == "agent-run" else (
                actor["name"] if actor["kind"] == "agent" else None
            ),
            "model": job.get("model") if job.get("kind") == "agent-run" else None,
            "node": job["nodeId"],
            "goal": job["plan"].get("goalId") or job["plan"].get("goal"),
            "plan": f"{job['planId']}@{job['planRevision']}",
            "job": job["jobId"],
        }

    def _jobs(self, filters):
        self.manager.jobs._require_available()
        return [
            job
            for job in self._all_jobs()
            if all(self.attribution(job)[key] == value for key, value in filters.items() if value is not None)
        ]

    def _all_jobs(self):
        service = getattr(self.manager, "agent_runs", None)
        if service and getattr(service, "_failure", False):
            raise PlanError("agent_usage_unavailable", "Managed Agent accounting is unavailable", status=503)
        return [*self.manager.jobs.records.values(), *(service.records.values() if service else ())]

    def window(self, key, *, start=None, end=None):
        earliest = min((job["createdAt"] for job in self._all_jobs()), default=None)
        return reporting_window(key, self.manager.jobs.clock(), start=start, end=end, earliest=earliest)

    def aggregate(self, jobs, window, *, include_end=True):
        seconds = reported = historical = unpriced = known_usd = 0.0
        peak, revisions, warnings = None, set(), set()
        for job in jobs:
            if job.get("kind") == "agent-run":
                continue
            state = job["costLedger"]
            for entry in state["entries"]:
                fraction = overlap_fraction(entry, window.start, window.end, include_end=include_end)
                if not fraction:
                    continue
                delta = entry["gpuSeconds"] * fraction
                seconds += delta
                # Later accepted reports can confirm a conservative timeout prefix without recharging it.
                confirmed = min(
                    entry["gpuSeconds"],
                    max(0, state["reportedSeconds"] - (entry["throughSeconds"] - entry["gpuSeconds"])),
                )
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
                if window.start <= sample["at"] <= window.end:
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
        }
        service = getattr(self.manager, "agent_runs", None)
        if service and service.records:
            rows = [row for job in jobs if job.get("kind") == "agent-run"
                    for row in job["provider"]["requests"]
                    if window.start <= row["at"] < window.end or include_end and row["at"] == window.end]
            snapshot = usage_snapshot(rows)
            unknown = snapshot["unknownUsageRequests"] > 0
            token_fields = ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
            known = {name: sum(row["usage"].get(key, 0) for row in rows)
                     for name, key in zip(token_fields, TOKEN_KEYS, strict=True)}
            result.update({name: None if unknown else value for name, value in known.items()})
            result.update({"known" + name[0].upper() + name[1:]: value for name, value in known.items()})
            result.update({name: snapshot[name] for name in ("requests", "failures", "tokens", "llmUsd",
                            "knownRequests", "knownFailures", "knownTokens", "knownLlmUsd",
                            "unknownUsageRequests", "unknownOutcomeRequests", "notForwardedRequests",
                            "reservedRequests")})
            result.update(reservedLlmUsd=snapshot["reservedUsd"], reservedTokens=snapshot["reservedTokens"])
            count, failed = snapshot["requests"], snapshot["failures"]
            result["successRate"] = (count - failed) / count if count and failed is not None else None
            inputs = known["inputTokens"] + known["cacheReadTokens"] + known["cacheWriteTokens"]
            result["cacheHitRate"] = known["cacheReadTokens"] / inputs if inputs and not unknown else None
            result["priceRevisions"] = sorted(revisions | set(snapshot["priceRevisions"]))
        return result

    def summary(self, window, filters):
        jobs = self._jobs(filters)
        choices = {
            dimension: sorted(
                {
                    self.attribution(job)[dimension]
                    for job in self._all_jobs()
                    if self.attribution(job)[dimension] is not None
                }
            )
            for dimension in DIMENSIONS
        }
        return {
            "version": 1,
            "window": window.document(),
            "filters": filters,
            "totals": self.aggregate(jobs, window),
            "cards": {key: self.aggregate(jobs, self.window(key)) for key in ("today", "7d", "all")},
            "choices": choices,
            "availability": {
                "llm": bool(getattr(self.manager, "agent_runs", None) and self.manager.agent_runs.records),
                "reason": "Managed Agent provider observations; interactive client usage is not inferred"
                if getattr(self.manager, "agent_runs", None) and self.manager.agent_runs.records
                else "No managed Agent usage has been recorded; no requests or tokens are inferred",
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
                        include_end=end == window.end,
                    ),
                    "agents": [{"agent": agent, **self.aggregate(
                        [job for job in jobs if job.get("agent") == agent],
                        type(window)(window.key, start, end, window.bucket_seconds),
                        include_end=end == window.end,
                    )} for agent in sorted({job["agent"] for job in jobs if job.get("kind") == "agent-run"})],
                }
                for start, end in window.buckets()
            ],
        }

    def breakdown(self, window, filters, by):
        if by not in DIMENSIONS:
            raise PlanError("invalid_cost_dimension", "Use agent, model, node, goal, plan or job", status=422)
        groups = defaultdict(list)
        for job in self._jobs(filters):
            groups[self.attribution(job)[by]].append(job)
        rows = []
        for key, jobs in groups.items():
            values = self.aggregate(jobs, window)
            rows.append(
                {
                    "key": key,
                    "label": key or "未关联",
                    **values,
                    "jobs": [
                        {
                            "jobId": job["jobId"],
                            "nodeId": job["nodeId"],
                            "status": job["status"],
                            **self.aggregate([job], window),
                        }
                        for job in jobs
                    ],
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

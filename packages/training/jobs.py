"""Durable training admission, budget reservations and fenced GPU ownership."""

import asyncio
import contextlib
import hashlib
import hmac
import json
import secrets
import time
import uuid
from copy import deepcopy

from packages.info.approvals import ApprovalConflict
from packages.info.plans import PlanError
from packages.library.archive import destination_parent
from packages.library.transactions import durable_json
from packages.training.node_telemetry import NodeTelemetryHistory

TERMINAL = {"succeeded", "failed", "cancelled"}


def profile_key(plan, task_hash, hardware, gpu_id):
    value = {
        "taskContentHash": task_hash,
        "hardware": hardware,
        "gpuId": gpu_id,
        **{key: plan[key] for key in ("algorithm", "model", "hyperparameters")},
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class TrainingJobs:
    def __init__(self, manager, *, clock=time.time):
        self.manager, self.clock = manager, clock
        self.telemetry = NodeTelemetryHistory()
        self.path = manager.settings.state_root / "jobs.json"
        self.records, self.nodes = {}, {}
        self._worker = None
        self.node_token = manager.settings.node_token
        self._failure = False
        self._started = False
        from apps.node.supervisor import LocalNodeSupervisor

        self.node = LocalNodeSupervisor(self)

    def startup(self):
        if self.node_token is None:
            from packages.vault import Vault

            credentials = Vault(self.manager.settings.state_root / "internal-node")
            if "local-node" not in credentials.names():
                credentials.set("local-node", secrets.token_urlsafe(32))
            self.node_token = credentials.adapter_environment({"NODE_TOKEN": "local-node"})["NODE_TOKEN"]
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.records, self.nodes = saved["jobs"], saved["nodes"]
        self.manager.costs.prepare(self.records)
        # Retain leases while a live node reconnects; a restart never invents an idle GPU.
        for node in self.nodes.values():
            node["reconnectDeadline"] = self.clock() + 60
        for approval in list(self.manager.approvals.records.values()):
            if (
                approval.subject == "job"
                and approval.status == "pending"
                and not any(job["approvalId"] == approval.approvalId for job in self.records.values())
            ):
                self.manager.approvals.set_status(approval.approvalId, "unavailable")
        self.manager.training_plans.job_in_use = self.plan_in_use
        self._started = True
        self.node.start()
        for job in self.records.values():
            self._event(job)

    def _require_available(self):
        if self._failure or not self._started:
            raise PlanError("jobs_persistence_unavailable", "Job ownership could not be saved", status=503)

    def _save(self):
        self._require_available()
        try:
            self.manager.costs.capture(self.records, self.clock())
            durable_json(self.path, {"jobs": self.records, "nodes": self.nodes})
        except (OSError, ValueError) as exc:
            self._failure = True
            raise PlanError("jobs_persistence_unavailable", "Job ownership save failed", status=503) from exc

    async def run(self):
        while True:
            await asyncio.sleep(1)
            try:
                self.reconcile()
                if self.records:
                    self._save()
            except PlanError:
                return

    async def close(self):
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        await self.node.close()

    def _event(self, job):
        self.manager.inbox.job(job)

    def plan_in_use(self, plan_id, revision):
        return any(
            job["planId"] == plan_id
            and job["planRevision"] == revision
            and (job["status"] == "running" or self._has_lease(job))
            for job in self.records.values()
        )

    def authenticated_node(self, headers):
        token = headers.get("x-node-token", "")
        return bool(
            self.node_token and token and hmac.compare_digest(token.encode(), self.node_token.encode())
        )

    def _has_lease(self, job):
        return any(
            lease["jobId"] == job["jobId"]
            for node in self.nodes.values()
            for lease in node["leases"].values()
        )

    def get(self, identity):
        if identity not in self.records:
            raise PlanError("unknown_job", "Unknown training job", status=404)
        return self.records[identity]

    def document(self, job):
        value = deepcopy(
            {key: value for key, value in job.items() if key not in ("plan", "lastNodeReport", "costLedger")}
        ) | {
            "gpuId": job["resourceId"] if job["executionDevice"] == "cuda" else None,
        }
        if job["reason"] == "pending_approval":
            value["code"] = "pending_approval"
        return value

    def _approved_plan(self, plan_id, revision=None):
        record = self.manager.training_plans.get(plan_id, revision)
        value = self.manager.training_plans.document(record)
        if value["status"] != "approved":
            raise PlanError("plan_not_approved", "The current training plan revision must be approved")
        return value

    def request(self, plan_id, seed, actor, request_id, revision=None):
        self._require_available()
        for job in self.records.values():
            if job["requestId"] == request_id and job["requestedBy"] == actor.model_dump():
                if (
                    job["planId"] != plan_id
                    or seed is not None
                    and job["seed"] != seed
                    or revision is not None
                    and job["planRevision"] != revision
                ):
                    raise PlanError("idempotency_conflict", "Request ID belongs to different job inputs")
                return self.document(job)
        plan = self._approved_plan(plan_id)
        if revision is not None and plan["revision"] != revision:
            raise PlanError("plan_not_approved", "Select the current approved plan revision")
        if seed is None:
            if len(plan["jobs"]["seeds"]) != 1:
                raise PlanError("invalid_job", "Choose one of the plan's declared seeds", status=422)
            seed = plan["jobs"]["seeds"][0]
        if seed not in plan["jobs"]["seeds"]:
            raise PlanError("invalid_job", "Seed is outside the approved plan", status=422)
        iterations = plan["hyperparameters"].get("max_iterations")
        if type(iterations) is not int or iterations < 1:
            raise PlanError("invalid_job", "max_iterations must be a positive integer", status=422)
        if plan["hyperparameters"].get("device", "cuda") not in ("cuda", "cpu"):
            raise PlanError("invalid_job", "Training device must be explicitly cpu or cuda", status=422)
        task = self.manager.tasks.show(plan["task"])
        scene = self.manager.library.resolve(task["task"]["scene"])
        if (
            scene.metadata["stage"].startswith("python:")
            and task["task"]["isaaclab"].get("sceneInput", "usd") != "scene-package"
        ):
            raise PlanError(
                "task_scene_input_unsupported", "Python builder inputs need a scene-package Task", status=422
            )
        demonstrations = self.manager.demonstrations.resolve_inputs(
            task["task"].get("demonstrations", []),
            task["task"] | {"sceneContentHash": task["sceneContentHash"]},
        )
        job = {
            "jobId": "job-" + uuid.uuid4().hex,
            "kind": "training",
            "status": "queued",
            "planId": plan_id,
            "planRevision": plan["revision"],
            "planApprovalId": plan["approvalId"],
            "plan": plan,
            "task": task["id"],
            "taskContentHash": task["contentHash"],
            "scene": scene.id,
            "sceneContentHash": scene.content_hash,
            "assets": list(
                {
                    item["id"]: item for item in (*scene.asset_dependencies(), *scene.template_dependencies())
                }.values()
            ),
            "demonstrationSets": demonstrations,
            "demonstrationRecordings": self.manager.demonstrations.recording_inputs(demonstrations),
            "seed": seed,
            "requestId": request_id,
            "requestedBy": actor.model_dump(),
            "createdAt": self.clock(),
            "startedAt": None,
            "finishedAt": None,
            "estimatedGpuHours": None,
            "chargedGpuHours": 0.0,
            "profileKey": None,
            "profile": None,
            "approvalId": None,
            "budgetReserved": False,
            "executionDevice": plan["hyperparameters"].get("device", "cuda"),
            "estimatedWallSeconds": None,
            "nodeId": None,
            "resourceId": None,
            "resourceModel": None,
            "leaseId": None,
            "cancelRequested": False,
            "reason": "estimate_unavailable",
            "metrics": {},
            "metricsHistory": [],
            "artifacts": [],
        }
        self.records[job["jobId"]] = job
        self.reconcile()
        self._save()
        self._event(job)
        return self.document(job)

    def _budget(self, job):
        plan = job["plan"]
        peers = [
            item
            for item in self.records.values()
            if item["planId"] == job["planId"] and item["jobId"] != job["jobId"]
        ]
        consumed = sum(
            item["chargedGpuHours"]
            if item["status"] in TERMINAL
            else (item["estimatedGpuHours"] or 0)
            if item["budgetReserved"]
            else 0
            for item in peers
        )
        remaining = max(0, plan["budget"]["gpuHours"] - consumed)
        approval = self.manager.approvals.records.get(job["approvalId"])
        if job["estimatedGpuHours"] > remaining and approval is None:
            from packages.protocol.schema import Actor

            approval = self.manager.approvals.submit(
                subject="job",
                subject_id=job["jobId"],
                requester=Actor.model_validate(job["requestedBy"]),
                title="超预算训练作业",
                details={
                    "jobId": job["jobId"],
                    "planId": job["planId"],
                    "planRevision": job["planRevision"],
                    "estimatedGpuHours": job["estimatedGpuHours"],
                    "remainingGpuHours": remaining,
                },
            )
            job["approvalId"] = approval.approvalId
        if approval and approval.status != "approved":
            job["budgetReserved"] = False
            job["reason"] = "pending_approval" if approval.status == "pending" else "approval_denied"
            return False
        job["allowedGpuHours"] = max(remaining, job["estimatedGpuHours"])
        job["budgetReserved"] = True
        return True

    def reconcile(self):
        self._require_available()
        now = self.clock()
        for node in self.nodes.values():
            if now > max(node["lastSeen"] + 60, node.get("reconnectDeadline", 0)):
                for lease in node["leases"].values():
                    job = self.records[lease["jobId"]]
                    if job["status"] not in TERMINAL:
                        job.update(status="failed", reason="node_lost", finishedAt=now)
                        job["chargedGpuHours"] = max(
                            job["chargedGpuHours"],
                            (now - lease["assignedAt"]) / 3600 if job["executionDevice"] == "cuda" else 0.0,
                        )
                        self._event(job)
                    lease["quarantined"] = True
        for job in self.records.values():
            if job["status"] != "queued" or job["leaseId"]:
                continue
            try:
                plan = self._approved_plan(job["planId"], job["planRevision"])
                if plan["approvalId"] != job["planApprovalId"]:
                    raise PlanError("plan_not_approved", "Plan approval changed")
            except PlanError:
                job.update(status="cancelled", reason="plan_not_approved", finishedAt=now)
                self._event(job)
                continue
            for node in self.nodes.values():
                resources = node["gpus"] + ([{"id": "cpu"}] if node.get("cpuAvailable") else [])
                for gpu in sorted(resources, key=lambda item: item["id"] in node["leases"]):
                    if (gpu["id"] == "cpu") != (job["executionDevice"] == "cpu"):
                        continue
                    key = profile_key(job["plan"], job["taskContentHash"], node["hardware"], gpu["id"])
                    measured = node["profiles"].get(key)
                    if measured is None or job["profileKey"] not in (None, key):
                        continue
                    if job["estimatedGpuHours"] is None:
                        job["estimatedWallSeconds"] = (
                            measured["setupSeconds"]
                            + measured["iterationSeconds"] * job["plan"]["hyperparameters"]["max_iterations"]
                        )
                        job["estimatedGpuHours"] = (
                            job["estimatedWallSeconds"] / 3600 if job["executionDevice"] == "cuda" else 0.0
                        )
                        job.update(profileKey=key, profile=deepcopy(measured))
                    if not self._budget(job):
                        break
                    if now > node["lastSeen"] + 60:
                        job["reason"] = "node_unavailable"
                        break
                    if self._reserve_resource(job, node, gpu, now):
                        break
                if job["leaseId"]:
                    break

    def _reserve_resource(self, job, node, resource, now):
        # This boundary shares ownership with the registration thread, without I/O or library locks.
        with self.manager._resource_lock:
            if job["executionDevice"] == "cuda" and any(
                session.kind == "newton" and session.options.device != "cpu"
                and (session.holds_resources or session is self.manager._starting_session)
                for session in self.manager.platform_sessions()
            ):
                job["reason"] = "session_gpu_busy"
                return False
            if resource["id"] in node["leases"]:
                job["reason"] = "gpu_busy"
                return False
            lease = {"jobId": job["jobId"], "leaseId": uuid.uuid4().hex, "assignedAt": now,
                     "quarantined": False}
            node["leases"][resource["id"]] = lease
            job.update(nodeId=node["id"], resourceId=resource["id"], leaseId=lease["leaseId"],
                       resourceModel=resource.get("name") if job["executionDevice"] == "cuda" else None,
                       reason="node_starting")
            return True

    def holds_gpu(self):
        with self.manager._resource_lock:
            return self._failure or any(any(key != "cpu" for key in node["leases"])
                                        for node in tuple(self.nodes.values()))

    def heartbeat(self, body):
        self._require_available()
        now = self.clock()
        node = self.nodes.get("local")
        if node and node["instanceId"] != body.instanceId and node["leases"]:
            raise PlanError("node_owner_conflict", "The previous node still owns a GPU lease")
        if len({gpu.id for gpu in body.gpus}) != len(body.gpus):
            raise PlanError("invalid_node_report", "GPU identities must be distinct", status=422)
        if len({report.jobId for report in body.reports}) != len(body.reports):
            raise PlanError("invalid_node_report", "Job reports must be distinct", status=422)
        replayed = set()
        for report in body.reports:
            job = self.get(report.jobId)
            if (
                node
                and node["instanceId"] == body.instanceId
                and job.get("lastNodeReport") == report.model_dump()
            ):
                replayed.add(report.jobId)
                continue
            lease = (node or {}).get("leases", {}).get(job["resourceId"])
            if (
                not lease
                or lease["leaseId"] != report.leaseId
                or job["nodeId"] != "local"
                or node["instanceId"] != body.instanceId
            ):
                raise PlanError("lease_mismatch", "Node report does not own this dispatch", status=403)
            if report.elapsedSeconds < job["metrics"].get("elapsedSeconds", 0):
                raise PlanError("invalid_node_report", "Job elapsed time cannot decrease", status=422)
            if report.status == "succeeded" and not job["artifacts"]:
                raise PlanError(
                    "artifacts_incomplete", "Upload verified artifacts before success", status=422
                )
            self._log_suffix(job, report.logAfter, report.logs)
        if node is None or node["instanceId"] != body.instanceId:
            node = {"id": "local", "instanceId": body.instanceId, "leases": {}, "profiles": {}}
            self.nodes["local"] = node
        if node["leases"] and node.get("hardware", body.hardware) != body.hardware:
            raise PlanError("node_owner_conflict", "Hardware changed while a lease is retained")
        node.update(
            hardware=body.hardware,
            lastSeen=now,
            reconnectDeadline=0,
            gpus=[gpu.model_dump() for gpu in body.gpus],
            cpuAvailable=body.cpuAvailable,
        )
        node["profiles"].update({profile.key: profile.model_dump() for profile in body.profiles})
        for report in body.reports:
            job = self.get(report.jobId)
            if report.jobId in replayed:
                continue
            self._append_logs(job, report.logAfter, report.logs)
            job["metrics"] = report.model_dump(
                exclude={"jobId", "leaseId", "status", "reason", "logs", "logAfter"}
            )
            job["chargedGpuHours"] = max(
                job["chargedGpuHours"],
                report.elapsedSeconds / 3600 if job["executionDevice"] == "cuda" else 0.0,
            )
            if report.status == "running" and job["status"] not in TERMINAL:
                job.update(status="running", reason=report.reason, startedAt=job["startedAt"] or now)
            elif report.status in TERMINAL or report.status == "cleaned":
                if job["status"] not in TERMINAL:
                    job.update(
                        status=report.status if report.status != "cleaned" else "failed",
                        reason=report.reason,
                        finishedAt=now,
                    )
                with self.manager._resource_lock:
                    del node["leases"][job["resourceId"]]
            job["lastNodeReport"] = report.model_dump()
            history = job.setdefault("metricsHistory", [])
            history.append({
                "receivedAt": now, "reportedStatus": report.status, "reason": report.reason,
                **job["metrics"],
            })
            del history[:-900]
            self._event(job)
        self.reconcile()
        self._save()
        self.telemetry.observe(node["id"], node["instanceId"], node["gpus"], now)
        return {
            "nodeId": "local",
            "logCursors": {report.jobId: len(self.read_logs(report.jobId)) for report in body.reports},
            "assignments": [
                {
                    "jobId": lease["jobId"],
                    "leaseId": lease["leaseId"],
                    "resourceId": gpu,
                    "gpuId": gpu if gpu != "cpu" else None,
                    "cancel": self.records[lease["jobId"]]["cancelRequested"]
                    or self.records[lease["jobId"]]["status"] in TERMINAL,
                }
                for gpu, lease in node["leases"].items()
            ],
        }

    def list_nodes(self):
        return [
            {
                **deepcopy(node),
                "gpus": [
                    {**gpu, "history": self.telemetry.for_gpu(
                        node["id"], node["instanceId"], gpu["id"], self.clock()
                    )} for gpu in node["gpus"]
                ],
                "state": "online" if self.clock() <= node["lastSeen"] + 60 else "offline",
                "queue": [job["jobId"] for job in self.records.values() if job["status"] == "queued"],
            }
            for node in self.nodes.values()
        ]

    def reference_snapshot(self):
        # Pinned fields never change; copy membership once for the library worker thread.
        return {
            job["jobId"]: {
                key: deepcopy(job[key]) for key in ("jobId", "scene", "sceneContentHash", "assets")
            }
            for job in tuple(self.records.values())
        }

    def _log_path(self, identity):
        self.get(identity)
        root = self.manager.library_assets.roots["user"]
        return destination_parent(root, ("jobs", identity), create=True) / "logs.jsonl"

    def read_logs(self, identity):
        path = self._log_path(identity)
        if not path.exists():
            return []
        lines, complete = [], 0
        for line in path.read_bytes().splitlines(keepends=True):
            if not line.endswith(b"\n"):
                with path.open("r+b") as stream:
                    stream.truncate(complete)
                break
            lines.append(json.loads(line))
            complete += len(line)
        return lines

    def _log_suffix(self, job, after, lines):
        previous = self.read_logs(job["jobId"])
        overlap = min(len(previous) - after, len(lines))
        if (
            after > len(previous)
            or previous[after : after + overlap] != lines[:overlap]
            or any(len(line) > 16000 for line in lines)
        ):
            raise PlanError("invalid_log_cursor", "Node logs do not continue the retained prefix", status=422)
        return lines[overlap:]

    def _append_logs(self, job, after, lines):
        new = self._log_suffix(job, after, lines)
        if new:
            with self._log_path(job["jobId"]).open("a", encoding="utf-8", newline="\n") as stream:
                for line in new:
                    stream.write(json.dumps(line, ensure_ascii=False) + "\n")
                stream.flush()
                import os

                os.fsync(stream.fileno())

    async def logs(self, identity, after, timeout):
        deadline = time.monotonic() + timeout
        while True:
            job, lines = self.get(identity), self.read_logs(identity)
            if after > len(lines):
                raise PlanError("invalid_log_cursor", "Log cursor is beyond retained output", status=422)
            settled = job["status"] in TERMINAL and not self._has_lease(job)
            if len(lines) > after or settled or time.monotonic() >= deadline:
                selected = lines[after : after + 500]
                return {
                    "lines": selected,
                    "cursor": after + len(selected),
                    "complete": settled and after + len(selected) == len(lines),
                }
            await asyncio.sleep(0.1)

    def cancel(self, identity):
        self._require_available()
        job = self.get(identity)
        if job["status"] in TERMINAL:
            return self.document(job)
        if job["leaseId"]:
            job.update(cancelRequested=True, reason="cancelling")
        else:
            job.update(status="cancelled", reason="cancelled", finishedAt=self.clock())
        self._save()
        self._event(job)
        return self.document(job)

    def decide(self, approval, decision, actor, note):
        self._require_available()
        job = self.get(approval.subjectId)
        current = self.manager.approvals.records[approval.approvalId]
        if job["approvalId"] != approval.approvalId or job["status"] != "queued" or job["leaseId"]:
            raise ApprovalConflict("This job approval can no longer be changed")
        expected = {"approve": "approved", "deny": "denied", "revoke": "revoked"}[decision]
        if current.status == expected:
            return
        if current.status != "pending" and decision != "revoke":
            raise ApprovalConflict("Job approval already processed")
        self.manager.approvals.decide(approval.approvalId, decision, actor, note)
        self.reconcile()
        self._save()
        self._event(job)

    def can_revoke(self, approval):
        job = self.records.get(approval.subjectId)
        return bool(
            job
            and job["approvalId"] == approval.approvalId
            and job["status"] == "queued"
            and not self._has_lease(job)
            and approval.status in ("approved", "denied")
        )

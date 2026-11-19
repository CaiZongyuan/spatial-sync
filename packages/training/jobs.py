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
from packages.training.job_contract import VALIDATION_ITERATIONS, VALIDATION_TIMEOUT
from packages.training.node_telemetry import NodeTelemetryHistory
from packages.training.runtime import (
    RuntimeMismatch,
    job_backend,
    reported_versions,
    runtime_profile_key,
    training_runtime,
)
from packages.training.runtime import profile_key as profile_key

TERMINAL = {"succeeded", "failed", "cancelled"}


def _secret_digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


class TrainingJobs:
    def __init__(self, manager, *, clock=time.time):
        self.manager, self.clock = manager, clock
        self.telemetry = NodeTelemetryHistory()
        self.path = manager.settings.state_root / "jobs.json"
        self.records, self.nodes = {}, {}
        self.registrations, self.node_credentials = {}, {}
        self.gpu_quarantine = {}
        self.cleanup_key = secrets.token_hex(32)
        self.lease_sequence = 0
        self.validation_profiles = {}
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
            self.registrations = saved.get("registrations", {})
            self.node_credentials = saved.get("nodeCredentials", {})
            self.gpu_quarantine = saved.get("gpuQuarantine", {})
            self.cleanup_key = saved.get("cleanupKey", self.cleanup_key)
            self.lease_sequence = saved.get("leaseSequence", 0)
        self.manager.costs.prepare(self.records)
        # Retain leases while a live node reconnects; a restart never invents an idle GPU.
        now = self.clock()
        for node in self.nodes.values():
            if node["id"] == "local" or not self._offline(node, now):
                node["reconnectDeadline"] = now + 60
        for approval in list(self.manager.approvals.records.values()):
            if (
                approval.subject == "job"
                and approval.status == "pending"
                and not any(job["approvalId"] == approval.approvalId for job in self.records.values())
            ):
                self.manager.approvals.set_status(approval.approvalId, "unavailable")
        self.manager.training_plans.job_in_use = self.plan_in_use
        self.manager.training_plans.validation_estimate = self.validation_estimate
        self._started = True
        self.node.start()
        for job in self.records.values():
            if job.get("validationEstimate"):
                self.validation_profiles[job["profileKey"]] = job["profile"]
            self._event(job)

    def _require_available(self):
        if self._failure or not self._started:
            raise PlanError("jobs_persistence_unavailable", "Job ownership could not be saved", status=503)

    def _save(self):
        self._require_available()
        try:
            self.manager.costs.capture(self.records, self.clock())
            durable_json(self.path, {
                "jobs": self.records, "nodes": self.nodes, "registrations": self.registrations,
                "nodeCredentials": self.node_credentials, "leaseSequence": self.lease_sequence,
                "gpuQuarantine": self.gpu_quarantine, "cleanupKey": self.cleanup_key,
            })
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

    def _require_remote_nodes(self):
        self._require_available()
        if not self.manager.settings.remote:
            raise PlanError("remote_nodes_disabled", "Enable authenticated remote mode", status=403)

    def issue_registration(self, body):
        self._require_remote_nodes()
        identity = body.nodeId or "node-" + uuid.uuid4().hex[:12]
        if identity == "local" or identity in self.node_credentials:
            raise PlanError("node_id_taken", "This node ID is already in use")
        now = self.clock()
        self.registrations = {key: record for key, record in self.registrations.items()
                              if record["expiresAt"] > now and record["nodeId"] != identity}
        code = secrets.token_urlsafe(32)
        record = {"nodeId": identity, "expiresAt": now + body.ttlSeconds, "colocated": body.colocated}
        self.registrations[_secret_digest(code)] = record
        self._save()
        return {**record, "code": code}

    def register_node(self, code):
        self._require_remote_nodes()
        digest = _secret_digest(code)
        record = self.registrations.get(digest)
        if record is None or record["expiresAt"] <= self.clock():
            raise PlanError("registration_invalid", "Registration code is invalid or expired", status=403)
        identity = record["nodeId"]
        token = secrets.token_urlsafe(32)
        self.node_credentials[identity] = {"digest": _secret_digest(token), "colocated": record["colocated"]}
        del self.registrations[digest]
        self._save()
        return {"nodeId": identity, "token": token}

    def revoke_node(self, identity):
        self._require_remote_nodes()
        with self.manager._resource_lock:
            if identity not in self.node_credentials:
                raise PlanError("unknown_node", "Unknown registered Compute Node", status=404)
            node = self.nodes.get(identity)
            if node:
                self._fence_node(node, "node_revoked", self.clock())
                del self.nodes[identity]
            del self.node_credentials[identity]
            self._save()

    def authenticated_node(self, headers, identity=None):
        claimed = headers.get("x-node-id", "local")
        if self._failure or (identity is not None and identity != claimed):
            return False
        token = headers.get("x-node-token", "")
        if not token:
            return False
        if claimed == "local":
            return bool(self.node_token and hmac.compare_digest(token.encode(), self.node_token.encode()))
        record = self.node_credentials.get(claimed) if self.manager.settings.remote else None
        return bool(record and hmac.compare_digest(_secret_digest(token), record["digest"]))

    def _has_lease(self, job):
        return job["leaseId"] in self.gpu_quarantine or any(
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

    def request(self, plan_id, seed, actor, request_id, revision=None, *, kind="training", target_node=None):
        self._require_available()
        for job in self.records.values():
            if job["requestId"] == request_id and job["requestedBy"] == actor.model_dump():
                if (
                    job["planId"] != plan_id
                    or job["kind"] != kind
                    or job.get("targetNodeId") != target_node
                    or seed is not None
                    and job["seed"] != seed
                    or revision is not None
                    and job["planRevision"] != revision
                ):
                    raise PlanError("idempotency_conflict", "Request ID belongs to different job inputs")
                return self.document(job)
        validation = kind == "plan_validation"
        plan = self._validation_plan(plan_id, revision) if validation else self._approved_plan(plan_id)
        if not validation and revision is not None and plan["revision"] != revision:
            raise PlanError("plan_not_approved", "Select the current approved plan revision")
        if seed is None:
            seeds = plan["jobs"]["seeds"]
            if not seeds or not validation and len(seeds) != 1:
                raise PlanError("invalid_job", "Choose one of the plan's declared seeds", status=422)
            seed = seeds[0]
        if seed not in plan["jobs"]["seeds"]:
            raise PlanError("invalid_job", "Seed is outside the approved plan", status=422)
        iterations = plan["hyperparameters"].get("max_iterations")
        if type(iterations) is not int or iterations < 1:
            raise PlanError("invalid_job", "max_iterations must be a positive integer", status=422)
        if plan["hyperparameters"].get("device", "cuda") not in ("cuda", "cpu"):
            raise PlanError("invalid_job", "Training device must be explicitly cpu or cuda", status=422)
        task = self.manager.tasks.show(plan["task"])
        scene = self.manager.library.resolve(task["task"]["scene"])
        backend = task["task"].get("mjlab") or task["task"].get("isaaclab") or {}
        is_mjlab = task["task"].get("mjlab") is not None
        if target_node is not None and target_node != "local":
            if not self.manager.settings.remote or target_node not in self.node_credentials:
                raise PlanError("unknown_node", "Select a registered Compute Node", status=404)
        if validation:
            hyper = plan["hyperparameters"]
            if not is_mjlab or type(hyper.get("num_envs")) is not int or hyper["num_envs"] < 1:
                raise PlanError(
                    "invalid_job", "Validation requires an mjlab Task and positive num_envs", status=422
                )
            if iterations > 2**31 - 1:
                raise PlanError("invalid_job", "max_iterations exceeds the supported range", status=422)
            if any(
                item["kind"] == kind and item["planId"] == plan_id
                and item["planRevision"] == plan["revision"] and item["status"] not in TERMINAL
                for item in self.records.values()
            ):
                raise PlanError("validation_in_progress", "This revision already has a validation Job")
        if (
            scene.metadata["stage"].startswith("python:")
            and backend.get("sceneInput", "usd") != "scene-package"
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
            "kind": kind,
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
            "targetNodeId": target_node,
            "backend": "mjlab" if is_mjlab else "isaaclab",
        }
        if validation:
            job["validationLimits"] = {
                "iterations": VALIDATION_ITERATIONS, "timeoutSeconds": VALIDATION_TIMEOUT,
            }
        self.records[job["jobId"]] = job
        self.reconcile()
        self._save()
        self._event(job)
        return self.document(job)

    def _validation_plan(self, plan_id, revision):
        store = self.manager.training_plans
        plan = store.document(store.get(plan_id))
        if plan["status"] not in ("submitted", "approved") or revision not in (None, plan["revision"]):
            raise PlanError("plan_not_available", "Select the current submitted or approved training plan")
        return plan

    def validation_estimate(self, approval_id):
        if approval_id is None:
            return {}
        for job in reversed(self.records.values()):
            if job["planApprovalId"] == approval_id and job.get("validationEstimate"):
                return deepcopy(job["validationEstimate"])
        return {}

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
            self._expire_leases(node, now)
        for job in self.records.values():
            if job["status"] != "queued" or job["leaseId"]:
                continue
            validation = job["kind"] == "plan_validation"
            try:
                if validation:
                    plan = self._validation_plan(job["planId"], job["planRevision"])
                else:
                    plan = self._approved_plan(job["planId"], job["planRevision"])
                if plan["approvalId"] != job["planApprovalId"]:
                    raise PlanError("plan_not_approved", "Plan approval changed")
            except PlanError:
                job.update(status="cancelled", reason="plan_not_approved", finishedAt=now)
                self._event(job)
                continue
            target = job.get("targetNodeId")
            if target and target != "local" and target not in self.node_credentials:
                job.update(status="cancelled", reason="node_revoked", finishedAt=now)
                self._event(job)
                continue
            self._schedule(job, now, validation)

    def _schedule(self, job, now, validation):
        target = job.get("targetNodeId")
        reason = "estimate_unavailable"
        if validation or target and target not in self.nodes:
            reason = "node_unavailable"
        candidates = []
        task = self.manager.tasks.show(job["task"])["task"] if job.get("backend") is None else None
        backend = job_backend(job, task)
        for node in self.nodes.values():
            if node["id"] != "local" and not self.manager.settings.remote:
                continue
            if target and node["id"] != target:
                continue
            resources = node["gpus"] if job["executionDevice"] == "cuda" else (
                [{"id": "cpu"}] if node.get("cpuAvailable") else []
            )
            if not resources:
                continue
            try:
                runtime = training_runtime(node, job, backend)
            except RuntimeMismatch as error:
                reason = error.code
                continue
            for resource in resources:
                key = runtime_profile_key(job["plan"], job["taskContentHash"], node, resource, runtime)
                measured = self.validation_profiles.get(key) or node["profiles"].get(key)
                if not validation and (not measured or job["approvalId"] and job["profileKey"] != key):
                    continue
                with self.manager._resource_lock:
                    blocker = self._resource_blocker(node, resource, now)
                candidates.append((blocker, node, resource, key, measured, runtime))
        if not candidates:
            job["reason"] = reason
            return
        selected = min(candidates, key=lambda item: item[0] is not None)
        blocker, node, resource, key, measured, runtime = selected
        if not validation:
            if job["profileKey"] != key:
                job.update(self._estimate(job, measured), profileKey=key, profile=deepcopy(measured))
            if not self._budget(job):
                return
        if blocker:
            job["reason"] = blocker
        elif self._reserve_resource(job, node, resource, now, runtime) and validation:
            job["profileKey"] = key

    def _resource_blocker(self, node, resource, now):
        if now > node["lastSeen"] + 60:
            return "node_unavailable"
        if resource["id"] != "cpu" and self._colocated(node) and any(
            session.kind == "newton" and session.options.device != "cpu"
            and (session.holds_resources or session is self.manager._starting_session)
            for session in self.manager.platform_sessions()
        ):
            return "session_gpu_busy"
        if resource["id"] in node["leases"] or any(
            row["gpuId"] == resource["id"] for row in self.gpu_quarantine.values()
        ) or (
            resource["id"] != "cpu" and any(resource["id"] in peer["leases"] for peer in self.nodes.values())
        ):
            return "gpu_busy"
        return None

    def _reserve_resource(self, job, node, resource, now, runtime):
        # This boundary shares ownership with the registration thread, without I/O or library locks.
        with self.manager._resource_lock:
            if reason := self._resource_blocker(node, resource, now):
                job["reason"] = reason
                return False
            self.lease_sequence += 1
            lease = {"jobId": job["jobId"], "leaseId": str(self.lease_sequence), "assignedAt": now,
                     "quarantined": False}
            if node["id"] != "local" and resource["id"] != "cpu":
                lease["cleanupVersion"] = 1
            if runtime:
                lease.update(runtime, runtimeIdentity=runtime)
            node["leases"][resource["id"]] = lease
            job.update(nodeId=node["id"], resourceId=resource["id"], leaseId=lease["leaseId"],
                       resourceModel=resource.get("name") if job["executionDevice"] == "cuda" else None,
                       reason="node_starting")
            return True

    def holds_gpu(self):
        with self.manager._resource_lock:
            return (
                self._failure or any(row["colocated"] for row in self.gpu_quarantine.values())
                or any(any(key != "cpu" for key in node["leases"])
                       for node in self.nodes.values() if self._colocated(node))
            )

    def _colocated(self, node):
        return node["id"] == "local" or self.node_credentials.get(node["id"], {}).get("colocated", False)

    def _fence_node(self, node, reason, now):
        for resource in tuple(node["leases"]):
            self._fence_lease(node, resource, reason, now)

    @staticmethod
    def _offline(node, now):
        return now > max(node["lastSeen"] + 60, node.get("reconnectDeadline", 0))

    def _validation_expired(self, lease, now):
        return (
            self.records[lease["jobId"]]["kind"] == "plan_validation"
            and now - lease["assignedAt"] > VALIDATION_TIMEOUT
        )

    def dispatch_expired(self, node, lease):
        now = self.clock()
        return lease["quarantined"] or self._offline(node, now) or self._validation_expired(lease, now)

    def _expire_leases(self, node, now):
        offline = self._offline(node, now)
        with self.manager._resource_lock:
            if offline and node["id"] != "local" and node["leases"]:
                node["instanceExpired"] = True
            changed = False
            for resource, lease in tuple(node["leases"].items()):
                if node["id"] == "local" and lease["quarantined"]:
                    continue
                if offline or lease["quarantined"] or self._validation_expired(lease, now):
                    self._fence_lease(node, resource, "node_lost" if offline else "validation_timeout", now)
                    changed = True
            return changed

    def _fence_lease(self, node, resource, reason, now):
        lease = node["leases"][resource]
        self._fail_lease(lease, reason, now)
        if node["id"] == "local":
            lease["quarantined"] = True  # Retained local owners can still send their legacy terminal report.
            return
        if resource != "cpu":
            receipt = self._cleanup_scope(node, resource, lease)
            self.gpu_quarantine[lease["leaseId"]] = receipt | {"colocated": self._colocated(node)}
        del node["leases"][resource]

    def _cleanup_scope(self, node, resource, lease):
        return {
            "schemaVersion": lease.get("cleanupVersion"), "nodeId": node["id"],
            "instanceId": node["instanceId"], "jobId": lease["jobId"],
            "leaseId": lease["leaseId"], "gpuId": resource,
        }

    def _cleanup_token(self, receipt):
        payload = json.dumps(receipt, sort_keys=True).encode()
        return hmac.digest(bytes.fromhex(self.cleanup_key), payload, "sha256").hex()

    def clean_gpu(self, body, token):
        self._require_available()
        receipt = body.model_dump()
        if not hmac.compare_digest(self._cleanup_token(receipt).encode(), token.encode()):
            raise PlanError(
                "cleanup_auth_required", "Cleanup capability does not match this lease", status=403
            )
        with self.manager._resource_lock:
            node = self.nodes.get(body.nodeId)
            lease = node and node["leases"].get(body.gpuId)
            if lease and lease["leaseId"] == body.leaseId:
                if not self.dispatch_expired(node, lease):
                    raise PlanError("cleanup_lease_active", "The receipt is for an active lease", status=409)
                self._expire_leases(node, self.clock())
            reserved = self.gpu_quarantine.get(body.leaseId)
            if reserved is None:
                return  # A signed receipt can be retried after this lease has released.
            if any(reserved[key] != value for key, value in receipt.items()):
                raise PlanError(
                    "cleanup_auth_required", "Receipt does not match the quarantined lease", status=403
                )
            del self.gpu_quarantine[body.leaseId]
            self._save()

    def _fail_lease(self, lease, reason, now):
        job = self.records[lease["jobId"]]
        if job["status"] not in TERMINAL:
            job.update(status="failed", reason=reason, finishedAt=now)
            if job["executionDevice"] == "cuda":
                job["chargedGpuHours"] = max(job["chargedGpuHours"], (now - lease["assignedAt"]) / 3600)
            self._event(job)

    def _estimate(self, job, profile):
        iterations = job["plan"]["hyperparameters"]["max_iterations"]
        wall = (
            profile.get("dispatchOverheadSeconds", 0) + profile["setupSeconds"]
            + profile["iterationSeconds"] * iterations
        )
        return {"estimatedWallSeconds": wall,
                "estimatedGpuHours": wall / 3600 if job["executionDevice"] == "cuda" else 0.0}

    def _check_validation(self, job, lease, report, now):
        timing = report.validation
        if job["kind"] != "plan_validation" or report.status != "succeeded":
            if timing is not None:
                raise PlanError(
                    "invalid_validation", "Only successful validation reports include timings", status=422
                )
            return
        if (
            timing is None or timing.numEnvs != job["plan"]["hyperparameters"]["num_envs"]
            or report.iteration != VALIDATION_ITERATIONS
            or not timing.setupSeconds + timing.warmupSeconds + sum(timing.samplesSeconds)
            <= report.elapsedSeconds <= VALIDATION_TIMEOUT
            or now - lease["assignedAt"] > VALIDATION_TIMEOUT
            or lease["quarantined"] or job["cancelRequested"] or job["status"] in TERMINAL
        ):
            raise PlanError(
                "invalid_validation", "Timing does not cover the assigned bounded validation", status=422
            )

    def _record_validation(self, job, node, timing, elapsed, now):
        iteration = max(timing.samplesSeconds)
        measured_training = timing.setupSeconds + timing.warmupSeconds + sum(timing.samplesSeconds)
        profile = {
            "key": job["profileKey"],
            "setupSeconds": timing.setupSeconds + max(0, timing.warmupSeconds - iteration),
            "dispatchOverheadSeconds": elapsed - measured_training,
            "iterationSeconds": iteration, "samples": len(timing.samplesSeconds),
            "measuredAt": now, "evidence": job["jobId"],
        }
        estimate = self._estimate(job, profile)
        job.update(estimate, profile=profile, validationEstimate={
            **estimate, "validationJobId": job["jobId"], "profileKey": profile["key"],
        })
        node["profiles"][profile["key"]] = self.validation_profiles[profile["key"]] = profile

    def heartbeat(self, body, identity="local"):
        self._require_available()
        now = self.clock()
        node = self.nodes.get(identity)
        if node and self._expire_leases(node, now):
            self._save()
        changed_instance = node is not None and node["instanceId"] != body.instanceId
        if node and not changed_instance and node.get("instanceExpired"):
            raise PlanError("node_owner_conflict", "The node instance expired", status=403)
        if changed_instance and (
            body.instanceId in node.get("retiredInstances", [])
            or identity == "local" and node["leases"]
        ):
            raise PlanError("node_owner_conflict", "The instance cannot take ownership of this node")
        if len({gpu.id for gpu in body.gpus}) != len(body.gpus):
            raise PlanError("invalid_node_report", "GPU identities must be distinct", status=422)
        if any(gpu.id == "cpu" for gpu in body.gpus):
            raise PlanError("invalid_node_report", "The CPU resource ID is reserved", status=422)
        if len({report.jobId for report in body.reports}) != len(body.reports):
            raise PlanError("invalid_node_report", "Job reports must be distinct", status=422)
        fresh = []
        capabilities = {
            "id": identity, "hardware": body.hardware, "trainerVersions": body.trainerVersions,
            "isaaclabVersions": body.isaaclabVersions, "compilerHash": body.compilerHash,
            "trainingEntries": body.trainingEntries,
        }
        for report in body.reports:
            job = self.get(report.jobId)
            if report.leaseId in self.gpu_quarantine or (
                identity != "local" and job["reason"] == "validation_timeout"
            ):
                raise PlanError("lease_mismatch", "Node report does not own this dispatch", status=403)
            data = report.model_dump()
            if report.validation is None:
                del data["validation"]
            if (
                node and job["nodeId"] == identity
                and node["instanceId"] == body.instanceId
                and job.get("lastNodeReport") == data
            ):
                continue
            lease = (node or {}).get("leases", {}).get(job["resourceId"])
            if (
                not lease
                or lease["leaseId"] != report.leaseId
                or job["nodeId"] != identity
                or node["instanceId"] != body.instanceId
                or lease["quarantined"] and report.status not in ("cancelled", "failed", "cleaned")
            ):
                raise PlanError("lease_mismatch", "Node report does not own this dispatch", status=403)
            if report.elapsedSeconds < job["metrics"].get("elapsedSeconds", 0):
                raise PlanError("invalid_node_report", "Job elapsed time cannot decrease", status=422)
            if report.status == "succeeded" and "runtimeIdentity" in lease:
                try:
                    runtime = training_runtime(capabilities, job, lease.get("backend", "mjlab"))
                except RuntimeMismatch:
                    runtime = None
                if runtime != lease["runtimeIdentity"]:
                    raise PlanError(
                        "trainer_version_mismatch", "Trainer changed on the leased node", status=422
                    )
            elif report.status == "succeeded" and "trainerVersions" in lease:
                # Retained pre-identity training may finish with its original version handshake.
                backend = lease.get("backend", "mjlab")
                if (
                    job["kind"] != "training"
                    or lease["trainerVersions"] != reported_versions(capabilities, backend)
                    or lease.get("compilerHash") != body.compilerHash
                ):
                    raise PlanError(
                        "trainer_version_mismatch", "Trainer changed on the leased node", status=422
                    )
            self._check_validation(job, lease, report, now)
            if report.status == "succeeded" and job["kind"] == "training" and not job["artifacts"]:
                raise PlanError(
                    "artifacts_incomplete", "Upload verified artifacts before success", status=422
                )
            self._log_suffix(job, report.logAfter, report.logs)
            fresh.append((job, report, data))
        if node is None:
            node = {"id": identity, "instanceId": body.instanceId, "leases": {}, "profiles": {}}
            with self.manager._resource_lock:
                self.nodes[identity] = node
        elif changed_instance:
            with self.manager._resource_lock:
                self._fence_node(node, "node_instance_replaced", now)
                node.setdefault("retiredInstances", []).append(node["instanceId"])
                node["instanceId"] = body.instanceId
                node.pop("instanceExpired", None)
                self._save()
        if node["leases"] and node.get("hardware", body.hardware) != body.hardware:
            raise PlanError("node_owner_conflict", "Hardware changed while a lease is retained")
        node.update(
            hardware=body.hardware,
            lastSeen=now,
            reconnectDeadline=0,
            gpus=[gpu.model_dump() for gpu in body.gpus],
            cpuAvailable=body.cpuAvailable,
            hostname=body.hostname,
            trainerVersions=body.trainerVersions,
            isaaclabVersions=body.isaaclabVersions,
            compilerHash=body.compilerHash,
            trainingEntries=body.trainingEntries,
            legacyHardware=body.legacyHardware if identity == "local" else None,
        )
        node["profiles"].update({profile.key: profile.model_dump() for profile in body.profiles})
        for job, report, data in fresh:
            self._append_logs(job, report.logAfter, report.logs)
            job["metrics"] = {key: data[key] for key in (
                "iteration", "elapsedSeconds", "meanReward", "trainSuccess", "memoryUsedMb",
            )}
            if report.validation is not None:
                job["metrics"]["validation"] = data["validation"]
                self._record_validation(job, node, report.validation, report.elapsedSeconds, now)
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
            job["lastNodeReport"] = data
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
            "nodeId": identity,
            "logCursors": {report.jobId: len(self.read_logs(report.jobId)) for report in body.reports},
            "assignments": [
                {
                    "jobId": lease["jobId"],
                    "leaseId": lease["leaseId"],
                    "resourceId": gpu,
                    "gpuId": gpu if gpu != "cpu" else None,
                    "cancel": self.records[lease["jobId"]]["cancelRequested"]
                    or self.records[lease["jobId"]]["status"] in TERMINAL,
                    **({"timeoutSeconds": VALIDATION_TIMEOUT}
                       if self.records[lease["jobId"]]["kind"] == "plan_validation" else {}),
                    **self._cleanup_offer(node, gpu, lease),
                }
                for gpu, lease in node["leases"].items()
            ],
        }

    def _cleanup_offer(self, node, resource, lease):
        if lease.get("cleanupVersion") != 1:
            return {}
        scope = self._cleanup_scope(node, resource, lease)
        return {"cleanup": {"schemaVersion": lease["cleanupVersion"], "token": self._cleanup_token(scope)}}

    def list_nodes(self):
        identities = dict.fromkeys(
            (*self.nodes, *self.node_credentials) if self.manager.settings.remote
            else (identity for identity in self.nodes if identity == "local")
        )
        identities.update(dict.fromkeys(row["nodeId"] for row in self.gpu_quarantine.values()))
        return [self.node_document(identity) for identity in identities]

    def node_document(self, identity):
        node = self.nodes.get(identity)
        if identity != "local" and not self.manager.settings.remote:
            node = None
        gpu_ids = {gpu["id"] for gpu in node["gpus"]} if node else set()
        quarantined = [
            {key: row[key] for key in ("nodeId", "instanceId", "jobId", "leaseId", "gpuId", "colocated")}
            | {"releaseAt": None}
            for row in self.gpu_quarantine.values() if row["nodeId"] == identity or row["gpuId"] in gpu_ids
        ]
        occupancy = {"quarantinedGpus": quarantined[:16], "quarantinedGpuCount": len(quarantined)}
        if node is None:
            credentials = self.node_credentials.get(identity) if self.manager.settings.remote else None
            if credentials or quarantined:
                return {"id": identity, "nodeId": identity, "instanceId": None, "state": "offline",
                        "hardware": None, "colocated": (credentials or quarantined[0])["colocated"],
                        "gpus": [], "leases": {}, "profiles": {}, "queue": [], "cpuAvailable": False,
                        **occupancy}
            raise PlanError("unknown_node", "Unknown Compute Node", status=404)
        return {
                **deepcopy({key: value for key, value in node.items() if key != "retiredInstances"}),
                "nodeId": identity,
            **occupancy,
                "colocated": self._colocated(node),
                "gpus": [
                    {**gpu, "history": self.telemetry.for_gpu(
                        node["id"], node["instanceId"], gpu["id"], self.clock()
                    )} for gpu in node["gpus"]
                ],
                "state": "online" if self.clock() <= node["lastSeen"] + 60 else "offline",
                "queue": [job["jobId"] for job in self.records.values() if job["status"] == "queued"],
        }

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

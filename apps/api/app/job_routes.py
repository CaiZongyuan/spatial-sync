"""Public job intent and separately authenticated Compute Node callbacks."""

import tempfile
import uuid
from functools import partial

import anyio
from fastapi import Query, Request
from fastapi.responses import Response

from apps.api.app.info_routes import actor_from_headers
from packages.info.plans import PlanError
from packages.training import checkpoints
from packages.training.checkpoint_format import MAX_BYTES
from packages.training.inputs import export_input, input_packages, store_artifacts
from packages.training.job_contract import (
    CleanupReceipt,
    ExchangeRegistration,
    NodeHeartbeat,
    NodeRegistration,
    ResumeJob,
    StartJob,
)


def install_job_routes(app, manager, role_for, views=None):
    jobs = manager.jobs
    app.state.jobs = jobs

    def operator(request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return actor_from_headers(request.headers)

    def node(request, identity=None, *, node_id=None):
        if not jobs.authenticated_node(request.headers, node_id):
            raise PlanError("node_auth_required", "This Compute Node's token is required", status=403)
        jobs._require_available()
        if identity:
            job = jobs.get(identity)
            owner = jobs.nodes.get(job["nodeId"])
            lease = owner and owner["leases"].get(job["resourceId"])
            if (
                not lease
                or job["nodeId"] != request.headers.get("x-node-id", "local")
                or lease["jobId"] != identity
                or lease["leaseId"] != request.headers.get("x-lease-id")
                or owner["instanceId"] != request.headers.get("x-node-instance")
                or jobs.dispatch_expired(owner, lease)
            ):
                raise PlanError("lease_mismatch", "Node does not own this dispatch", status=403)
            return job

    @app.post("/api/v1/jobs", status_code=202)
    async def start(body: StartJob, request: Request):
        actor = operator(request)
        return jobs.request(
            body.planId, body.seed, actor, body.requestId or uuid.uuid4().hex, body.planRevision,
            kind=body.kind, target_node=body.targetNodeId, checkpoint=body.checkpoint,
        )

    @app.get("/api/v1/jobs")
    async def listing(plan: str | None = None, status: str | None = None, kind: str | None = None):
        jobs._require_available()
        return {
            "jobs": [
                jobs.document(job)
                for job in jobs.records.values()
                if (plan is None or job["planId"] == plan)
                and (status is None or job["status"] == status)
                and (kind is None or job["kind"] == kind)
            ]
        }

    @app.get("/api/v1/jobs/{identity}")
    async def show(identity: str):
        jobs.reconcile()
        jobs._save()
        return jobs.document(jobs.get(identity))

    @app.post("/api/v1/jobs/{identity}/cancel")
    async def cancel(identity: str, request: Request):
        operator(request)
        return jobs.cancel(identity)

    @app.post("/api/v1/jobs/{identity}/resume", status_code=202)
    async def resume(identity: str, body: ResumeJob, request: Request):
        operator(request)
        return jobs.resume(identity, body.targetNodeId)

    @app.get("/api/v1/jobs/{identity}/logs")
    async def logs(identity: str, after: int = Query(0, ge=0), timeoutSeconds: float = Query(0, ge=0, le=30)):
        return await jobs.logs(identity, after, timeoutSeconds)

    @app.get("/api/v1/nodes")
    async def nodes():
        return {"nodes": jobs.list_nodes()}

    @app.post("/api/v1/nodes/registrations", status_code=201)
    async def issue_registration(body: NodeRegistration, request: Request, response: Response):
        operator(request)
        response.headers["Cache-Control"] = "no-store"
        return jobs.issue_registration(body)

    @app.post("/api/v1/nodes/register", status_code=201)
    async def register(body: ExchangeRegistration, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return jobs.register_node(body.code)

    @app.post("/api/v1/nodes/cleanup", status_code=204)
    async def cleanup(body: CleanupReceipt, request: Request):
        jobs.clean_gpu(body, request.headers.get("x-node-cleanup-token", ""))

    @app.get("/api/v1/nodes/{node_id}")
    async def show_node(node_id: str):
        return jobs.node_document(node_id)

    @app.delete("/api/v1/nodes/{node_id}", status_code=204)
    async def revoke_node(node_id: str, request: Request):
        operator(request)
        jobs.revoke_node(node_id)

    @app.post("/api/v1/nodes/{node_id}/heartbeat")
    async def heartbeat(node_id: str, body: NodeHeartbeat, request: Request):
        node(request, node_id=node_id)
        instance = request.headers.get("x-node-instance")
        if node_id != "local" and instance != body.instanceId:
            raise PlanError("node_owner_conflict", "Heartbeat instance must match the caller", status=403)
        return jobs.heartbeat(body, node_id, owner_instance=instance)

    @app.get("/api/v1/jobs/{identity}/inputs")
    async def inputs(identity: str, request: Request):
        job = node(request, identity)
        lease = jobs.nodes[job["nodeId"]]["leases"][job["resourceId"]]
        value = {
            "schemaVersion": 2 if lease.get("checkpointVersion") else 1,
            "job": jobs.document(job),
            "plan": job["plan"],
            "trainerVersions": lease.get("trainerVersions"),
            **({"runtimeIdentity": lease["runtimeIdentity"]} if lease.get("runtimeIdentity") else {}),
            **({"compilerHash": lease["compilerHash"]} if lease.get("compilerHash") else {}),
            "nativeObservationProfile": None,
            "packages": await anyio.to_thread.run_sync(input_packages, manager, job),
        }
        if lease.get("checkpointVersion"):
            value["checkpointIdentity"] = checkpoints.identity(job, lease)
            if source := lease.get("resumeCheckpoint"):
                value["resumeCheckpoint"] = source | {
                    "url": f"/api/v1/jobs/{identity}/inputs/checkpoint/{source['sha256']}",
                }
        if job["kind"] in ("training", "checkpoint_finalize"):
            value["nativeObservationProfile"] = manager.policy_evidence.native_profiles.for_task(
                job["taskContentHash"], job["plan"]["algorithm"]["version"]
            )
            if views is not None and job["kind"] == "training":
                value["view"] = await views.inputs_hint(job)
        node(request, identity)
        return value

    @app.get("/api/v1/jobs/{identity}/inputs/packages/{index}")
    async def package(identity: str, index: int, request: Request):
        job = node(request, identity)
        data = await anyio.to_thread.run_sync(export_input, manager, job, index)
        node(request, identity)
        return Response(data, media_type="application/x-tar")

    @app.get("/api/v1/jobs/{identity}/inputs/checkpoint/{sha256}")
    async def checkpoint_input(identity: str, sha256: str, request: Request):
        job = node(request, identity)
        lease = jobs.nodes[job["nodeId"]]["leases"][job["resourceId"]]
        source = lease.get("resumeCheckpoint")
        if not source or source["sha256"] != sha256:
            raise PlanError("unknown_checkpoint", "Dispatch does not own the Checkpoint", status=404)
        raw = await anyio.to_thread.run_sync(checkpoints.load, manager, source)
        node(request, identity)
        return Response(raw, media_type="application/x-tar")

    @app.post("/api/v1/jobs/{identity}/checkpoints", status_code=201)
    async def checkpoint_upload(identity: str, request: Request):
        job = node(request, identity)
        lease = jobs.nodes[job["nodeId"]]["leases"][job["resourceId"]]
        if job["kind"] != "training" or not lease.get("checkpointVersion"):
            raise PlanError("checkpoint_unsupported", "This dispatch cannot publish checkpoints", status=422)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_BYTES:
                raise PlanError("checkpoint_invalid", "Checkpoint exceeds 128 MiB", status=422)
            data.extend(chunk)
        raw = bytes(data)
        node(request, identity)
        descriptor = await anyio.to_thread.run_sync(checkpoints.validate, raw, job, lease)
        node(request, identity)
        retained = job.setdefault("checkpoints", [])
        if retained and descriptor["iteration"] <= retained[-1]["iteration"]:
            if descriptor in retained:
                return descriptor
            raise PlanError("checkpoint_conflict", "Checkpoint does not advance committed iterations")
        await anyio.to_thread.run_sync(checkpoints.persist, manager, descriptor, raw)
        with manager._resource_lock:
            node(request, identity)
            if retained and descriptor["iteration"] <= retained[-1]["iteration"]:
                if descriptor in retained:
                    return descriptor
                raise PlanError("checkpoint_conflict", "Checkpoint does not advance committed iterations")
            retained.append(descriptor)
            attempt = jobs._attempt(job, lease["leaseId"])
            attempt["endIteration"] = max(attempt["endIteration"], descriptor["iteration"])
            jobs._save()
        return descriptor

    @app.post("/api/v1/jobs/{identity}/artifacts")
    async def artifacts(identity: str, request: Request):
        job = node(request, identity)
        if job["kind"] not in ("training", "checkpoint_finalize"):
            raise PlanError(
                "artifacts_unsupported", "Validation submits timing in its terminal report", status=422
            )
        lease = jobs.nodes[job["nodeId"]]["leases"][job["resourceId"]]
        with tempfile.TemporaryFile() as stream:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 128 * 1024 * 1024:
                    raise PlanError("artifacts_invalid", "Artifacts exceed 128 MiB", status=422)
                stream.write(chunk)
            stream.seek(0)
            node(request, identity)
            portable = bool(lease.get("checkpointVersion"))
            descriptor = jobs.resume_source(job) if portable else None
            records, versions = await anyio.to_thread.run_sync(partial(
                store_artifacts, manager, dict(job), stream, lambda: node(request, identity),
                lease.get("trainerVersions"), portable=portable, descriptor=descriptor,
            ))
            node(request, identity)
            if portable:
                job["artifactLeaseId"] = lease["leaseId"]
            job.update(artifacts=records, versions=versions)
            jobs._save()
        return {"artifacts": records, "versions": versions}

    if views is not None:
        from apps.api.app.training_view_routes import install_training_view_routes

        install_training_view_routes(app, manager, views, role_for, node)

"""Public job intent and separately authenticated local-node callbacks."""

import tempfile
import uuid

import anyio
from fastapi import Query, Request
from fastapi.responses import Response

from apps.api.app.info_routes import actor_from_headers
from packages.info.plans import PlanError
from packages.training.inputs import export_input, input_packages, store_artifacts
from packages.training.job_contract import NodeHeartbeat, StartJob


def install_job_routes(app, manager, role_for):
    jobs = manager.jobs
    app.state.jobs = jobs

    def operator(request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return actor_from_headers(request.headers)

    def node(request, identity=None):
        if not jobs.authenticated_node(request.headers):
            raise PlanError("node_auth_required", "A local node token is required", status=403)
        if identity:
            job = jobs.get(identity)
            owner = jobs.nodes.get(job["nodeId"])
            lease = owner and owner["leases"].get(job["resourceId"])
            if (
                not lease
                or lease["jobId"] != identity
                or lease["leaseId"] != request.headers.get("x-lease-id")
                or owner["instanceId"] != request.headers.get("x-node-instance")
            ):
                raise PlanError("lease_mismatch", "Node does not own this dispatch", status=403)
            return job

    @app.post("/api/v1/jobs", status_code=202)
    async def start(body: StartJob, request: Request):
        actor = operator(request)
        async with manager._create_lock:
            return jobs.request(
                body.planId, body.seed, actor, body.requestId or uuid.uuid4().hex, body.planRevision
            )

    @app.get("/api/v1/jobs")
    async def listing(plan: str | None = None, status: str | None = None, kind: str | None = None):
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
        async with manager._create_lock:
            jobs.reconcile()
            jobs._save()
        return jobs.document(jobs.get(identity))

    @app.post("/api/v1/jobs/{identity}/cancel")
    async def cancel(identity: str, request: Request):
        operator(request)
        async with manager._create_lock:
            return jobs.cancel(identity)

    @app.get("/api/v1/jobs/{identity}/logs")
    async def logs(identity: str, after: int = Query(0, ge=0), timeoutSeconds: float = Query(0, ge=0, le=30)):
        return await jobs.logs(identity, after, timeoutSeconds)

    @app.get("/api/v1/nodes")
    async def nodes():
        return {"nodes": jobs.list_nodes()}

    @app.post("/api/v1/nodes/local/heartbeat")
    async def heartbeat(body: NodeHeartbeat, request: Request):
        node(request)
        async with manager._create_lock:
            return jobs.heartbeat(body)

    @app.get("/api/v1/jobs/{identity}/inputs")
    async def inputs(identity: str, request: Request):
        job = node(request, identity)
        return {
            "schemaVersion": 1,
            "job": jobs.document(job),
            "plan": job["plan"],
            "packages": await anyio.to_thread.run_sync(input_packages, manager, job),
        }

    @app.get("/api/v1/jobs/{identity}/inputs/packages/{index}")
    async def package(identity: str, index: int, request: Request):
        job = node(request, identity)
        data = await anyio.to_thread.run_sync(export_input, manager, job, index)
        return Response(data, media_type="application/x-tar")

    @app.post("/api/v1/jobs/{identity}/artifacts")
    async def artifacts(identity: str, request: Request):
        job = node(request, identity)
        with tempfile.TemporaryFile() as stream:
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 128 * 1024 * 1024:
                    raise PlanError("artifacts_invalid", "Artifacts exceed 128 MiB", status=422)
                stream.write(chunk)
            stream.seek(0)
            records, versions = await anyio.to_thread.run_sync(store_artifacts, manager, job, stream)
            async with manager._create_lock:
                node(request, identity)
                job.update(artifacts=records, versions=versions)
                jobs._save()
        return {"artifacts": records, "versions": versions}

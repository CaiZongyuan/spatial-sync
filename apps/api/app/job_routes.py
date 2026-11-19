"""Public job intent and separately authenticated Compute Node callbacks."""

import tempfile
import uuid

import anyio
from fastapi import Query, Request
from fastapi.responses import Response

from apps.api.app.info_routes import actor_from_headers
from packages.info.plans import PlanError
from packages.training.inputs import export_input, input_packages, store_artifacts
from packages.training.job_contract import (
    CleanupReceipt,
    ExchangeRegistration,
    NodeHeartbeat,
    NodeRegistration,
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
            kind=body.kind, target_node=body.targetNodeId,
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
        jobs.reconcile()
        jobs._save()
        return jobs.document(jobs.get(identity))

    @app.post("/api/v1/jobs/{identity}/cancel")
    async def cancel(identity: str, request: Request):
        operator(request)
        return jobs.cancel(identity)

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
        if node_id != "local" and request.headers.get("x-node-instance") != body.instanceId:
            raise PlanError("node_owner_conflict", "Heartbeat instance must match the caller", status=403)
        return jobs.heartbeat(body, node_id)

    @app.get("/api/v1/jobs/{identity}/inputs")
    async def inputs(identity: str, request: Request):
        job = node(request, identity)
        lease = jobs.nodes[job["nodeId"]]["leases"][job["resourceId"]]
        value = {
            "schemaVersion": 1,
            "job": jobs.document(job),
            "plan": job["plan"],
            "trainerVersions": lease.get("trainerVersions"),
            **({"compilerHash": lease["compilerHash"]} if lease.get("compilerHash") else {}),
            "nativeObservationProfile": None,
            "packages": await anyio.to_thread.run_sync(input_packages, manager, job),
        }
        if job["kind"] == "training":
            value["nativeObservationProfile"] = manager.policy_evidence.native_profiles.for_task(
                job["taskContentHash"], job["plan"]["algorithm"]["version"]
            )
            if views is not None:
                value["view"] = await views.inputs_hint(job)
        node(request, identity)
        return value

    @app.get("/api/v1/jobs/{identity}/inputs/packages/{index}")
    async def package(identity: str, index: int, request: Request):
        job = node(request, identity)
        data = await anyio.to_thread.run_sync(export_input, manager, job, index)
        node(request, identity)
        return Response(data, media_type="application/x-tar")

    @app.post("/api/v1/jobs/{identity}/artifacts")
    async def artifacts(identity: str, request: Request):
        job = node(request, identity)
        if job["kind"] != "training":
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
            records, versions = await anyio.to_thread.run_sync(
                store_artifacts, manager, job, stream, lambda: node(request, identity),
                lease.get("trainerVersions"),
            )
            node(request, identity)
            job.update(artifacts=records, versions=versions)
            jobs._save()
        return {"artifacts": records, "versions": versions}

    if views is not None:
        from apps.api.app.training_view_routes import install_training_view_routes

        install_training_view_routes(app, manager, views, role_for, node)

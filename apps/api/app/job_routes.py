"""Public job intent and separately authenticated Compute Node callbacks."""

import tempfile
import uuid
from functools import partial

import anyio
from fastapi import Query, Request
from fastapi.responses import Response

from apps.api.app.info_routes import actor_from_headers
from packages.info.plans import PlanError
from packages.motion.contract import MAX_CLIP_BYTES, MAX_VIDEO_BYTES, VideoLicense
from packages.motion.library import MotionLibrary
from packages.training import checkpoints
from packages.training.checkpoint_format import MAX_BYTES
from packages.training.finalization import finalization_profile
from packages.training.inputs import export_input, input_packages, store_artifacts
from packages.training.job_contract import (
    CleanupReceipt,
    ExchangeRegistration,
    NodeHeartbeat,
    NodeRegistration,
    ResumeJob,
    StartJob,
)
from packages.training.jobs import TERMINAL


def install_job_routes(app, manager, role_for, views=None):
    jobs = manager.jobs
    motion = manager.motion = MotionLibrary(manager.library_assets.roots["user"], jobs)
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
            motion_video=body.motionVideo,
        )

    @app.post("/api/v1/library/motion-videos", status_code=201)
    async def upload_motion_video(
        request: Request, license: str, attribution: str, sourceUrl: str | None = None,
    ):
        from pathlib import Path

        operator(request)
        try:
            rights = VideoLicense(license=license, attribution=attribution, sourceUrl=sourceUrl)
        except ValueError as exc:
            raise PlanError(
                "motion_license_invalid", "Provide a video license and attribution", status=422
            ) from exc
        with tempfile.TemporaryDirectory(prefix="motion-upload-") as directory:
            path = Path(directory) / "video"
            size = 0
            with path.open("wb") as stream:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_VIDEO_BYTES:
                        raise PlanError("motion_video_too_large", "Video upload exceeds 16 MiB", status=413)
                    stream.write(chunk)
            return await anyio.to_thread.run_sync(motion.publish_video, path, rights)

    @app.get("/api/v1/library/motion-videos")
    async def motion_videos():
        return {"videos": await anyio.to_thread.run_sync(motion.videos)}

    @app.get("/api/v1/library/motion-videos/{digest}")
    async def motion_video(digest: str):
        return await anyio.to_thread.run_sync(motion.video, f"motion-video/{digest}")

    @app.get("/api/v1/library/motion-clips")
    async def motion_clips():
        return {"clips": await anyio.to_thread.run_sync(motion.clips)}

    @app.get("/api/v1/library/motion-clips/{digest}/{revision}")
    async def motion_clip(digest: str, revision: str):
        return await anyio.to_thread.run_sync(motion.clip, f"motion-clip/{digest}@{revision}")

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
        if job["kind"] == "motion_preprocess":
            source = await anyio.to_thread.run_sync(motion.video, job["motionVideo"])
            node(request, identity)
            return {
                "schemaVersion": 1, "job": jobs.document(job), "poseRuntime": lease["poseRuntime"],
                "video": source | {"url": f"/api/v1/jobs/{identity}/inputs/video"},
            }
        task = manager.tasks.show(job["task"])
        manager.tasks.require_execution(task)
        lease = jobs.nodes[job["nodeId"]]["leases"][job["resourceId"]]
        profile = None
        if job["kind"] == "checkpoint_finalize":
            profile = finalization_profile(manager, job, lease.get("runtimeIdentity", {}), task=task)
        elif task["task"].get("declarative") is not None:
            from packages.policies.qualification import declarative_profile

            profile = declarative_profile(
                task, manager.tasks.observation(task["id"]), job, lease.get("runtimeIdentity"),
            ).model_dump(mode="json")
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
            value["nativeObservationProfile"] = (
                profile if profile is not None
                else manager.policy_evidence.native_profiles.for_task(
                    job["taskContentHash"], job["plan"]["algorithm"]["version"],
                )
            )
            if views is not None and job["kind"] == "training":
                value["view"] = await views.inputs_hint(job)
        node(request, identity)
        return value

    @app.get("/api/v1/jobs/{identity}/inputs/packages/{index}")
    async def package(identity: str, index: int, request: Request):
        job = node(request, identity)
        if job["kind"] == "motion_preprocess":
            raise PlanError("unknown_input_package", "Motion Job inputs contain a video", status=404)
        manager.tasks.require_execution(manager.tasks.show(job["task"]))
        data = await anyio.to_thread.run_sync(export_input, manager, job, index)
        node(request, identity)
        return Response(data, media_type="application/x-tar")

    @app.get("/api/v1/jobs/{identity}/inputs/video")
    async def job_video(identity: str, request: Request):
        job = node(request, identity)
        if job["kind"] != "motion_preprocess":
            raise PlanError("unknown_motion_video", "This Job has no video input", status=404)
        metadata = await anyio.to_thread.run_sync(motion.video, job["motionVideo"])
        raw = await anyio.to_thread.run_sync(motion.video_bytes, metadata)
        node(request, identity)
        return Response(raw, media_type="application/octet-stream")

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
        if job["kind"] == "motion_preprocess":
            return await motion_artifacts(identity, request, job)
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
                runtime_identity=lease.get("runtimeIdentity"),
            ))
            node(request, identity)
            if portable:
                job["artifactLeaseId"] = lease["leaseId"]
            job.update(artifacts=records, versions=versions)
            jobs._save()
        return {"artifacts": records, "versions": versions}

    async def motion_artifacts(identity, request, job):
        lease_id = job["leaseId"]
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_CLIP_BYTES:
                raise PlanError("motion_artifact_too_large", "Motion artifact exceeds 4 MiB", status=413)
            raw.extend(chunk)
        source = await anyio.to_thread.run_sync(motion.video, job["motionVideo"])
        clip = await anyio.to_thread.run_sync(motion.prepare_clip, source, bytes(raw))
        job = node(request, identity)
        if job["leaseId"] != lease_id or job["cancelRequested"] or job["status"] in TERMINAL:
            raise PlanError("lease_mismatch", "The motion dispatch cannot accept artifacts", status=403)
        descriptor = motion.store_clip(clip, existing=job["motionClip"])
        artifact = {"path": "motion-clip.json", "size": descriptor["bytes"], "sha256": descriptor["sha256"]}
        job.update(motionClip=descriptor, artifacts=[artifact], artifactLeaseId=lease_id)
        jobs._save()
        return {"motionClip": descriptor}

    if views is not None:
        from apps.api.app.training_view_routes import install_training_view_routes

        install_training_view_routes(app, manager, views, role_for, node)

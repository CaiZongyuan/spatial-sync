"""Training data admission checks platform provenance before reading recording bytes."""

import re

import anyio
from fastapi import Request
from fastapi.responses import FileResponse

from apps.api.app.info_routes import actor_from_headers
from apps.api.app.recordings import RECORDING_ID
from packages.info.plans import PlanError
from packages.library.archive import relative_path
from packages.training.demonstration_evidence import recording_evidence
from packages.training.demonstrations import Demonstrations
from packages.training.schema import Schema, TaskRef, Text


class AddDemonstration(Schema):
    recordingId: Text
    task: TaskRef | None = None


class DemonstrationService:
    def __init__(self, manager):
        self.manager = manager
        self.library = getattr(manager, "demonstrations", None) or Demonstrations(manager.library_assets)

    def _associations(self, identifier):
        matches = []
        session = self.manager.sessions.get(identifier)
        for record in tuple(self.manager.evaluations.store.records.values()):
            episodes = [episode for episode in record.get("episodes") or []
                        if episode.get("recordingId") == identifier]
            listed = (identifier in (record.get("result") or {}).get("recordings", [])
                      or identifier in (record.get("recordings") or [])
                      or any(case.get("recordingId") == identifier for case in record.get("caseRuns") or []))
            if episodes or listed or session and session.evaluation_id == record["evaluationId"]:
                matches.append((record, episodes))
        # Classify all associations first: a train receipt cannot mask a known held-out source.
        if any(record.get("split") != "train" for record, _ in matches):
            raise PlanError("demo_private_source", "Held-out or unknown-split data cannot enter training")
        if any(record.get("layer") == "agent" for record, _ in matches):
            raise PlanError("demo_source_unsupported", "Agent scores do not prove physical Task success")
        for _, episodes in matches:
            if any(episode.get("success") is not True for episode in episodes):
                raise PlanError("demo_not_successful", "The platform episode is not a known success")
        return matches

    def add(self, recording_id, task_id, actor):
        if not RECORDING_ID.fullmatch(recording_id):
            raise PlanError("unknown_recording", "Unknown recording", status=404)
        associations = self._associations(recording_id)  # Private sources cannot be laundered via --task.
        existing = self.library.find(recording_id)
        if existing is not None:
            if task_id not in (None, existing["task"]):
                raise PlanError("demo_task_conflict", "This recording already has a fixed source Task")
            return self.library.retry(existing)
        inferred = set()
        for record, _ in associations:
            context = record.get("notebookContext") or {}
            reference = context.get("plan", {}).get("task")
            if reference is None and record.get("planId") and record.get("planRevision"):
                stored = self.manager.training_plans.get(record["planId"], record["planRevision"])
                if getattr(stored.plan, "layer", None) == "agent":
                    raise PlanError("demo_source_unsupported", "Agent Plans have no physical Task episode")
                reference = getattr(stored.plan, "task", None)
            if reference is not None:
                inferred.add(reference)
        if len(inferred) > 1 or task_id is not None and inferred and task_id not in inferred:
            raise PlanError("demo_task_conflict", "Recording has another fixed source Task")
        task_id = task_id or next(iter(inferred), None)
        if task_id is None:
            raise PlanError(
                "demo_task_required", "Select --task task/name@revision for this recording", status=422
            )
        task = self.manager.tasks.show(task_id)
        source = self.manager.recordings.resolve(recording_id, "recording.json").parent
        evidence = recording_evidence(source, task)
        if evidence["recordingId"] != recording_id:
            raise PlanError("demo_recording_invalid", "Recording identity and storage disagree", status=422)
        for record, episodes in associations:
            for episode in episodes:
                if episode.get("invocationId") != evidence["invocationId"]:
                    raise PlanError("demo_episode_mismatch", "Recording and platform episode disagree")
            context = record.get("notebookContext") or {}
            if context.get("taskContentHash", task["contentHash"]) != task["contentHash"]:
                raise PlanError("demo_task_conflict", "Fixed source Task content changed")
            original, completed = context.get("implementations"), record.get("notebookExecution")
            if (
                isinstance(original, dict) and original == completed
                and isinstance(original.get("candidate"), str)
                and re.fullmatch(r"[a-f0-9]{64}", original.get("candidate", ""))
            ):
                evidence["sourceProgram"].update(
                    implementationHash=original["candidate"],
                    implementationCoverage="programs_code_at_evaluation",
                )
            evidence["successEvidence"]["evaluationId"] = record["evaluationId"]
        return self.library.add(evidence, source, actor)


def install_demonstration_routes(app, service, role_for):
    @app.post("/api/v1/demos", status_code=201)
    async def add(body: AddDemonstration, request: Request):
        if role_for(request.headers) != "operator":
            raise PlanError("not_permitted", "Operator permission is required", status=403)
        return await anyio.to_thread.run_sync(
            service.add, body.recordingId, body.task, actor_from_headers(request.headers)
        )

    @app.get("/api/v1/demos")
    async def listing(task: TaskRef | None = None):
        return {"demos": await anyio.to_thread.run_sync(service.library.list, task)}

    @app.get("/api/v1/demo-sets/{name}/{revision}")
    async def show_set(name: str, revision: str):
        return await anyio.to_thread.run_sync(service.library.show, f"demoset/{name}@{revision}")

    @app.get("/api/v1/demos/recordings/{identifier}/{relative:path}")
    async def recording(identifier: str, relative: str):
        def resolve():
            entry = service.library.find(identifier)
            if entry is None:
                raise PlanError("unknown_demo_recording", "Unknown Demonstration recording", status=404)
            root = service.library.recording(identifier, entry["recordingContentHash"])
            try:
                file = root / relative_path(relative)
            except ValueError:
                raise PlanError("unknown_demo_recording", "Unknown Demonstration file", status=404) from None
            if not file.is_file():
                raise PlanError("unknown_demo_recording", "Unknown Demonstration file", status=404)
            return file

        return FileResponse(await anyio.to_thread.run_sync(resolve))

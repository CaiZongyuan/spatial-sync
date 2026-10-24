"""Current training approval is required for evaluation of exact immutable script material."""

import json
from pathlib import Path

from pydantic import Field

from packages.info.plans import PlanError
from packages.library.assets import AssetLibrary
from packages.library.common import LibraryError
from packages.library.scenes import SceneLibrary
from packages.library.tasks import TaskLibrary
from packages.programs.metadata import Hash, ProgramIntent, Schema, Text


class ProgramApproval(Schema):
    planId: Text
    planRevision: int = Field(ge=1)
    approvalId: Text
    program: ProgramIntent
    task: Text
    taskContentHash: Hash


def require_program_approval(manager, requested, suite, scene, *, plan_id, plan_revision=None):
    """Read only metadata. Caller owns the existing evaluation admission lock before effects."""
    if plan_id is None:
        raise PlanError("program_approval_required", "Select a human-approved code Plan", status=409)
    stored = manager.training_plans.get(plan_id)
    plan = stored.plan
    if (
        manager.training_plans.document(stored)["status"] != "approved"
        or plan_revision not in (None, plan.revision)
    ):
        raise PlanError("program_not_approved", "Select the current approved code Plan revision", status=409)
    if plan.program is None or plan.program.id != requested:
        raise PlanError(
            "program_plan_mismatch", "Plan does not approve this exact program version", status=409
        )
    approval = manager.approvals.records.get(stored.approvalId)
    if (
        approval is None
        or approval.subject != "plan"
        or approval.subjectId != plan.id
        or approval.details.get("revision") != plan.revision
        or approval.details.get("plan", {}).get("program") != plan.program.model_dump(mode="json")
    ):
        raise PlanError("program_not_approved", "Exact code approval evidence is unavailable", status=409)
    suite.verify()
    if (
        suite.metadata["layer"] != "embodied"
        or suite.metadata["source"] != "newton"
        or "newton" not in scene.metadata["sources"]
    ):
        raise PlanError("program_source_unsupported", "Scripts require the owned Newton Runner", status=422)
    task = manager.tasks.show(plan.task)
    metadata = task["task"]
    program = manager.programs.show(requested)
    if (
        program["contentHash"] != plan.program.contentHash
        or metadata["implements"] != program["metadata"]["implements"]
        or metadata["robot"] != suite.task["robot"]
        or metadata["implements"] != suite.task["implements"]
        or metadata["scene"] != scene.id
        or task["sceneContentHash"] != scene.content_hash
        or plan.evaluation.scene != scene.id
        or suite.metadata["scene"] != scene.id
        or suite.metadata["sceneContentHash"] != scene.content_hash
    ):
        raise PlanError(
            "program_plan_mismatch", "Frozen code Plan, Task and evaluation bindings differ", status=409
        )
    return ProgramApproval(
        planId=plan.id,
        planRevision=plan.revision,
        approvalId=approval.approvalId,
        program=plan.program,
        task=plan.task,
        taskContentHash=task["contentHash"],
    ), program


def verify_program_approval(manager, proof, suite, scene):
    """A queued descriptor cannot borrow a revised, revoked or different code approval."""
    proof = ProgramApproval.model_validate(proof) if isinstance(proof, dict) else proof
    actual, _ = require_program_approval(
        manager, proof.program.id, suite, scene, plan_id=proof.planId, plan_revision=proof.planRevision
    )
    if actual != proof:
        raise PlanError("program_approval_changed", "Frozen code approval differs from admission", status=409)
    return proof


def require_runtime_approval(proof, state_root):
    """Runner rechecks evaluation code approval. Committed deployments use their own proof."""
    proof = ProgramApproval.model_validate(proof) if isinstance(proof, dict) else proof
    root = Path(state_root)
    try:
        records = json.loads((root / "training-plans.json").read_bytes())
        current = max(
            (record for record in records if record["plan"]["id"] == proof.planId),
            key=lambda record: record["plan"]["revision"],
        )
        approval = None
        for line in (root / "approvals.jsonl").read_bytes().splitlines(keepends=True):
            if not line.endswith(b"\n"):
                break
            value = json.loads(line)
            if value["approvalId"] == proof.approvalId:
                approval = value
        plan = current["plan"]
        if (
            current["approvalId"] != proof.approvalId
            or plan["revision"] != proof.planRevision
            or plan["task"] != proof.task
            or plan.get("program") != proof.program.model_dump(mode="json")
            or approval is None
            or approval["status"] != "approved"
            or approval["subject"] != "plan"
            or approval["subjectId"] != proof.planId
            or approval["details"].get("revision") != proof.planRevision
            or approval["details"].get("plan") != plan
            or not approval["decisions"]
            or approval["decisions"][-1]["decision"] != "approve"
            or approval["decisions"][-1]["actor"]["kind"] != "user"
        ):
            raise ValueError("Exact current human code approval differs")
        assets = AssetLibrary(root)
        scenes = SceneLibrary(user_root=assets.roots["user"], state_root=root)
        task = TaskLibrary(assets, lambda: scenes).show(proof.task)
        if task["contentHash"] != proof.taskContentHash:
            raise ValueError("Frozen approved Task material differs")
    except (OSError, ValueError, TypeError, KeyError, LibraryError) as exc:
        raise PlanError(
            "program_not_approved", "Current human code approval is unavailable", status=409
        ) from exc
    return proof

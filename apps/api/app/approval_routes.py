"""Human decisions over generic approval subjects; invocation is the first consumer."""

import base64
import json
import math
from typing import Literal

import anyio
from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.info_routes import actor_from_headers
from packages.info.approvals import ApprovalConflict, Subject
from packages.info.context import information_revision
from packages.training.dispatch import training_store


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny", "revoke"]
    note: str = Field(default="", max_length=4000)


def install_approval_routes(app, manager, role_for):
    store = manager.approvals

    def target(record):
        live = getattr(manager, "live", None)
        return (live if live is not None and live.session_id == record.sessionId
                else manager.sessions.get(record.sessionId))

    def document(record):
        if record.subject == "deletion":
            record = manager.library_deletions.describe(record)
        training = record.subject == "plan" and record.details.get("plan", {}).get("kind") == "training"
        session = target(record)
        invocation = (
            session.pipeline.table.get(record.subjectId)
            if session and session.pipeline and record.subject == "invocation"
            else None
        )
        available = bool(session and session.state in ("RUNNING", "PAUSED") and session.source)
        plan = manager.plans.records.get(record.subjectId) if record.subject == "plan" else None
        plan_current = bool(
            plan and session and plan.approvalId == record.approvalId
            and (plan.sceneRevision, plan.epoch) == (information_revision(session), session.epoch)
        )
        plan_can_revoke = bool(plan_current
                               and all(step.status == "pending" for step in plan.plan.steps))
        promotion = record.details.get("controllerPromotionId")
        promotion_revoke = bool(promotion and manager.controller_promotions.can_revoke(record))
        return {
            **record.model_dump(mode="json"),
            "canRevoke": promotion_revoke or (
                          record.subject == "job" and manager.jobs.can_revoke(record)) or (
                          training
                          and training_store(manager, identifier=record.subjectId).can_revoke(record)) or (
                          record.subject == "deletion" and record.status in ("approved", "denied")
                          and record.details.get("execution", {}).get("status")
                          in ("not_started", "blocked", "failed")) or available
            and (
                (record.status == "denied" and (record.subject != "plan" or plan_current))
                or (record.status == "approved" and plan_can_revoke)
                or (
                    record.status == "approved"
                    and invocation is not None
                    and invocation.status == "accepted"
                    and invocation.startedSimTime is None
                )
            ),
            "invocation": invocation.model_dump(mode="json") if invocation else None,
        }

    def get_record(approval_id):
        from packages.info.evaluation_privacy import private_recording

        record = store.records.get(approval_id)
        if record is None or record.sessionId and private_recording(manager, record.sessionId):
            raise HTTPException(404, {"code": "unknown_approval"})
        return record

    @app.get("/api/v1/approvals")
    async def approvals(
        request: Request,
        status: str | None = None,
        subject: Subject | None = None,
        sessionId: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        view: Literal["full", "summary", "page"] = "full", cursor: str | None = None,
        include: str = "",
        layer: Literal["agent", "embodied"] | None = None, agentId: str | None = None,
    ):
        from packages.info.evaluation_privacy import private_recording

        def page():
            records = [record for record in list(store.records.values())
                       if not record.sessionId or not private_recording(manager, record.sessionId)]
            result = {"approvals": [], "pendingCount": sum(record.status == "pending" for record in records),
                      "role": role_for(request.headers)}
            if view == "summary":
                return result
            included = include.split(",") if include else []
            if len(included) > 20 or any(len(value) > 100 for value in included):
                raise HTTPException(422, {"code": "invalid_approval_page"})
            def key(record):
                return (record.status == "pending", record.createdAtMs, record.approvalId)
            def relevant(record):
                plan = record.details.get("plan", {})
                agent = plan.get("layer") == "agent" or bool(record.details.get("agentPromotionId"))
                if layer == "agent" and not agent or layer == "embodied" and agent:
                    return False
                return agentId is None or (plan.get("agentId") or record.details.get("agentId")) == agentId
            selected = sorted((record for record in records if (
                (status is None or record.status == status
                 or status == "processed" and record.status != "pending")
                and (subject is None or record.subject == subject)
                and (sessionId is None or record.sessionId == sessionId)
                and relevant(record))), key=key, reverse=True)
            total = len(selected)
            query = [status, subject, sessionId, layer, agentId]
            if cursor:
                try:
                    if len(cursor) > 2048:
                        raise ValueError()
                    value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                    after = value["after"]
                    if (value["query"] != query or len(after) != 3 or type(after[0]) is not bool
                            or not isinstance(after[1], (int, float)) or not math.isfinite(after[1])
                            or not isinstance(after[2], str)):
                        raise ValueError()
                except (ValueError, KeyError, TypeError, UnicodeError):
                    raise HTTPException(422, {"code": "invalid_approval_cursor"}) from None
                selected = [record for record in selected if key(record) < tuple(after)]
            current = selected[:limit]
            result["approvals"] = [document(record) for record in current]
            if view == "page":
                next_cursor = base64.urlsafe_b64encode(json.dumps({
                    "query": query, "after": key(current[-1]),
                }).encode()).decode() if len(selected) > limit else None
                result["pagination"] = {"total": total, "nextCursor": next_cursor}
                result["recentApprovals"] = [document(record) for record in records
                                             if record.approvalId in included]
            return result
        return await anyio.to_thread.run_sync(page)

    @app.get("/api/v1/approvals/{approval_id}")
    async def approval(approval_id: str):
        return document(get_record(approval_id))

    @app.post("/api/v1/approvals/{approval_id}/decision")
    async def decide(approval_id: str, body: ApprovalDecisionRequest, request: Request):
        actor, role = actor_from_headers(request.headers), role_for(request.headers)
        if actor.kind == "agent":
            raise HTTPException(403, {"code": "approval_requires_human"})
        if role != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        record = get_record(approval_id)
        if record.details.get("controllerPromotionId"):
            try:
                return await anyio.to_thread.run_sync(
                    manager.controller_promotions.decide, record, body.decision, actor, body.note
                )
            except ApprovalConflict as exc:
                raise HTTPException(409, {"code": "approval_conflict", "message": str(exc)}) from exc
        if record.details.get("agentPromotionId"):
            from packages.agent_rsi.promotions import decide

            try:
                return await anyio.to_thread.run_sync(
                    decide, manager.agent_rsi, record, body.decision, actor, body.note
                )
            except ApprovalConflict as exc:
                raise HTTPException(409, {"code": "approval_conflict", "message": str(exc)}) from exc
        if record.subject == "job":
            try:
                manager.jobs.decide(record, body.decision, actor, body.note)
            except ApprovalConflict as exc:
                raise HTTPException(409, {"code": "approval_conflict", "message": str(exc)}) from exc
            return document(get_record(approval_id))
        if record.subject == "plan" and record.details.get("plan", {}).get("kind") == "training":
            try:
                training_store(manager, identifier=record.subjectId).decide(
                    record, body.decision, actor, body.note
                )
            except ApprovalConflict as exc:
                raise HTTPException(409, {"code": "approval_conflict", "message": str(exc)}) from exc
            return document(get_record(approval_id))
        if record.subject == "deletion":
            try:
                await anyio.to_thread.run_sync(
                    manager.library_deletions.decide, approval_id, body.decision, actor, body.note,
                )
            except ApprovalConflict as exc:
                raise HTTPException(409, {"code": "approval_conflict", "message": str(exc)}) from exc
            return document(get_record(approval_id))
        session = target(record)
        simulation = manager.sessions.get(record.sessionId)
        if simulation is not None:
            simulation.require_interactive()
        if (record.subject not in ("invocation", "plan") or not session
                or session.state not in ("RUNNING", "PAUSED")):
            raise HTTPException(409, {"code": "approval_unavailable"})
        pipeline = await session.action_pipeline()
        try:
            if record.subject == "plan":
                await manager.plans.decide(record, body.decision, session, actor, body.note)
                return document(get_record(approval_id))
            if body.decision == "revoke":
                await pipeline.revoke_approval(record.subjectId, role, actor, body.note)
            else:
                expected = {"approve": "approved", "deny": "denied"}[body.decision]
                if record.status not in ("pending", expected):
                    raise ApprovalConflict("This approval has already been processed")
                await pipeline.approve(record.subjectId, body.decision, role, actor, body.note)
                if get_record(approval_id).status != expected:
                    raise ApprovalConflict("Another decision or session change was processed first")
        except ApprovalConflict as exc:
            raise HTTPException(409, {"code": "approval_conflict", "message": str(exc)}) from exc
        return document(get_record(approval_id))

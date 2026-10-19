"""Human decisions over generic approval subjects; invocation is the first consumer."""

from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.app.info_routes import actor_from_headers
from packages.info.approvals import ApprovalConflict, Subject


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny", "revoke"]
    note: str = Field(default="", max_length=4000)


def install_approval_routes(app, manager, role_for):
    store = manager.approvals

    def document(record):
        session = manager.sessions.get(record.sessionId)
        invocation = (
            session.pipeline.table.get(record.subjectId)
            if session and session.pipeline and record.subject == "invocation"
            else None
        )
        available = bool(session and session.state in ("RUNNING", "PAUSED") and session.source)
        return {
            **record.model_dump(mode="json"),
            "canRevoke": available
            and (
                record.status == "denied"
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
        record = store.records.get(approval_id)
        if record is None:
            raise HTTPException(404, {"code": "unknown_approval"})
        return record

    @app.get("/api/v1/approvals")
    async def approvals(
        status: str | None = None,
        subject: Subject | None = None,
        sessionId: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
    ):
        records = sorted(
            store.records.values(),
            key=lambda record: (record.status == "pending", record.createdAtMs),
            reverse=True,
        )
        return {
            "approvals": [
                document(record)
                for record in records
                if (
                    (status is None or record.status == status)
                    and (subject is None or record.subject == subject)
                    and (sessionId is None or record.sessionId == sessionId)
                )
            ][:limit],
            "pendingCount": sum(record.status == "pending" for record in records),
        }

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
        session = manager.sessions.get(record.sessionId)
        if record.subject != "invocation" or not session or session.state not in ("RUNNING", "PAUSED"):
            raise HTTPException(409, {"code": "approval_unavailable"})
        pipeline = await session.action_pipeline()
        try:
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

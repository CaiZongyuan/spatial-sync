"""Read-only attention snapshots and persistent cursor continuation."""

from fastapi import HTTPException

from apps.api.app.information_targets import alert_documents, information_targets
from packages.info.inbox import InboxUnavailable, InvalidCursor, selected_kinds


def install_inbox_routes(app, manager):
    @app.get("/api/v1/inbox")
    async def inbox(after: str | None = None, kinds: str | None = None):
        try:
            selected = selected_kinds(kinds)
        except ValueError as error:
            raise HTTPException(422, {"code": "invalid_inbox_kinds", "message": str(error)}) from error
        alerts = [
            value | {"sessionId": session.session_id}
            for session in information_targets(manager)
            if session.alerts
            for value in alert_documents(session)
            if value["active"]
        ]
        try:
            return await manager.inbox.poll(after, selected, alerts)
        except InvalidCursor as error:
            raise HTTPException(422, {"code": "invalid_inbox_cursor", "message": str(error)}) from error
        except InboxUnavailable as error:
            raise HTTPException(503, {"code": "inbox_unavailable", "message": str(error)}) from error

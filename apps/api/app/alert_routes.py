"""Session-owned alert episodes exposed through one global list and acknowledgement interface."""

import time
from typing import Literal

from fastapi import HTTPException, Query, Request
from pydantic import Field, StrictStr

from apps.api.app.info_routes import actor_from_headers
from apps.api.app.information_targets import alert_documents, information_target, information_targets
from packages.info.alerts import AlertUnavailable
from packages.protocol.schema import Model


class AlertAcknowledgement(Model):
    note: StrictStr = Field(default="", max_length=4000)


def install_alert_routes(app, manager, role_for):
    @app.get("/api/v1/alerts")
    async def alerts(
        sessionId: str | None = None,
        state: Literal["raised", "acknowledged", "cleared"] | None = None,
        severity: Literal["info", "warning", "critical"] | None = None,
        active: bool | None = None,
        limit: int = Query(100, ge=1, le=1000),
    ):
        sessions = ([information_target(manager, sessionId) or manager.get(sessionId)] if sessionId
                    else information_targets(manager))
        documents = [
            value | {"sessionId": session.session_id,
                     "readOnly": bool(getattr(session, "evaluation_id", None))}
            for session in sessions
            if session.alerts
            for value in alert_documents(session)
            if (state is None or value["state"] == state)
            and (severity is None or value["severity"] == severity)
            and (active is None or value["active"] is active)
        ]
        documents.sort(key=lambda value: (value["sampleTimeMs"], value["alertId"]), reverse=True)
        return {"alerts": documents[:limit]}

    @app.post("/api/v1/alerts/{alert_id}/ack")
    async def acknowledge(alert_id: str, body: AlertAcknowledgement, request: Request):
        if role_for(request.headers) != "operator":
            raise HTTPException(403, {"code": "not_permitted"})
        actor = actor_from_headers(request.headers)
        session = next(
            (
                session
                for session in information_targets(manager)
                if session.alerts and session.alerts.get(alert_id)
            ),
            None,
        )
        if session is None:
            raise HTTPException(404, {"code": "unknown_alert"})
        simulation = manager.sessions.get(session.session_id)
        if simulation is not None:
            simulation.require_interactive()
        async with session.command_lock:
            if session.state not in ("RUNNING", "PAUSED"):
                raise HTTPException(409, {"code": "alert_unavailable"})
            try:
                document, messages = session.alerts.acknowledge(
                    alert_id, actor, body.note,
                    getattr(session, "raw_sample_time_ms", lambda: time.monotonic() * 1000)(),
                )
            except AlertUnavailable as error:
                raise HTTPException(409, {"code": "alert_unavailable", "message": str(error)}) from error
            for message in messages:
                session.broadcast_info(message)
            project = getattr(session, "public_alert_document", lambda value: value)
            return project(document) | {"sessionId": session.session_id, "readOnly": False}
